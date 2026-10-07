"""Shared helpers: audit logging, emojis, notifications, departments."""

import json
import logging
from typing import Any

import discord

from utils.core import load_config
from utils.database import get_db

log = logging.getLogger("vsrp_bot.helpers")

# --- departments ---

_DEFAULT_NAMES = {
    "fire1": "Wytheville Fire & Rescue",
    "fire2": "Rural Retreat Volunteer Fire Department",
    "fire3": "Max Meadows Volunteer Fire Department",
    "police1": "Wythe County Sheriffs Office",
    "police2": "Wytheville Police Department",
    "ems": "EMS",
    "dot": "DOT",
    "dispatch": "Dispatch",
}


def department_display_name(key: str) -> str:
    names = load_config().get("roles", {}).get("department_names", {})
    return names.get(key) or _DEFAULT_NAMES.get(key, key.replace("_", " ").title())


# --- emojis ---


def get_emoji(key: str) -> str | None:
    value = load_config().get("emojis", {}).get(key)
    if value and str(value).strip():
        return str(value).strip()
    return None


def button_kwargs(key: str | None = None) -> dict:
    if not key:
        return {}
    emoji = get_emoji(key)
    return {"emoji": emoji} if emoji else {}


# --- audit ---

LOG_CHANNEL_MAP = {
    "vehicle_registration": "bot",
    "trailer_registration": "bot",
    "citation_ticket": "bot",
    "ticket_payment": "bot",
    "warrant": "bot",
    "warrant_removal": "bot",
    "economy": "economy",
    "shop": "bot",
    "admin": "bot",
    "owner": "bot",
    "support_ticket": "support",
    "mod_ban": "bot",
    "mod_kick": "bot",
    "mod_mute": "bot",
    "mod_unmute": "bot",
    "mod_unban": "bot",
    "mod_warn": "bot",
    "mod_clear": "bot",
    "mod_purge": "bot",
    "mod_strike": "bot",
    "mod_ban_record": "bot",
    "mod_role": "bot",
}

CHANNEL_KEY_ALIASES = {
    "vehicles": "bot",
    "trailers": "bot",
    "tickets": "bot",
    "warrants": "bot",
    "shop": "bot",
    "admin": "bot",
    "owner": "bot",
    "moderation": "bot",
}


def _resolve_log_channel(channel_key: str | None, action_type: str) -> int | None:
    cfg = load_config().get("channels", {})
    logs = cfg.get("logs", {})

    if channel_key == "support":
        return cfg.get("ticket_logs") or logs.get("support")

    key = channel_key or LOG_CHANNEL_MAP.get(action_type, "bot")
    key = CHANNEL_KEY_ALIASES.get(key, key)
    if key == "economy":
        return logs.get("economy")
    if key == "support":
        return cfg.get("ticket_logs") or logs.get("support")
    return logs.get("bot")


async def log_action(
    bot: discord.Client,
    action_type: str,
    actor_id: int,
    *,
    target_id: int | None = None,
    details: dict[str, Any] | str | None = None,
    channel_key: str | None = None,
) -> None:
    db = await get_db()
    detail_str = json.dumps(details) if isinstance(details, dict) else (details or "")
    await db.execute(
        """
        INSERT INTO audit_logs (action_type, actor_id, target_id, details)
        VALUES (?, ?, ?, ?)
        """,
        (action_type, actor_id, target_id, detail_str),
    )
    await db.commit()

    channel_id = _resolve_log_channel(channel_key, action_type)
    if not channel_id:
        return

    channel = bot.get_channel(channel_id)
    if not channel:
        return

    embed = discord.Embed(title=f"Audit: {action_type.replace('_', ' ').title()}", color=0x2B2D31)
    embed.add_field(name="Actor", value=f"<@{actor_id}> (`{actor_id}`)", inline=True)
    if target_id:
        embed.add_field(name="Target", value=f"<@{target_id}> (`{target_id}`)", inline=True)
    if detail_str:
        embed.add_field(name="Details", value=detail_str[:1024], inline=False)

    try:
        await channel.send(embed=embed)
    except discord.HTTPException:
        log.warning("Failed to send audit log to channel %s", channel_id)


