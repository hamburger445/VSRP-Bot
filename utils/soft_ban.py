"""Server soft-ban: strip roles, assign banned role, restore on unban."""

from __future__ import annotations

import asyncio
import json
import logging

import discord

from utils.core import (
    is_main_guild,
    is_shift_guild,
    load_config,
    main_guild_id,
    shift_guild_ids,
)
from utils.database import get_db

log = logging.getLogger("vsrp_bot.soft_ban")

DEFAULT_BANNED_ROLE_ID = 1553820949357535363
DEFAULT_BANNED_CHANNEL_ID = 1250622854110773291
DEFAULT_SOFTBAN_NOTIFY_CHANNEL_ID = 1553820901781536881
DEFAULT_UNBAN_RESTORE_ROLE_IDS = (
    1244718289616244789,
    1514256466398154752,
    1250612203850170390,
    1513915102938529802,
)
DEFAULT_SOFTBAN_ACCOMPANY_ROLE_IDS = (1513915102938529802,)

SOFTBAN_CASE_ACTIONS = ("softban", "ban", "modban")
SHIFT_BANNED_ROLE_NAME = "Banned"
SHIFT_BANNED_CHANNEL_NAME = "banned-chat"
SHIFT_BANNED_ROLE_COLOR = 0xFF0000
SHIFT_BAN_INFRA_STATE_KEY = "shift_ban_infra_v1"


def soft_ban_record_guild_id() -> int:
    return main_guild_id()


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


def softban_accompany_role_ids() -> list[int]:
    cfg = load_config().get("moderation", {}).get("softban_accompany_role_ids")
    if cfg:
        return [int(r) for r in cfg]
    return list(DEFAULT_SOFTBAN_ACCOMPANY_ROLE_IDS)


def _allowed_softban_role_ids(guild: discord.Guild) -> set[int]:
    allowed = {guild.default_role.id}
    banned = _banned_role(guild)
    if banned:
        allowed.add(banned.id)
    if is_main_guild(guild.id):
        for role_id in softban_accompany_role_ids():
            allowed.add(role_id)
    return allowed


def _softban_roles(guild: discord.Guild) -> list[discord.Role]:
    banned = _banned_role(guild)
    if not banned:
        return []
    if is_shift_guild(guild.id):
        return [banned]
    roles: list[discord.Role] = [banned]
    seen = {banned.id}
    for role_id in softban_accompany_role_ids():
        role = guild.get_role(role_id)
        if role and role.id not in seen:
            seen.add(role.id)
            roles.append(role)
    return roles


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
        (soft_ban_record_guild_id(), user_id),
    )
    await db.commit()


def _shift_banned_role_id_from_state_key(guild_id: int) -> str:
    return f"shift_banned_role_id:{guild_id}"


def _shift_banned_channel_id_from_state_key(guild_id: int) -> str:
    return f"shift_banned_channel_id:{guild_id}"


async def _get_state_id(key: str) -> int | None:
    from utils.core import get_state

    raw = await get_state(key)
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


async def _set_state_id(key: str, value: int) -> None:
    from utils.core import set_state

    await set_state(key, str(value))


def _banned_role(guild: discord.Guild) -> discord.Role | None:
    if is_main_guild(guild.id):
        role_id = banned_role_id()
        return guild.get_role(role_id) if role_id else None
    return discord.utils.get(guild.roles, name=SHIFT_BANNED_ROLE_NAME)


async def is_soft_banned(guild_id: int, user_id: int) -> bool:
    db = await get_db()
    record_guild = soft_ban_record_guild_id()
    row = await db.execute_fetchone(
        "SELECT 1 FROM soft_bans WHERE guild_id = ? AND user_id = ? AND active = 1",
        (record_guild, user_id),
    )
    return row is not None


async def apply_soft_ban_on_shift_member(
    member: discord.Member,
    *,
    reason: str = "Soft ban (main server)",
) -> bool:
    role = _banned_role(member.guild)
    if not role:
        return False
    try:
        await member.edit(roles=[role], reason=reason)
        return True
    except discord.HTTPException:
        log.warning("Could not apply shift soft ban for %s in guild %s", member.id, member.guild.id)
        return False


async def sync_soft_ban_to_shift_guilds(
    bot: discord.Client,
    user_id: int,
    *,
    reason: str,
) -> None:
    for guild_id in shift_guild_ids():
        guild = bot.get_guild(guild_id)
        if not guild:
            continue
        member = guild.get_member(user_id)
        if member:
            await apply_soft_ban_on_shift_member(member, reason=reason)
            await setup_shift_banned_role_permissions(guild)


