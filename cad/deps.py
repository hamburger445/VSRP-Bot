"""FastAPI auth dependencies."""

from __future__ import annotations

import json
from typing import Any

from fastapi import Cookie, Depends, HTTPException, Request

from datetime import datetime

from cad.security import hash_token, utcnow
from cad.settings import get_settings
from utils.cad_permissions import compute_permissions, needs_dm_verify

COOKIE_NAME = "vsrp_cad"
CSRF_COOKIE = "vsrp_cad_csrf"

ELEVATED_PERMS = {
    "mdt.police",
    "mdt.fire",
    "mdt.ems",
    "mdt.dot",
    "dispatch.operate",
    "cad.admin",
    "citations.manage",
    "warrants.manage",
    "calls.manage",
}


async def _load_guild_perms(conn, guild_id: int) -> dict[str, list[int]]:
    if not guild_id:
        return {}
    row = await conn.fetchrow(
        "SELECT permissions_json FROM guild_settings WHERE guild_id = $1",
        guild_id,
    )
    if not row or not row["permissions_json"]:
        return {}
    try:
        parsed = json.loads(row["permissions_json"])
    except json.JSONDecodeError:
        return {}
    out: dict[str, list[int]] = {}
    for k, v in parsed.items():
        if isinstance(v, int):
            out[k] = [v]
        elif isinstance(v, list):
            out[k] = [int(x) for x in v if x]
    return out


def _json_int_list(raw: str | list | None) -> set[int]:
    if isinstance(raw, list):
        return {int(x) for x in raw if x is not None and str(x).lstrip("-").isdigit()}
    try:
        return {int(x) for x in json.loads(raw or "[]")}
    except (json.JSONDecodeError, TypeError, ValueError):
        return set()


async def current_session(
    request: Request,
    vsrp_cad: str | None = Cookie(default=None),
) -> dict[str, Any]:
    raw = vsrp_cad or request.cookies.get(COOKIE_NAME)
    if not raw:
        raise HTTPException(401, "Not authenticated")
    from cad.db import connection

    token_hash = hash_token(raw.strip().strip('"'))
    async with connection() as conn:
        sess = await conn.fetchrow(
            "SELECT * FROM cad_sessions WHERE token_hash = $1",
            token_hash,
        )
        if not sess or sess.get("revoked_at") is not None:
            raise HTTPException(401, "Session expired")
        exp = sess.get("expires_at")
        if isinstance(exp, str):
            try:
                exp = datetime.fromisoformat(exp.replace("Z", "+00:00"))
            except ValueError:
                exp = None
        if exp is not None and exp < utcnow():
            raise HTTPException(401, "Session expired")
        user_row = await conn.fetchrow(
            "SELECT * FROM cad_users WHERE discord_user_id = $1",
            sess["discord_user_id"],
        )
        if not user_row:
            raise HTTPException(401, "Session expired")
        row = dict(user_row)
        row["id"] = sess["id"]
        row["discord_user_id"] = sess["discord_user_id"]
        row["expires_at"] = exp
        if row["status"] and row["status"] != "active":
            raise HTTPException(403, "Account suspended")
        await conn.execute("UPDATE cad_sessions SET last_seen_at = NOW() WHERE id = $1", row["id"])
        overrides_rows = await conn.fetch(
            """
            SELECT permission, granted FROM cad_permission_overrides
            WHERE discord_user_id = $1
              AND (expires_at IS NULL OR expires_at > NOW())
            """,
            row["discord_user_id"],
        )
        settings = get_settings()
        main_perms = await _load_guild_perms(conn, settings.main_guild_id)
        fd_perms = await _load_guild_perms(conn, settings.fd_guild_id)
        pd_perms = await _load_guild_perms(conn, settings.pd_guild_id)

    overrides = {r["permission"]: bool(r["granted"]) for r in overrides_rows}
    perms = compute_permissions(
        discord_user_id=int(row["discord_user_id"]),
        in_main_guild=bool(row["in_main_guild"]),
        owners=settings.owners,
        main_roles=_json_int_list(row["main_roles"]),
        fd_roles=_json_int_list(row["fd_roles"]),
        pd_roles=_json_int_list(row["pd_roles"]),
        main_perms=main_perms,
        fd_perms=fd_perms,
        pd_perms=pd_perms,
        department_roles=settings.department_roles,
        reviewer_role_ids=settings.reviewer_role_ids,
        overrides=overrides,
    )
    user = {
        "discord_user_id": int(row["discord_user_id"]),
        "username": row["username"],
        "global_name": row["global_name"],
        "avatar_hash": row["avatar_hash"],
        "dm_verified_at": row["dm_verified_at"].isoformat() if hasattr(row.get("dm_verified_at"), "isoformat") else row.get("dm_verified_at"),
        "permissions": sorted(perms),
        "needs_dm_verify": bool(
            settings.require_dm_verify and needs_dm_verify(perms) and not row["dm_verified_at"]
        ),
        "in_main_guild": bool(row["in_main_guild"]),
    }
    request.state.cad_user = user
    return user


def require_perm(*needed: str):
    async def _inner(user: dict = Depends(current_session)) -> dict:
        have = set(user.get("permissions") or [])
        if "cad.admin" in have:
            return user
        if not any(p in have for p in needed):
            raise HTTPException(403, "Permission denied")
        settings = get_settings()
        if (
            settings.require_dm_verify
            and user.get("needs_dm_verify")
            and set(needed) & ELEVATED_PERMS
        ):
            raise HTTPException(403, "Discord DM verification required")
        return user

    return _inner