# --- notifications ---


async def notify_user(
    client: discord.Client,
    user_id: int,
    *,
    title: str,
    description: str,
    guild: discord.Guild | None = None,
    color: int = 0x2B2D31,
) -> bool:
    user: discord.abc.Messageable | None = None
    if guild:
        user = guild.get_member(user_id)
    if user is None:
        try:
            user = await client.fetch_user(user_id)
        except discord.HTTPException:
            log.debug("Could not resolve user %s for notification", user_id)
            return False
    embed = discord.Embed(title=title, description=description, color=color)
    try:
        await user.send(embed=embed)
        return True
    except discord.HTTPException:
        log.debug("Could not DM user %s", user_id)
        return False


async def notify_citation_issued(
    client: discord.Client,
    user_id: int,
    *,
    ticket_id: int,
    violation: str,
    fine: int,
    issued_by: str,
    guild: discord.Guild | None = None,
) -> None:
    await notify_user(
        client,
        user_id,
        title="New Citation Ticket",
        description=(
            f"A citation has been issued on your record.\n\n"
            f"**Ticket ID:** #{ticket_id}\n"
            f"**Violation:** {violation}\n"
            f"**Fine:** ${fine:,}\n"
            f"**Issued by:** {issued_by}\n\n"
            "Use `/payticket` to pay outstanding fines."
        ),
        guild=guild,
        color=0xE67E22,
    )


async def notify_ticket_paid(
    client: discord.Client,
    user_id: int,
    *,
    ticket_id: int,
    fine: int,
    registration_restored: bool = False,
    guild: discord.Guild | None = None,
) -> None:
    extra = "\n\nYour driver's license suspension has been lifted." if registration_restored else ""
    await notify_user(
        client,
        user_id,
        title="Citation Ticket Paid",
        description=f"Ticket #{ticket_id} has been marked as paid.\n**Amount paid:** ${fine:,}{extra}",
        guild=guild,
        color=0x27AE60,
    )


async def notify_warrant_issued(
    client: discord.Client,
    user_id: int,
    *,
    reason: str,
    issued_by: str | None = None,
    automatic: bool = False,
    guild: discord.Guild | None = None,
) -> None:
    issuer_line = "System (automatic)" if automatic else f"**Issued by:** {issued_by}"
    await notify_user(
        client,
        user_id,
        title="Warrant Issued",
        description=(
            f"A warrant has been issued on your record.\n\n"
            f"**Reason:** {reason}\n"
            f"{issuer_line}\n\n"
            "Contact law enforcement or staff for details."
        ),
        guild=guild,
        color=0x992D22,
    )


async def notify_warrant_cleared(
    client: discord.Client,
    user_id: int,
    *,
    warrant_id: int,
    reason: str,
    cleared_by: str,
    guild: discord.Guild | None = None,
) -> None:
    await notify_user(
        client,
        user_id,
        title="Warrant Cleared",
        description=(
            f"Warrant #{warrant_id} has been cleared from your record.\n\n"
            f"**Original reason:** {reason}\n"
            f"**Cleared by:** {cleared_by}"
        ),
        guild=guild,
        color=0x27AE60,
    )


async def notify_registration_suspended(
    client: discord.Client,
    user_id: int,
    *,
    reason: str,
    guild: discord.Guild | None = None,
) -> None:
    await notify_user(
        client,
        user_id,
        title="Registration Suspended",
        description=(
            "Your vehicle and trailer registration privileges have been suspended.\n\n"
            f"**Reason:** {reason}"
        ),
        guild=guild,
        color=0xE74C3C,
    )


async def notify_registration_restored(
    client: discord.Client,
    user_id: int,
    *,
    reason: str | None = None,
    guild: discord.Guild | None = None,
) -> None:
    detail = f"\n\n**Reason:** {reason}" if reason else ""
    await notify_user(
        client,
        user_id,
        title="Registration Restored",
        description=f"Your vehicle and trailer registration privileges have been restored.{detail}",
        guild=guild,
        color=0x27AE60,
    )


