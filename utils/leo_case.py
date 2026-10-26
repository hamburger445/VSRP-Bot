"""Sequential LEO case numbers (0000+). Allocation is atomic."""

from __future__ import annotations

import asyncpg

_CASE_STATE_KEY = "leo_case_sequence"


async def allocate_case_number_on_conn(conn: asyncpg.Connection) -> str:
    row = await conn.fetchrow(
        """
        INSERT INTO bot_state (key, value) VALUES ($1, '1')
        ON CONFLICT (key) DO UPDATE
        SET value = (COALESCE(NULLIF(bot_state.value, '')::int, 0) + 1)::text
        RETURNING value
        """,
        _CASE_STATE_KEY,
    )
    n = int(row["value"]) if row else 1
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
