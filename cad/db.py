"""CAD database helpers on the shared asyncpg pool."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import asyncpg

from utils.database import get_db


async def pool() -> asyncpg.Pool:
    db = await get_db()
    return db._pool  # noqa: SLF001 — shared process pool


@asynccontextmanager
async def connection() -> AsyncIterator[asyncpg.Connection]:
    async with (await pool()).acquire() as conn:
        yield conn


@asynccontextmanager
async def transaction() -> AsyncIterator[asyncpg.Connection]:
    async with (await pool()).acquire() as conn:
        async with conn.transaction():
            yield conn


def row_to_dict(row: asyncpg.Record | None) -> dict | None:
    if row is None:
        return None
    return dict(row)


def rows_to_dicts(rows: list[asyncpg.Record]) -> list[dict]:
    return [dict(r) for r in rows]


async def enqueue_outbox(conn: asyncpg.Connection, kind: str, payload: dict) -> None:
    await conn.execute(
        "INSERT INTO cad_outbox (kind, payload_json) VALUES ($1, $2)",
        kind,
        json.dumps(payload),
    )


async def audit(
    conn: asyncpg.Connection,
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
