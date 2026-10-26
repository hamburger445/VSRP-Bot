"""Postgres LISTEN/NOTIFY fan-out to WebSocket clients."""

from __future__ import annotations

import asyncio
import json
import logging

import asyncpg

from cad.settings import get_settings

log = logging.getLogger("vsrp_bot.cad.realtime")

_clients: set[asyncio.Queue] = set()
_listen_task: asyncio.Task | None = None


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    _clients.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    _clients.discard(q)


async def _listen_loop() -> None:
    settings = get_settings()
    url = settings.database_direct_url or settings.database_url
    if not url:
        return
    while True:
        conn = None
        try:
            conn = await asyncpg.connect(url, timeout=20)
            await conn.add_listener("cad_events", _on_notify)
            log.info("CAD LISTEN cad_events connected")
            while True:
                await asyncio.sleep(30)
                await conn.execute("SELECT 1")
        except asyncio.CancelledError:
            if conn:
                await conn.close()
            raise
        except Exception as exc:
            log.warning("CAD LISTEN failed (%s); retrying", exc)
            if conn:
                try:
                    await conn.close()
                except Exception:
                    pass
            await asyncio.sleep(5)


def _on_notify(connection, pid, channel, payload: str) -> None:  # noqa: ARG001
    for q in list(_clients):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            pass


def start_listener() -> None:
    global _listen_task
    if _listen_task is None or _listen_task.done():
        _listen_task = asyncio.create_task(_listen_loop())


async def stop_listener() -> None:
    global _listen_task
    if _listen_task:
        _listen_task.cancel()
        _listen_task = None
