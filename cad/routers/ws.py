from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from cad.db import connection
from cad.deps import COOKIE_NAME
from cad.realtime import subscribe, unsubscribe
from cad.security import utcnow

router = APIRouter()


@router.websocket("/api/ws")
async def cad_ws(ws: WebSocket):
    token = ws.cookies.get(COOKIE_NAME)
    if not token:
        await ws.close(code=4401)
        return
    from cad.security import hash_token as ht  # noqa: PLC0415

    async with connection() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, expires_at, revoked_at FROM cad_sessions WHERE token_hash = $1
            """,
            ht(token),
        )
    if not row or row["revoked_at"] or row["expires_at"] < utcnow():
        await ws.close(code=4401)
        return
    await ws.accept()
    q = subscribe()
    try:
        while True:
            try:
                payload = await asyncio.wait_for(q.get(), timeout=25)
                await ws.send_text(payload)
            except asyncio.TimeoutError:
                await ws.send_json({"table": "ping", "op": "ping"})
    except WebSocketDisconnect:
        pass
    finally:
        unsubscribe(q)
