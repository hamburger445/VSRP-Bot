"""Save citations/warrants and post them to Discord (used by slash commands and the web forms)."""

from __future__ import annotations

import json
import logging

import discord

from utils.core import load_config
from utils.database import DatabaseError, DatabaseTimeoutError, get_db
from utils.helpers import log_action, notify_citation_issued, notify_warrant_issued
from utils.leo_case import allocate_case_number, format_case_number
from utils.leo_forms import (
    build_citation_embed,
    build_served_warrant_embed,
    build_warrant_embed,
    citation_summary,
    parse_form_json,
    warrant_form_to_reason,
)
from utils.leo_pdf import citation_pdf_bytes, pdf_file, served_warrant_pdf_bytes, warrant_pdf_bytes
from utils.tickets import enforce_ticket_thresholds

log = logging.getLogger("wcrp_bot.leo_submit")


async def _post_record(
    guild: discord.Guild | None,
    officer: discord.abc.User,
    embed: discord.Embed,
    pdf: bytes,
    filename: str,
    *,
    channel_key: str,
    review_id: int = 0,
    bot: discord.Client | None = None,
) -> None:
    from utils.form_discord import channel_id_for, post_channel_message, review_components

    view = None
    if review_id:
        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(label="Approve", style=discord.ButtonStyle.success, custom_id=f"wcrpdoc:approve:{review_id}"))
        view.add_item(discord.ui.Button(label="Reject", style=discord.ButtonStyle.danger, custom_id=f"wcrpdoc:reject:{review_id}"))
    posted = False
    channel_id = load_config().get("channels", {}).get(channel_key)
    if channel_id and guild:
        ch = guild.get_channel(channel_id)
        if ch:
            try:
                kwargs = {"embed": embed, "file": pdf_file(pdf, filename)}
                if view:
                    kwargs["view"] = view
                await ch.send(**kwargs)
                posted = True
            except discord.HTTPException:
                log.exception("Could not post %s to channel %s", channel_key, channel_id)
    if not posted:
        cid = channel_id_for(channel_key)
        if cid:
            posted = bool(
                await post_channel_message(
                    cid,
                    embed=embed.to_dict(),
                    filename=filename,
                    file_bytes=pdf,
                    components=review_components(review_id) if review_id else None,
                )
            )
    if posted:
        return
    if bot is None:
        return
    try:
        await officer.send(embed=embed, file=pdf_file(pdf, filename))
    except discord.HTTPException:
        log.warning("Could not DM officer %s the %s PDF", getattr(officer, "id", None), channel_key)


def _guild_member(bot: discord.Client, guild: discord.Guild | None, user_id: int) -> discord.abc.User | None:
    if guild:
        m = guild.get_member(user_id)
        if m:
            return m
    return bot.get_user(user_id)


async def submit_citation(
    bot: discord.Client | None,
    *,
    guild: discord.Guild | None,
    officer: discord.abc.User,
    subject: discord.abc.User,
    form_data: dict,
    fine: int,
    jti: str = "",
    guild_id: int = 0,
) -> dict:
    from utils.leo_records import create_citation

    violation = citation_summary(form_data)
    row = await create_citation(
        subject_id=int(subject.id),
        issued_by=int(officer.id),
        form_data=form_data,
        fine=fine,
        violation=violation,
    )
    ticket_id = row.get("id") or 0
    case_no = row.get("case_number") or await allocate_case_number()
    embed = build_citation_embed(
        form_data,
        case_no=case_no,
        subject=subject if isinstance(subject, discord.Member) else None,
        fine=fine,
        footer=f"Issued by {officer.display_name} · Ticket #{ticket_id}",
    )
    pdf = citation_pdf_bytes(
        form_data,
        case_no=case_no,
        fine=fine,
        subject_label=str(subject),
        issuer_label=officer.display_name,
    )
    filename = f"summons-{format_case_number(case_no)}.pdf"
    if bot is not None:
        await notify_citation_issued(
            bot,
            subject.id,
            ticket_id=ticket_id,
            violation=violation,
            fine=fine,
            issued_by=officer.display_name,
            guild=guild,
        )
        if guild:
            await enforce_ticket_thresholds(bot, guild, subject.id)
        await log_action(
            bot,
            "citation_ticket",
            officer.id,
            target_id=subject.id,
            details={"case_number": case_no, "violation": violation, "fine": fine},
            channel_key="tickets",
        )
    await _post_record(guild, officer, embed, pdf, filename, channel_key="citations", review_id=0, bot=bot)
    return {
        "case_number": format_case_number(case_no),
        "ticket_id": ticket_id,
        "pdf": pdf,
        "filename": filename,
        "embed": embed,
    }


