"""Server soft-ban: strip roles, assign banned role, restore on unban."""

from __future__ import annotations

import json
import logging

import discord

from utils.core import load_config
from utils.database import get_db

log = logging.getLogger("vsrp_bot.soft_ban")

DEFAULT_BANNED_ROLE_ID = 1553820949357535363
DEFAULT_BANNED_CHANNEL_ID = 1250622854110773291
DEFAULT_SOFTBAN_NOTIFY_CHANNEL_ID = 1553820901781536881
DEFAULT_UNBAN_RESTORE_ROLE_IDS = (
    1244718289616244789,
    1514256466398154752,
    1250612203850170390,
)

SOFTBAN_CASE_ACTIONS = ("softban", "ban", "modban")


def banned_role_id() -> int:
    raw = load_config().get("moderation", {}).get("banned_role_id") or DEFAULT_BANNED_ROLE_ID
    return int(raw)


def banned_channel_id() -> int:
    mod = load_config().get("moderation", {})
    raw = mod.get("banned_channel_id") or load_config().get("channels", {}).get("tickets") or DEFAULT_BANNED_CHANNEL_ID
    return int(raw)


def unban_restore_role_ids() -> list[int]:
    cfg = load_config().get("moderation", {}).get("unban_restore_role_ids")
    if cfg:
        return [int(r) for r in cfg]
    return list(DEFAULT_UNBAN_RESTORE_ROLE_IDS)


def _pending_unban_state_key(guild_id: int, user_id: int) -> str:
    return f"unban_restore:{guild_id}:{user_id}"


async def mark_pending_unban_roles(guild_id: int, user_id: int) -> None:
    from utils.core import set_state

    await set_state(_pending_unban_state_key(guild_id, user_id), "1")


async def consume_pending_unban_roles(member: discord.Member) -> bool:
    from utils.core import get_state, set_state

    key = _pending_unban_state_key(member.guild.id, member.id)
    if not await get_state(key):
        return False
    await set_state(key, "")
    await apply_unban_roles(member, reason="Unban restore on rejoin")
    return True


async def apply_unban_roles(member: discord.Member, *, reason: str = "Ban lifted") -> list[discord.Role]:
    guild = member.guild
    me = guild.me
    if not me:
        raise discord.ClientException("Bot member not available for role assignment.")

    banned = _banned_role(guild)
    if banned and banned in member.roles:
        try:
            await member.remove_roles(banned, reason=reason)
        except discord.HTTPException as exc:
            log.warning("Could not remove banned role from %s: %s", member.id, exc)
            raise

    assigned: list[discord.Role] = []
    for role_id in unban_restore_role_ids():
        role = guild.get_role(role_id)
        if not role:
            log.warning("Unban restore role %s not found in guild %s", role_id, guild.id)
            continue
        if role >= me.top_role:
            log.warning(
                "Cannot assign %s (%s): move the bot role above it in Server Settings → Roles",
                role.name,
                role_id,
            )
            continue
        if role in member.roles:
            assigned.append(role)
            continue
        try:
            await member.add_roles(role, reason=reason)
            assigned.append(role)
        except discord.HTTPException as exc:
            log.warning("Failed to add role %s to %s: %s", role_id, member.id, exc)

    if not assigned and unban_restore_role_ids():
        raise discord.Forbidden(
            None,
            "No unban roles could be assigned. Move the bot role above the member, banned, and unban roles.",
        )
    return assigned


def softban_notify_channel_id() -> int:
    raw = load_config().get("moderation", {}).get("softban_notify_channel_id") or DEFAULT_SOFTBAN_NOTIFY_CHANNEL_ID
    return int(raw)


def banned_visible_channel_ids() -> list[int]:
    seen: set[int] = set()
    ordered: list[int] = []
    for channel_id in (softban_notify_channel_id(), banned_channel_id()):
        if channel_id and channel_id not in seen:
            seen.add(channel_id)
            ordered.append(channel_id)
    return ordered


