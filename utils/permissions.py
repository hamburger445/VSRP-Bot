import json
import logging
import re
from typing import Any

import discord

from utils.core import fd_guild_id, is_main_guild, is_shift_guild, load_config, pd_guild_id
from utils.database import DatabaseError, get_db

log = logging.getLogger("vsrp_bot.permissions")

PERMISSION_DEFINITIONS: dict[str, dict[str, str]] = {
    "staff": {
        "label": "Staff",
        "question": "Who can use staff tools? (tickets, panels, logs)",
        "description": "Tickets, panels, ticket logs, and general staff tools",
    },
    "admin": {
        "label": "Admin",
        "question": "Who has full admin access?",
        "description": "Full admin access (grants all other permissions)",
    },
    "warn": {
        "label": "Warn",
        "question": "Who can warn members?",
        "description": "Issue warnings (-warn)",
    },
    "moderate": {
        "label": "Moderate",
        "question": "Who can moderate? (kick, mute, purge, cases)",
        "description": "Kick, mute, purge, mod logs, cases, /strike",
    },
    "ban": {
        "label": "Ban",
        "question": "Who can ban and unban members?",
        "description": "Ban and unban members, /modban",
    },
    "leo": {
        "label": "Law Enforcement",
        "question": "Who can use LEO commands?",
        "description": "LEO panel, citations, warrants, pager",
    },
    "shift": {
        "label": "Shifts",
        "question": "Who can use shifts? (/shift manage)",
        "description": "Start, break, and stop your own shift on FD/PD",
    },
    "shift_admin": {
        "label": "Shift Admin",
        "question": "Who can manage other people's shifts? (/shift admin)",
        "description": "Manage shifts on FD/PD servers (/shift admin)",
    },
    "sessions": {
        "label": "Sessions",
        "question": "Who can start and end RP sessions?",
        "description": "Start and end RP sessions (/startup, /over)",
    },
}

MAIN_GUILD_PERMISSION_KEYS: tuple[str, ...] = (
    "staff",
    "admin",
    "warn",
    "moderate",
    "ban",
    "leo",
    "sessions",
)

SHIFT_GUILD_PERMISSION_KEYS: tuple[str, ...] = (
    "staff",
    "admin",
    "warn",
    "moderate",
    "ban",
    "shift",
    "shift_admin",
)

PERMISSION_ALIASES = {
    "mod": "moderate",
    "mods": "moderate",
    "moderator": "moderate",
    "moderators": "moderate",
    "warns": "warn",
    "warning": "warn",
    "warnings": "warn",
    "bans": "ban",
    "unban": "ban",
    "leo": "leo",
    "police": "leo",
    "shifts": "shift",
    "shift": "shift",
    "shiftadmin": "shift_admin",
    "shift-admin": "shift_admin",
    "session": "sessions",
    "startup": "sessions",
}

_cache: dict[int, dict[str, list[int]]] = {}


def permission_keys_for_guild(guild_id: int) -> tuple[str, ...]:
    if is_main_guild(guild_id):
        return MAIN_GUILD_PERMISSION_KEYS
    if is_shift_guild(guild_id):
        return SHIFT_GUILD_PERMISSION_KEYS
    return MAIN_GUILD_PERMISSION_KEYS


def setup_intro_for_guild(guild_id: int) -> str:
    if is_main_guild(guild_id):
        return (
            "Configure **Main RP** permissions: staff tools, moderation, LEO, and sessions.\n"
            "Shift permissions are configured separately on FD and PD servers."
        )
    if guild_id == fd_guild_id():
        return (
            "Configure **Fire Department** permissions: shifts and moderation.\n"
            "Sessions and LEO commands are configured on the main RP server."
        )
    if guild_id == pd_guild_id():
        return (
            "Configure **City PD / LEO** permissions: shifts and moderation.\n"
            "Sessions and LEO panel commands are configured on the main RP server."
        )
    return "Pick a question below, then select which roles should have that permission."


def _legacy_roles(guild_id: int) -> dict[str, list[int]]:
    cfg = load_config()
    yaml_roles = cfg.get("guild_roles", {}).get(guild_id, {})
    global_roles = cfg.get("roles", {})
    server_staff = 0
    server_admin = 0
    for entry in cfg.get("servers", {}).values():
        if int(entry.get("id", 0)) == guild_id:
            server_staff = int(entry.get("staff_role") or 0)
            server_admin = int(entry.get("admin_role") or 0)
            break

    staff_id = int(yaml_roles.get("staff") or server_staff or global_roles.get("staff") or 0)
    admin_id = int(yaml_roles.get("admin") or server_admin or global_roles.get("admin") or 0)

    perms: dict[str, list[int]] = {key: [] for key in PERMISSION_DEFINITIONS}
    if staff_id:
        perms["staff"] = [staff_id]
        perms["sessions"] = [staff_id]
        perms["shift"] = [staff_id]
        perms["shift_admin"] = [staff_id]
        perms["moderate"] = [staff_id]
        perms["warn"] = [staff_id]
    if admin_id:
        perms["admin"] = [admin_id]
        perms["ban"] = [admin_id]
    return perms


