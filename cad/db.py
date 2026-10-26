"""CAD database helpers: PostgreSQL when available, otherwise GitHub JSON."""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from cad.github_db import github_connection, github_enabled
from utils.database import get_db

log = logging.getLogger("vsrp_bot.cad.db")


def using_github() -> bool:
    return github_enabled()


@asynccontextmanager
async def connection() -> AsyncIterator[Any]:
    if github_enabled():
        async with github_connection() as conn:
            yield conn
        return
    db = await get_db()
    async with db._pool.acquire() as conn:  # noqa: SLF001
        yield conn


@asynccontextmanager
async def transaction() -> AsyncIterator[Any]:
    async with connection() as conn:
        tx = conn.transaction()
        if hasattr(tx, "__aenter__"):
            async with tx:
                yield conn
        else:
            yield conn


def row_to_dict(row: Any | None) -> dict | None:
    if row is None:
        return None
    return dict(row)


def rows_to_dicts(rows: list[Any]) -> list[dict]:
    return [dict(r) for r in rows]


async def enqueue_outbox(conn: Any, kind: str, payload: dict) -> None:
    await conn.execute(
        "INSERT INTO cad_outbox (kind, payload_json) VALUES ($1, $2)",
        kind,
        json.dumps(payload),
    )


async def audit(
    conn: Any,
    actor_id: int | None,
    action: str,
    entity_type: str | None = None,
    entity_id: Any = None,
    details: dict | None = None,
) -> None:
    await conn.execute(
        """
        INSERT INTO cad_audit (actor_id, action, entity_type, entity_id, details_json)
        VALUES ($1, $2, $3, $4, $5)
        """,
        actor_id,
        action,
        entity_type,
        str(entity_id) if entity_id is not None else None,
        json.dumps(details) if details else None,
    )
