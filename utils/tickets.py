import json
from datetime import datetime, timezone

import discord

from utils.core import format_datetime, load_config
from utils.database import get_db
from utils.helpers import log_action, notify_warrant_issued
from utils.leo_case import allocate_case_number, format_case_number
from utils.leo_pdf import pdf_file, warrant_pdf_bytes
from utils.users import ensure_user, set_license_suspended


async def count_unpaid_tickets(user_id: int) -> int:
    db = await get_db()
    row = await db.execute_fetchone(
        "SELECT COUNT(*) AS cnt FROM citation_tickets WHERE user_id = ? AND payment_status = 'unpaid'",
        (user_id,),
    )
    return row["cnt"] if row else 0


async def enforce_ticket_thresholds(bot: discord.Client, guild: discord.Guild, user_id: int) -> None:
    cfg = load_config().get("citation_tickets", {})
    suspend_at = cfg.get("unpaid_suspend_threshold", 5)
    warrant_at = cfg.get("unpaid_warrant_threshold", 7)
    unpaid = await count_unpaid_tickets(user_id)

    if unpaid >= suspend_at:
        await set_license_suspended(
            user_id,
            True,
            notify_client=bot,
            guild=guild,
            reason=f"You have {unpaid} unpaid citation ticket(s). Pay all tickets to restore your license.",
        )

    if unpaid >= warrant_at:
        db = await get_db()
        existing = await db.execute_fetchone(
            """
            SELECT id FROM warrants
            WHERE user_id = ? AND active = 1 AND reason LIKE 'Automatic warrant%'
            """,
            (user_id,),
        )
        if not existing:
            reason = f"Automatic warrant: {unpaid} unpaid tickets"
            case_no = await allocate_case_number()
            form = {
                "warrant_type": "Arrest",
                "charge_class": "Misdemeanor",
                "jurisdiction": "County",
                "county": "WYTHE",
                "code_description": reason,
                "issued_datetime": format_datetime(datetime.now(timezone.utc)),
            }
            await db.execute(
                """
                INSERT INTO warrants (user_id, reason, issued_by, active, status, form_json, case_number)
                VALUES (?, ?, 0, 1, 'active', ?, ?)
                """,
                (user_id, reason, json.dumps(form), case_no),
            )
            await db.commit()
            await log_action(
                bot,
                "warrant",
                0,
                target_id=user_id,
                details={"automatic": True, "unpaid_tickets": unpaid, "case_number": case_no},
                channel_key="warrants",
            )
            await notify_warrant_issued(
                bot,
                user_id,
                reason=reason,
                automatic=True,
                guild=guild,
            )
            channel_id = load_config().get("channels", {}).get("warrants")
            if channel_id and guild:
                ch = guild.get_channel(channel_id)
                if ch:
                    member = guild.get_member(user_id)
                    pdf = warrant_pdf_bytes(
                        form,
                        case_no=case_no,
                        subject_label=str(member) if member else str(user_id),
                        reason=reason,
                    )
                    await ch.send(
                        content=f"Automatic warrant **#{format_case_number(case_no)}**",
                        file=pdf_file(pdf, f"warrant-{format_case_number(case_no)}.pdf"),
                    )


async def check_registration_allowed(user_id: int) -> tuple[bool, str]:
    db = await get_db()
    await ensure_user(db, user_id)
    row = await db.execute_fetchone(
        "SELECT registration_suspended, license_suspended, license_expires_at FROM users WHERE user_id = ?",
        (user_id,),
    )
    if row and row["registration_suspended"]:
        unpaid = await count_unpaid_tickets(user_id)
        return False, f"Registration suspended. {unpaid} unpaid ticket(s)."
    if row and row["license_suspended"]:
        unpaid = await count_unpaid_tickets(user_id)
        return False, f"License suspended. {unpaid} unpaid ticket(s). Pay all tickets to restore."
    from utils.users import license_is_valid

    if row and not license_is_valid(row):
        return False, "You need a valid driver's license. Buy one in the shop with `/shop`."
    return True, ""