def _normalize_permissions(raw: dict[str, Any] | None) -> dict[str, list[int]]:
    perms = {key: [] for key in PERMISSION_DEFINITIONS}
    if not raw:
        return perms
    for key in PERMISSION_DEFINITIONS:
        values = raw.get(key, [])
        if isinstance(values, int):
            values = [values]
        if isinstance(values, list):
            perms[key] = [int(v) for v in values if v]
    return perms


async def _fetch_guild_settings_row(guild_id: int) -> dict | None:
    db = await get_db()
    try:
        return await db.execute_fetchone(
            "SELECT permissions_json, staff_role_id, admin_role_id FROM guild_settings WHERE guild_id = ?",
            (guild_id,),
        )
    except DatabaseError as exc:
        if "permissions_json" not in str(exc):
            raise
        log.warning("permissions_json column missing; using legacy role columns only")
        return await db.execute_fetchone(
            "SELECT staff_role_id, admin_role_id FROM guild_settings WHERE guild_id = ?",
            (guild_id,),
        )


async def load_guild_permissions(guild_id: int) -> dict[str, list[int]]:
    if guild_id in _cache:
        return _cache[guild_id]

    row = await _fetch_guild_settings_row(guild_id)
    if not row:
        github_perms = None
        try:
            from utils.github_store import load_guild_permissions_from_github

            github_perms = await load_guild_permissions_from_github(guild_id)
        except Exception:
            log.debug("GitHub permission load skipped for guild %s", guild_id)
        if github_perms:
            perms = _normalize_permissions(github_perms)
            _cache[guild_id] = perms
            return perms
        perms = _legacy_roles(guild_id)
        _cache[guild_id] = perms
        return perms

    if row.get("permissions_json"):
        try:
            parsed = json.loads(row["permissions_json"])
            perms = _normalize_permissions(parsed)
            if any(perms.values()):
                _cache[guild_id] = perms
                return perms
        except json.JSONDecodeError:
            log.warning("Invalid permissions_json for guild %s", guild_id)

    perms = _legacy_roles(guild_id)
    if row.get("staff_role_id"):
        perms["staff"] = list(set(perms["staff"] + [int(row["staff_role_id"])]))
    if row.get("admin_role_id"):
        perms["admin"] = list(set(perms["admin"] + [int(row["admin_role_id"])]))
    _cache[guild_id] = perms
    return perms


def invalidate_guild_permissions(guild_id: int) -> None:
    _cache.pop(guild_id, None)


async def save_guild_permissions(guild_id: int, permissions: dict[str, list[int]]) -> None:
    normalized = _normalize_permissions(permissions)
    db = await get_db()
    await db.execute(
        """
        INSERT INTO guild_settings (guild_id, permissions_json, staff_role_id, admin_role_id, configured_at)
        VALUES (?, ?, ?, ?, NOW())
        ON CONFLICT (guild_id) DO UPDATE SET
            permissions_json = EXCLUDED.permissions_json,
            staff_role_id = EXCLUDED.staff_role_id,
            admin_role_id = EXCLUDED.admin_role_id,
            configured_at = NOW()
        """,
        (
            guild_id,
            json.dumps(normalized),
            normalized["staff"][0] if normalized["staff"] else None,
            normalized["admin"][0] if normalized["admin"] else None,
        ),
    )
    await db.commit()
    _cache[guild_id] = normalized
    try:
        from utils.github_store import sync_guild_permissions_to_github

        await sync_guild_permissions_to_github(guild_id, normalized)
    except Exception:
        log.exception("GitHub permission sync failed for guild %s", guild_id)


async def set_permission_roles(guild_id: int, key: str, role_ids: list[int]) -> dict[str, list[int]]:
    perms = await load_guild_permissions(guild_id)
    perms[key] = role_ids
    await save_guild_permissions(guild_id, perms)
    return perms


async def add_role_to_permissions(
    guild_id: int,
    role_id: int,
    keys: list[str],
) -> dict[str, list[int]]:
    perms = await load_guild_permissions(guild_id)
    for key in keys:
        if key not in PERMISSION_DEFINITIONS:
            continue
        if role_id not in perms[key]:
            perms[key].append(role_id)
    await save_guild_permissions(guild_id, perms)
    return perms