async def submit_warrant(
    bot: discord.Client | None,
    *,
    guild: discord.Guild | None,
    officer: discord.abc.User,
    subject: discord.abc.User,
    form_data: dict,
    jti: str = "",
    guild_id: int = 0,
) -> dict:
    from utils.leo_records import create_warrant

    form_data.setdefault("county", "WYTHE")
    form_data.setdefault("issued_by", officer.display_name)
    reason = warrant_form_to_reason(form_data)
    row = await create_warrant(
        subject_id=int(subject.id),
        issued_by=int(officer.id),
        form_data=form_data,
        reason=reason,
    )
    warrant_id = row.get("id") or 0
    case_no = row.get("case_number") or await allocate_case_number()
    embed = build_warrant_embed(
        form_data,
        case_no=case_no,
        subject=subject if isinstance(subject, discord.Member) else None,
        footer=f"Issued by {officer.display_name} · Warrant #{warrant_id}",
    )
    pdf = warrant_pdf_bytes(
        form_data,
        case_no=case_no,
        subject_label=str(subject),
        issuer_label=officer.display_name,
        reason=reason,
    )
    filename = f"warrant-{format_case_number(case_no)}.pdf"
    if bot is not None:
        await notify_warrant_issued(
            bot,
            subject.id,
            reason=reason,
            issued_by=officer.display_name,
            guild=guild,
        )
        await log_action(
            bot,
            "warrant",
            officer.id,
            target_id=subject.id,
            details={"case_number": case_no, "reason": reason},
        )
    from utils.form_store import create_review

    review_id = await create_review(
        kind="warrant",
        case_number=format_case_number(case_no),
        officer_id=int(officer.id),
        subject_id=int(subject.id),
        guild_id=int(guild.id if guild else guild_id or 0),
        jti=jti or f"war-{warrant_id}",
        record_id=warrant_id,
    )
    await _post_record(guild, officer, embed, pdf, filename, channel_key="warrants", review_id=review_id, bot=bot)
    return {
        "case_number": format_case_number(case_no),
        "warrant_id": warrant_id,
        "pdf": pdf,
        "filename": filename,
        "embed": embed,
        "review_id": review_id,
    }


async def submit_serve_warrant(
    bot: discord.Client | None,
    *,
    guild: discord.Guild | None,
    officer: discord.abc.User,
    warrant_id: int,
    serve_data: dict,
    jti: str = "",
    guild_id: int = 0,
) -> dict:
    db = await get_db()
    warrant_row = await db.execute_fetchone("SELECT * FROM warrants WHERE id = ?", (warrant_id,))
    if not warrant_row:
        raise ValueError("Warrant not found")
    form_data = parse_form_json(warrant_row.get("form_json"))
    case_no = warrant_row.get("case_number") or str(warrant_id)
    result = await db.execute(
        """
        UPDATE warrants
        SET active = 0, status = 'served', cleared_at = NOW(),
            served_at = NOW(), served_by = ?, serve_form_json = ?
        WHERE id = ? AND active = 1
        """,
        (int(officer.id), json.dumps(serve_data), warrant_id),
    )
    await db.commit()
    if result.rowcount == 0:
        raise ValueError("This warrant has already been served")
    user_id = int(warrant_row["user_id"])
    member = _guild_member(bot, guild, user_id) if bot is not None else None
    embed = build_served_warrant_embed(
        form_data,
        serve_data,
        case_no=case_no,
        subject=member if isinstance(member, discord.Member) else None,
    )
    pdf = served_warrant_pdf_bytes(
        form_data,
        serve_data,
        case_no=case_no,
        subject_label=str(member) if member else str(user_id),
    )
    filename = f"served-warrant-{format_case_number(case_no)}.pdf"
    from utils.form_store import create_review

    review_id = await create_review(
        kind="serve",
        case_number=format_case_number(case_no),
        officer_id=int(officer.id),
        subject_id=user_id,
        guild_id=int(guild.id if guild else guild_id or 0),
        jti=jti or f"serve-{warrant_id}",
        record_id=warrant_id,
    )
    await _post_record(guild, officer, embed, pdf, filename, channel_key="warrants", review_id=review_id, bot=bot)
    if bot is not None:
        await log_action(
            bot,
            "warrant_served",
            officer.id,
            target_id=user_id,
            details={"warrant_id": warrant_id, "case_number": case_no},
        )
    return {
        "case_number": format_case_number(case_no),
        "warrant_id": warrant_id,
        "pdf": pdf,
        "filename": filename,
        "embed": embed,
        "review_id": review_id,
    }