async def count_prior_softban_cases(guild_id: int, user_id: int) -> int:
    db = await get_db()
    row = await db.execute_fetchone(
        f"""
        SELECT COUNT(*) AS c FROM mod_cases
        WHERE guild_id = ? AND user_id = ?
          AND action_type IN ({",".join("?" * len(SOFTBAN_CASE_ACTIONS))})
        """,
        (guild_id, user_id, *SOFTBAN_CASE_ACTIONS),
    )
    return int(row["c"]) if row else 0


async def deactivate_soft_ban_record(guild_id: int, user_id: int) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE soft_bans SET active = 0, lifted_at = NOW() WHERE guild_id = ? AND user_id = ? AND active = 1",
        (guild_id, user_id),
    )
    await db.commit()


def _banned_role(guild: discord.Guild) -> discord.Role | None:
    role_id = banned_role_id()
    return guild.get_role(role_id) if role_id else None


async def is_soft_banned(guild_id: int, user_id: int) -> bool:
    db = await get_db()
    row = await db.execute_fetchone(
        "SELECT 1 FROM soft_bans WHERE guild_id = ? AND user_id = ? AND active = 1",
        (guild_id, user_id),
    )
    return row is not None


async def apply_soft_ban(
    member: discord.Member,
    *,
    moderator_id: int,
    case_id: int | None = None,
    reason: str = "Soft ban",
) -> tuple[bool, str]:
    role = _banned_role(member.guild)
    if not role:
        return False, "Banned role is not configured or not found in this server."

    if role in member.roles and await is_soft_banned(member.guild.id, member.id):
        return False, f"{member.mention} is already soft-banned."

    saved = [r.id for r in member.roles if r != member.guild.default_role and r.id != role.id]

    try:
        await member.edit(roles=[role], reason=f"{reason} (case #{case_id})" if case_id else reason)
    except discord.Forbidden:
        return False, "I cannot change this member's roles (check role hierarchy)."
    except discord.HTTPException as exc:
        return False, f"Failed to apply banned role: {exc}"

    db = await get_db()
    await db.execute(
        """
        INSERT INTO soft_bans (guild_id, user_id, saved_roles_json, active, case_id, moderator_id)
        VALUES (?, ?, ?, 1, ?, ?)
        ON CONFLICT (guild_id, user_id) DO UPDATE SET
            saved_roles_json = EXCLUDED.saved_roles_json,
            active = 1,
            case_id = EXCLUDED.case_id,
            moderator_id = EXCLUDED.moderator_id,
            lifted_at = NULL
        """,
        (member.guild.id, member.id, json.dumps(saved), case_id, moderator_id),
    )
    await db.commit()
    return True, f"Soft-banned {member.mention}. They can only access the ban appeal channel."


async def remove_soft_ban(
    guild: discord.Guild,
    user_id: int,
    *,
    reason: str = "Ban lifted",
) -> tuple[bool, str]:
    member = guild.get_member(user_id)
    banned = _banned_role(guild)
    if not banned:
        return False, "Banned role not configured."

    soft_active = await is_soft_banned(guild.id, user_id)
    has_banned_role = member is not None and banned in member.roles
    if not soft_active and not has_banned_role:
        return False, "That user is not soft-banned."

    # Clear DB before changing roles so on_member_update does not re-strip them.
    await deactivate_soft_ban_record(guild.id, user_id)

    if not member:
        await mark_pending_unban_roles(guild.id, user_id)
        return True, f"Soft-ban cleared for `{user_id}` (not in server; roles will restore when they rejoin)."

    try:
        assigned = await apply_unban_roles(member, reason=reason)
    except discord.Forbidden:
        return False, "Cannot restore roles — move the bot role above the member/banned/unban roles."
    except discord.HTTPException as exc:
        return False, f"Failed to remove soft ban: {exc}"

    role_names = ", ".join(r.name for r in assigned) or "none (check hierarchy)"
    return True, f"Removed soft-ban from {member.mention} and assigned: {role_names}."