async def clear_soft_ban_on_shift_guilds(
    bot: discord.Client,
    user_id: int,
    *,
    reason: str = "Ban lifted",
) -> None:
    for guild_id in shift_guild_ids():
        guild = bot.get_guild(guild_id)
        if not guild:
            continue
        member = guild.get_member(user_id)
        if not member:
            continue
        banned = _banned_role(guild)
        if banned and banned in member.roles:
            try:
                await member.edit(roles=[], reason=reason)
            except discord.HTTPException:
                log.warning("Could not clear shift soft ban for %s in %s", user_id, guild_id)


async def apply_soft_ban(
    member: discord.Member,
    *,
    moderator_id: int,
    case_id: int | None = None,
    reason: str = "Soft ban",
    bot: discord.Client | None = None,
) -> tuple[bool, str]:
    if not is_main_guild(member.guild.id):
        return False, "Soft bans are issued from the main server."

    role = _banned_role(member.guild)
    if not role:
        return False, "Banned role is not configured or not found in this server."

    record_guild = soft_ban_record_guild_id()
    if role in member.roles and await is_soft_banned(record_guild, member.id):
        return False, f"{member.mention} is already soft-banned."

    keep_ids = {role.id, *softban_accompany_role_ids()}
    saved = [r.id for r in member.roles if r != member.guild.default_role and r.id not in keep_ids]
    assign = _softban_roles(member.guild)
    if not assign:
        return False, "Banned role is not configured or not found in this server."

    try:
        await member.edit(roles=assign, reason=f"{reason} (case #{case_id})" if case_id else reason)
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
        (record_guild, member.id, json.dumps(saved), case_id, moderator_id),
    )
    await db.commit()

    if bot:
        await sync_soft_ban_to_shift_guilds(bot, member.id, reason=reason)

    return True, f"Soft-banned {member.mention}. They can only access the ban appeal channel."


async def remove_soft_ban(
    guild: discord.Guild,
    user_id: int,
    *,
    reason: str = "Ban lifted",
    bot: discord.Client | None = None,
) -> tuple[bool, str]:
    member = guild.get_member(user_id)
    banned = _banned_role(guild)
    if not banned:
        return False, "Banned role not configured."

    record_guild = soft_ban_record_guild_id()
    soft_active = await is_soft_banned(record_guild, user_id)
    has_banned_role = member is not None and banned and banned in member.roles
    if not soft_active and not has_banned_role:
        return False, "That user is not soft-banned."

    # Clear DB before changing roles so on_member_update does not re-strip them.
    await deactivate_soft_ban_record(record_guild, user_id)

    if not member:
        await mark_pending_unban_roles(guild.id, user_id)
        if bot:
            await clear_soft_ban_on_shift_guilds(bot, user_id, reason=reason)
        return True, f"Soft-ban cleared for `{user_id}` (not in server; roles will restore when they rejoin)."

    try:
        assigned = await apply_unban_roles(member, reason=reason)
    except discord.Forbidden:
        return False, "Cannot restore roles — move the bot role above the member/banned/unban roles."
    except discord.HTTPException as exc:
        return False, f"Failed to remove soft ban: {exc}"

    role_names = ", ".join(r.name for r in assigned) or "none (check hierarchy)"
    if bot:
        await clear_soft_ban_on_shift_guilds(bot, user_id, reason=reason)
    return True, f"Removed soft-ban from {member.mention} and assigned: {role_names}."


async def full_unban(
    guild: discord.Guild,
    user_id: int,
    *,
    reason: str = "Ban lifted",
    bot: discord.Client | None = None,
) -> tuple[bool, str]:
    """Lift soft-ban or Discord hard-ban and apply standard unban roles when possible."""
    if await is_soft_banned(soft_ban_record_guild_id(), user_id):
        ok, msg = await remove_soft_ban(guild, user_id, reason=reason, bot=bot)
        return ok, msg

    try:
        ban_entry = await guild.fetch_ban(discord.Object(id=user_id))
    except discord.NotFound:
        member = guild.get_member(user_id)
        banned = _banned_role(guild)
        if member and banned and banned in member.roles:
            return await remove_soft_ban(guild, user_id, reason=reason, bot=bot)
        if await is_soft_banned(soft_ban_record_guild_id(), user_id):
            return await remove_soft_ban(guild, user_id, reason=reason, bot=bot)
        return False, "That user is not soft-banned or Discord-banned."

    try:
        await guild.unban(ban_entry.user, reason=reason)
    except discord.HTTPException as exc:
        return False, f"Failed to Discord-unban: {exc}"

    await deactivate_soft_ban_record(soft_ban_record_guild_id(), user_id)
    if bot:
        await clear_soft_ban_on_shift_guilds(bot, user_id, reason=reason)
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
    if is_main_guild(member.guild.id) and await consume_pending_unban_roles(member):
        return
    if not await is_soft_banned(soft_ban_record_guild_id(), member.id):
        return
    banned = _banned_role(member.guild)
    if not banned:
        return
    assign = _softban_roles(member.guild)
    allowed_ids = _allowed_softban_role_ids(member.guild)
    if all(r.id in allowed_ids for r in member.roles) and banned in member.roles:
        return
    try:
        await member.edit(roles=assign, reason="Soft ban: rejoin — restore banned roles")
    except discord.HTTPException:
        log.warning("Could not enforce soft ban on join for %s", member.id)