def find_roles_by_query(guild: discord.Guild, query: str) -> list[discord.Role]:
    query = query.strip()
    if not query:
        return []

    role_mention = re.fullmatch(r"<@&(\d+)>", query)
    if role_mention:
        role = guild.get_role(int(role_mention.group(1)))
        return [role] if role and role != guild.default_role else []

    user_style_mention = re.fullmatch(r"<@!?(\d+)>", query)
    if user_style_mention:
        role = guild.get_role(int(user_style_mention.group(1)))
        return [role] if role and role != guild.default_role else []

    if query.isdigit():
        role = guild.get_role(int(query))
        return [role] if role and role != guild.default_role else []

    needle = query.lower()
    matches = [
        role
        for role in guild.roles
        if role != guild.default_role and needle in role.name.lower()
    ]
    return sorted(matches, key=lambda r: r.position, reverse=True)


def resolve_permission_keys(raw_keys: list[str]) -> list[str]:
    resolved: list[str] = []
    for raw in raw_keys:
        key = raw.lower().strip()
        key = PERMISSION_ALIASES.get(key, key)
        if key in PERMISSION_DEFINITIONS and key not in resolved:
            resolved.append(key)
    return resolved


def permissions_for_role(role_id: int, permissions: dict[str, list[int]]) -> list[str]:
    return [key for key, role_ids in permissions.items() if role_id in role_ids]


def _god_ids() -> set[int]:
    return set(load_config().get("owners", []))


def _member_role_ids(member: discord.Member) -> set[int]:
    return {r.id for r in member.roles}


def _department_role_ids(*keys: str) -> set[int]:
    depts = load_config().get("roles", {}).get("departments", {})
    ids: set[int] = set()
    for key in keys:
        val = depts.get(key)
        if isinstance(val, int) and val:
            ids.add(val)
        elif isinstance(val, list):
            ids.update(r for r in val if r)
    return ids


def is_god(user: discord.Member | discord.User) -> bool:
    return user.id in _god_ids()


def is_owner(user: discord.Member | discord.User) -> bool:
    return is_god(user)


def _role_match(member: discord.Member, role_ids: list[int]) -> bool:
    if not role_ids:
        return False
    return bool(_member_role_ids(member) & set(role_ids))


def has_permission(member: discord.Member, key: str) -> bool:
    if is_god(member):
        return True
    if member.guild_permissions.administrator:
        return True

    perms = _cache.get(member.guild.id)
    if perms is None:
        perms = _legacy_roles(member.guild.id)

    if _role_match(member, perms.get("admin", [])):
        return True
    if key == "admin":
        return _role_match(member, perms.get("admin", []))
    if _role_match(member, perms.get(key, [])):
        return True
    if key == "warn" and _role_match(member, perms.get("moderate", [])):
        return True
    return False


async def has_permission_async(member: discord.Member, key: str) -> bool:
    if is_god(member):
        return True
    if member.guild_permissions.administrator:
        return True

    perms = await load_guild_permissions(member.guild.id)
    if _role_match(member, perms.get("admin", [])):
        return True
    if key == "admin":
        return _role_match(member, perms.get("admin", []))
    if _role_match(member, perms.get(key, [])):
        return True
    if key == "warn" and _role_match(member, perms.get("moderate", [])):
        return True
    return False


def is_staff(member: discord.Member) -> bool:
    return (
        has_permission(member, "staff")
        or has_permission(member, "admin")
        or has_permission(member, "moderate")
    )


def is_admin(member: discord.Member) -> bool:
    return has_permission(member, "admin")


def can_warn(member: discord.Member) -> bool:
    return has_permission(member, "warn")


def can_moderate(member: discord.Member) -> bool:
    return has_permission(member, "moderate")


def can_ban(member: discord.Member) -> bool:
    return has_permission(member, "ban")


def can_shift(member: discord.Member) -> bool:
    return has_permission(member, "shift") or has_permission(member, "shift_admin")


def can_leo(member: discord.Member) -> bool:
    if is_god(member):
        return True
    if has_permission(member, "leo"):
        return True
    police_roles = _department_role_ids("police1", "police2")
    if police_roles and _member_role_ids(member) & police_roles:
        return True
    return is_staff(member)


def is_police(member: discord.Member) -> bool:
    if is_god(member):
        return True
    police_roles = _department_role_ids("police1", "police2")
    if not police_roles:
        return False
    return bool(_member_role_ids(member) & police_roles)


def is_leo(member: discord.Member) -> bool:
    return can_leo(member)


def is_session_host(member: discord.Member) -> bool:
    return has_permission(member, "sessions") or is_staff(member)


def has_role(member: discord.Member, role_key: str) -> bool:
    if is_god(member):
        return True
    roles = load_config().get("roles", {})
    role_id = roles.get(role_key)
    if not role_id:
        return member.guild_permissions.administrator
    return any(r.id == role_id for r in member.roles)
