"""Sequential LEO case numbers (0000+). Allocation is atomic."""

from __future__ import annotations

import asyncpg

_CASE_STATE_KEY = "leo_case_sequence"


async def allocate_case_number_on_conn(conn: asyncpg.Connection) -> str:
    # Seed -1 so the first UPDATE yields 0 (displayed 0000). Existing counters
    # are left in place and only incremented.
    await conn.execute(
        "INSERT INTO bot_state (key, value) VALUES ($1, '-1') ON CONFLICT (key) DO NOTHING",
        _CASE_STATE_KEY,
    )
    row = await conn.fetchrow(
        """
        UPDATE bot_state
        SET value = (value::int + 1)::text
        WHERE key = $1
        RETURNING value
        """,
        _CASE_STATE_KEY,
    )
    n = int(row["value"]) if row else 0
    return f"{n:04d}"


async def allocate_case_number() -> str:
    from utils.database import get_db

    db = await get_db()
    pool = db._pool  # noqa: SLF001
    async with pool.acquire() as conn:
        async with conn.transaction():
            return await allocate_case_number_on_conn(conn)


def format_case_number(value: str | int | None) -> str:
    if value is None:
        return "0000"
    if isinstance(value, int):
        return f"{value:04d}"
    s = str(value).strip()
    if s.isdigit():
        return f"{int(s):04d}"
    return s[:16]
