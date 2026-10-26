"""Shared citation/warrant SQL used by Discord LEO and the CAD API."""

from __future__ import annotations

import json

import asyncpg

from utils.leo_case import allocate_case_number_on_conn
from utils.users import ensure_user
from utils.database import get_db


async def create_citation(
    *,
    subject_id: int,
    issued_by: int,
    form_data: dict,
    fine: int,
    violation: str,
    conn: asyncpg.Connection | None = None,
) -> dict:
    case_no = await allocate_case_number_on_conn(conn) if conn else await _alloc()
    form_json = json.dumps(form_data)
    own = conn is None
    if own:
        db = await get_db()
        await ensure_user(db, subject_id)
        row = await db.execute_fetchone(
            """
            INSERT INTO citation_tickets (
                user_id, violation, fine, issued_by, payment_status, form_json, case_number
            )
            VALUES (?, ?, ?, ?, 'unpaid', ?, ?)
            RETURNING id, case_number, user_id, violation, fine, issued_by, payment_status, created_at
            """,
            (subject_id, violation, fine, issued_by, form_json, case_no),
        )
        await db.commit()
        return dict(row or {})
    await conn.execute(
        "INSERT INTO users (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING",
        subject_id,
    )
    row = await conn.fetchrow(
        """
        INSERT INTO citation_tickets (
            user_id, violation, fine, issued_by, payment_status, form_json, case_number
        )
        VALUES ($1, $2, $3, $4, 'unpaid', $5, $6)
        RETURNING id, case_number, user_id, violation, fine, issued_by, payment_status, created_at
        """,
        subject_id,
        violation,
        fine,
        issued_by,
        form_json,
        case_no,
    )
    return dict(row)


async def create_warrant(
    *,
    subject_id: int,
    issued_by: int,
    form_data: dict,
    reason: str,
    conn: asyncpg.Connection | None = None,
) -> dict:
    case_no = await allocate_case_number_on_conn(conn) if conn else await _alloc()
    form_json = json.dumps(form_data)
    if conn is None:
        db = await get_db()
        await ensure_user(db, subject_id)
        row = await db.execute_fetchone(
            """
            INSERT INTO warrants (
                user_id, reason, issued_by, active, status, form_json, case_number
            )
            VALUES (?, ?, ?, 1, 'active', ?, ?)
            RETURNING id, case_number, user_id, reason, issued_by, active, status, created_at
            """,
            (subject_id, reason, issued_by, form_json, case_no),
        )
        await db.commit()
        return dict(row or {})
    await conn.execute(
        "INSERT INTO users (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING",
        subject_id,
    )
    row = await conn.fetchrow(
        """
        INSERT INTO warrants (
            user_id, reason, issued_by, active, status, form_json, case_number
        )
        VALUES ($1, $2, $3, 1, 'active', $4, $5)
        RETURNING id, case_number, user_id, reason, issued_by, active, status, created_at
        """,
        subject_id,
        reason,
        issued_by,
        form_json,
        case_no,
    )
    return dict(row)


async def _alloc() -> str:
    from utils.leo_case import allocate_case_number

    return await allocate_case_number()


async def pay_citation(ticket_id: int, conn: asyncpg.Connection) -> dict | None:
    row = await conn.fetchrow(
        """
        UPDATE citation_tickets
        SET payment_status = 'paid', paid_at = NOW()
        WHERE id = $1 AND payment_status = 'unpaid'
        RETURNING id, user_id, case_number, violation, fine
        """,
        ticket_id,
    )
    return dict(row) if row else None


async def serve_warrant(warrant_id: int, served_by: int, serve_data: dict, conn: asyncpg.Connection) -> dict | None:
    row = await conn.fetchrow(
        """
        UPDATE warrants
        SET active = 0, status = 'served', cleared_at = NOW(),
            served_at = NOW(), served_by = $1, serve_form_json = $2
        WHERE id = $3 AND active = 1
        RETURNING id, user_id, case_number, reason
        """,
        served_by,
        json.dumps(serve_data),
        warrant_id,
    )
    return dict(row) if row else None


async def clear_warrant(warrant_id: int, conn: asyncpg.Connection) -> dict | None:
    row = await conn.fetchrow(
        """
        UPDATE warrants
        SET active = 0, status = 'cleared', cleared_at = NOW()
        WHERE id = $1 AND active = 1
        RETURNING id, user_id, case_number, reason
        """,
        warrant_id,
    )
    return dict(row) if row else None