async def full_unban(
    guild: discord.Guild,
    user_id: int,
    *,
    reason: str = "Ban lifted",
) -> tuple[bool, str]:
    """Lift soft-ban or Discord hard-ban and apply standard unban roles when possible."""
    if await is_soft_banned(guild.id, user_id):
        ok, msg = await remove_soft_ban(guild, user_id, reason=reason)
        return ok, msg

    try:
        ban_entry = await guild.fetch_ban(discord.Object(id=user_id))
    except discord.NotFound:
        member = guild.get_member(user_id)
        banned = _banned_role(guild)
        if member and banned and banned in member.roles:
            return await remove_soft_ban(guild, user_id, reason=reason)
        if await is_soft_banned(guild.id, user_id):
            return await remove_soft_ban(guild, user_id, reason=reason)
        return False, "That user is not soft-banned or Discord-banned."

    try:
        await guild.unban(ban_entry.user, reason=reason)
    except discord.HTTPException as exc:
        return False, f"Failed to Discord-unban: {exc}"

    await deactivate_soft_ban_record(guild.id, user_id)
    member = guild.get_member(user_id)
    if member:
        try:
            assigned = await apply_unban_roles(member, reason=reason)
        except discord.HTTPException as exc:
            return False, f"Unbanned from Discord but could not assign roles: {exc}"
        names = ", ".join(r.name for r in assigned) or "none (check hierarchy)"
        return True, f"Hard-unbanned {member.mention} and assigned: {names}."

    await mark_pending_unban_roles(guild.id, user_id)
    return True, f"Hard-unbanned `{ban_entry.user}`. Unban roles will apply when they rejoin."


async def enforce_soft_ban_on_join(member: discord.Member) -> None:
    if await consume_pending_unban_roles(member):
        return
    if not await is_soft_banned(member.guild.id, member.id):
        return
    banned = _banned_role(member.guild)
    if not banned:
        return
    allowed_ids = {member.guild.default_role.id, banned.id}
    if all(r.id in allowed_ids for r in member.roles) and banned in member.roles:
        return
    try:
        await member.edit(roles=[banned], reason="Soft ban: rejoin — banned role only")
    except discord.HTTPException:
        log.warning("Could not enforce soft ban on join for %s", member.id)


async def enforce_soft_ban_roles(member: discord.Member) -> bool:
    """Strip extra roles if a soft-banned member received new ones."""
    if not await is_soft_banned(member.guild.id, member.id):
        return False
    banned = _banned_role(member.guild)
    if not banned:
        return False

    allowed_ids = {member.guild.default_role.id, banned.id}
    extra = [r for r in member.roles if r.id not in allowed_ids]
    if not extra:
        if banned not in member.roles:
            try:
                await member.add_roles(banned, reason="Soft ban: restore banned role")
            except discord.HTTPException:
                pass
        return False

    try:
        await member.edit(roles=[banned], reason="Soft ban: removed unauthorized roles")
        return True
    except discord.HTTPException:
        log.warning("Could not enforce soft ban roles for %s", member.id)
        return False


async def setup_banned_role_permissions(guild: discord.Guild) -> tuple[int, list[int]]:
    """Channel overwrites: banned role sees only configured appeal / notice channels."""
    role = _banned_role(guild)
    allow_ids = banned_visible_channel_ids()
    if not role:
        raise ValueError("Banned role not found.")
    if not allow_ids:
        raise ValueError("No banned visible channels configured.")

    for channel_id in allow_ids:
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.abc.GuildChannel):
            raise ValueError(f"Channel {channel_id} not found.")

    allow_set = set(allow_ids)
    updated = 0
    for channel in guild.channels:
        if not isinstance(
            channel,
            (discord.TextChannel, discord.VoiceChannel, discord.ForumChannel, discord.StageChannel),
        ):
            continue
        try:
            if channel.id in allow_set:
                await channel.set_permissions(
                    role,
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    attach_files=True,
                    embed_links=True,
                    add_reactions=False,
                    reason="Soft ban setup: banned member channel access",
                )
            else:
                await channel.set_permissions(
                    role,
                    view_channel=False,
                    send_messages=False,
                    read_message_history=False,
                    reason="Soft ban setup: hide channel from banned members",
                )
            updated += 1
        except discord.Forbidden:
            log.warning("Missing permission to set overwrite on channel %s", channel.id)
        except discord.HTTPException as exc:
            log.warning("Overwrite failed for channel %s: %s", channel.id, exc)

    return updated, allow_ids