async def notify_license_suspended(
    client: discord.Client,
    user_id: int,
    *,
    reason: str,
    guild: discord.Guild | None = None,
) -> None:
    await notify_user(
        client,
        user_id,
        title="Driver's License Suspended",
        description=(
            "Your driver's license has been suspended.\n\n"
            f"**Reason:** {reason}\n\n"
            "Pay all outstanding citation tickets to restore your license."
        ),
        guild=guild,
        color=0xE74C3C,
    )


async def notify_license_restored(
    client: discord.Client,
    user_id: int,
    *,
    reason: str | None = None,
    guild: discord.Guild | None = None,
) -> None:
    detail = f"\n\n**Reason:** {reason}" if reason else ""
    await notify_user(
        client,
        user_id,
        title="Driver's License Restored",
        description=f"Your driver's license suspension has been lifted.{detail}",
        guild=guild,
        color=0x27AE60,
    )


async def notify_vehicle_registered(
    client: discord.Client,
    user_id: int,
    *,
    plate: str,
    vehicle: str,
    guild: discord.Guild | None = None,
) -> None:
    await notify_user(
        client,
        user_id,
        title="Vehicle Registered",
        description=(
            f"A vehicle has been added to your profile.\n\n"
            f"**Plate:** {plate}\n"
            f"**Vehicle:** {vehicle}\n"
            f"**Status:** Active"
        ),
        guild=guild,
        color=0x27AE60,
    )


async def notify_trailer_registered(
    client: discord.Client,
    user_id: int,
    *,
    plate: str,
    trailer_type: str,
    guild: discord.Guild | None = None,
) -> None:
    await notify_user(
        client,
        user_id,
        title="Trailer Registered",
        description=(
            f"A trailer has been added to your profile.\n\n"
            f"**Plate:** {plate}\n"
            f"**Type:** {trailer_type}\n"
            f"**Status:** Active"
        ),
        guild=guild,
        color=0x27AE60,
    )


async def notify_account_frozen(
    client: discord.Client,
    user_id: int,
    *,
    by_staff: str,
    guild: discord.Guild | None = None,
) -> None:
    await notify_user(
        client,
        user_id,
        title="Economy Account Frozen",
        description=(
            "Your economy account has been frozen. Wallet transactions are disabled.\n\n"
            f"**Action by:** {by_staff}\n\n"
            "Contact staff if you have questions."
        ),
        guild=guild,
        color=0xE74C3C,
    )


async def notify_account_unfrozen(
    client: discord.Client,
    user_id: int,
    *,
    by_staff: str,
    guild: discord.Guild | None = None,
) -> None:
    await notify_user(
        client,
        user_id,
        title="Economy Account Unfrozen",
        description=(
            "Your economy account has been unfrozen. Wallet transactions are enabled again.\n\n"
            f"**Action by:** {by_staff}"
        ),
        guild=guild,
        color=0x27AE60,
    )


async def notify_balance_updated(
    client: discord.Client,
    user_id: int,
    *,
    field: str,
    amount: int,
    by_staff: str,
    guild: discord.Guild | None = None,
) -> None:
    await notify_user(
        client,
        user_id,
        title="Balance Updated",
        description=(
            f"Your {field} balance has been updated by staff.\n\n"
            f"**New {field.title()}:** ${amount:,}\n"
            f"**Updated by:** {by_staff}"
        ),
        guild=guild,
        color=0x3498DB,
    )


async def notify_wallet_adjustment(
    client: discord.Client,
    user_id: int,
    *,
    amount: int,
    action: str,
    by_staff: str,
    guild: discord.Guild | None = None,
) -> None:
    verb = "added to" if amount > 0 else "removed from"
    await notify_user(
        client,
        user_id,
        title="Wallet Adjustment",
        description=(
            f"${abs(amount):,} has been {verb} your wallet by staff.\n\n"
            f"**Action:** {action}\n"
            f"**By:** {by_staff}"
        ),
        guild=guild,
        color=0x3498DB,
    )