async def enforce_soft_ban_roles(member: discord.Member) -> bool:
    """Strip extra roles if a soft-banned member received new ones."""
    if not await is_soft_banned(soft_ban_record_guild_id(), member.id):
        return False
    banned = _banned_role(member.guild)
    if not banned:
        return False

    allowed_ids = _allowed_softban_role_ids(member.guild)
    assign = _softban_roles(member.guild)
    extra = [r for r in member.roles if r.id not in allowed_ids]
    if not extra:
        missing = [r for r in assign if r not in member.roles]
        if missing:
            try:
                await member.add_roles(*missing, reason="Soft ban: restore banned roles")
            except discord.HTTPException:
                pass
        return False

    try:
        await member.edit(roles=assign, reason="Soft ban: removed unauthorized roles")
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


async def _shift_banned_channel_id(guild: discord.Guild) -> int | None:
    stored = await _get_state_id(_shift_banned_channel_id_from_state_key(guild.id))
    if stored:
        ch = guild.get_channel(stored)
        if ch:
            return stored
    found = discord.utils.get(guild.text_channels, name=SHIFT_BANNED_CHANNEL_NAME)
    return found.id if found else None


async def setup_shift_banned_role_permissions(guild: discord.Guild) -> None:
    role = _banned_role(guild)
    channel_id = await _shift_banned_channel_id(guild)
    if not role or not channel_id:
        return
    allow_set = {channel_id}
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
                    reason="Shift soft ban: banned-chat only",
                )
            else:
                await channel.set_permissions(
                    role,
                    view_channel=False,
                    send_messages=False,
                    read_message_history=False,
                    reason="Shift soft ban: hide channel",
                )
        except discord.HTTPException as exc:
            log.warning("Shift ban overwrite failed %s: %s", channel.id, exc)


async def ensure_shift_ban_infrastructure(guild: discord.Guild) -> None:
    me = guild.me
    if not me:
        return

    role = discord.utils.get(guild.roles, name=SHIFT_BANNED_ROLE_NAME)
    if not role:
        try:
            role = await guild.create_role(
                name=SHIFT_BANNED_ROLE_NAME,
                colour=discord.Colour(SHIFT_BANNED_ROLE_COLOR),
                reason="WPD/FD soft-ban infrastructure",
            )
            await asyncio.sleep(0.2)
        except discord.HTTPException as exc:
            log.error("Could not create Banned role in guild %s: %s", guild.id, exc)
            return
    else:
        try:
            await role.edit(colour=discord.Colour(SHIFT_BANNED_ROLE_COLOR), reason="WPD/FD soft-ban infrastructure")
        except discord.HTTPException:
            pass

    await _set_state_id(_shift_banned_role_id_from_state_key(guild.id), role.id)

    channel = discord.utils.get(guild.text_channels, name=SHIFT_BANNED_CHANNEL_NAME)
    if not channel:
        try:
            channel = await guild.create_text_channel(
                SHIFT_BANNED_CHANNEL_NAME,
                reason="Soft-ban appeal chat (shift server)",
            )
            await asyncio.sleep(0.2)
        except discord.HTTPException as exc:
            log.error("Could not create banned-chat in guild %s: %s", guild.id, exc)
            return

    await _set_state_id(_shift_banned_channel_id_from_state_key(guild.id), channel.id)
    await setup_shift_banned_role_permissions(guild)


async def provision_shift_ban_infrastructure(bot: discord.Client) -> None:
    """One-time per deploy flag: ensure FD/PD Banned role + banned-chat channel exist."""
    from utils.core import get_state, set_state

    if await get_state(SHIFT_BAN_INFRA_STATE_KEY):
        return
    for guild_id in shift_guild_ids():
        guild = bot.get_guild(guild_id)
        if guild:
            await ensure_shift_ban_infrastructure(guild)
    await set_state(SHIFT_BAN_INFRA_STATE_KEY, "1")
    log.info("Shift server soft-ban infrastructure provisioned (FD/PD).")
