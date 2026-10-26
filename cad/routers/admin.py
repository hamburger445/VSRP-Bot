"""Admin: sessions, overrides, audit, charges."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from cad.db import audit, connection
from cad.deps import require_perm

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _js(row) -> dict:
    data = dict(row)
    for k, v in list(data.items()):
        if hasattr(v, "isoformat"):
            data[k] = v.isoformat()
    return data


class OverrideBody(BaseModel):
    discord_user_id: int
    permission: str
    granted: bool = True
    reason: str | None = None


class ChargeBody(BaseModel):
    code: str
    description: str
    discipline: str | None = None


@router.get("/audit")
async def cad_audit_log(user: dict = Depends(require_perm("cad.admin"))):
    async with connection() as conn:
        rows = await conn.fetch("SELECT * FROM cad_audit ORDER BY created_at DESC LIMIT 200")
        bot_rows = await conn.fetch("SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT 50")
    return {"cad": [_js(r) for r in rows], "bot": [_js(r) for r in bot_rows]}


@router.get("/sessions")
async def list_sessions(user: dict = Depends(require_perm("cad.admin"))):
    async with connection() as conn:
        rows = await conn.fetch(
            """
            SELECT id, discord_user_id, expires_at, last_seen_at, revoked_at, created_at
            FROM cad_sessions ORDER BY created_at DESC LIMIT 100
            """
        )
    return {"results": [_js(r) for r in rows]}


@router.post("/sessions/{session_id}/revoke")
async def revoke_session(session_id: str, user: dict = Depends(require_perm("cad.admin"))):
    async with connection() as conn:
        row = await conn.fetchrow(
            "UPDATE cad_sessions SET revoked_at = NOW() WHERE id = $1::uuid RETURNING id",
            session_id,
        )
        if not row:
            raise HTTPException(404, "Session not found")
        await audit(conn, int(user["discord_user_id"]), "session_revoke", "cad_sessions", session_id, {})
    return {"ok": True}


@router.get("/overrides")
async def list_overrides(user: dict = Depends(require_perm("cad.admin"))):
    async with connection() as conn:
        rows = await conn.fetch("SELECT * FROM cad_permission_overrides ORDER BY created_at DESC")
    return {"results": [_js(r) for r in rows]}


@router.post("/overrides")
async def set_override(body: OverrideBody, user: dict = Depends(require_perm("cad.admin"))):
    async with connection() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO cad_permission_overrides (discord_user_id, permission, granted, reason, granted_by)
            VALUES ($1,$2,$3,$4,$5)
            ON CONFLICT (discord_user_id, permission) DO UPDATE SET
                granted = EXCLUDED.granted, reason = EXCLUDED.reason, granted_by = EXCLUDED.granted_by
            RETURNING *
            """,
            body.discord_user_id,
            body.permission,
            body.granted,
            body.reason,
            int(user["discord_user_id"]),
        )
        await audit(conn, int(user["discord_user_id"]), "override_set", "cad_permission_overrides", body.discord_user_id, {"permission": body.permission})
    return _js(row)


@router.get("/charges")
async def list_charges(user: dict = Depends(require_perm("cad.admin", "mdt.police"))):
    async with connection() as conn:
        rows = await conn.fetch("SELECT * FROM cad_charges ORDER BY code")
    return {"results": [_js(r) for r in rows]}


@router.post("/charges")
async def add_charge(body: ChargeBody, user: dict = Depends(require_perm("cad.admin"))):
    async with connection() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO cad_charges (code, description, discipline) VALUES ($1,$2,$3)
            ON CONFLICT (code) DO UPDATE SET description=EXCLUDED.description, discipline=EXCLUDED.discipline
            RETURNING *
            """,
            body.code,
            body.description,
            body.discipline,
        )
    return _js(row)
