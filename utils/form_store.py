"""Warrant review records in the GitHub JSON database."""

from __future__ import annotations

from utils.database import get_db


async def create_review(
    *,
    kind: str,
    case_number: str,
    officer_id: int,
    subject_id: int,
    guild_id: int,
    jti: str,
    record_id: int | None = None,
) -> int:
    db = await get_db()
    await db.execute(
        """
        INSERT INTO form_reviews (kind, case_number, officer_id, subject_id, guild_id, jti, record_id, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
        """,
        (kind, str(case_number), officer_id, subject_id, guild_id, jti, record_id or 0),
    )
    await db.commit()
    row = await db.execute_fetchone(
        "SELECT id FROM form_reviews WHERE jti = ? ORDER BY id DESC LIMIT 1",
        (jti,),
    )
    return int((row or {}).get("id") or 0)


async def get_review(review_id: int) -> dict | None:
    db = await get_db()
    row = await db.execute_fetchone("SELECT * FROM form_reviews WHERE id = ?", (review_id,))
    return dict(row) if row else None


async def set_review_status(review_id: int, status: str, reviewer_id: int) -> dict | None:
    db = await get_db()
    await db.execute(
        "UPDATE form_reviews SET status = ?, reviewer_id = ? WHERE id = ? AND status = 'pending'",
        (status, reviewer_id, review_id),
    )
    await db.commit()
    return await get_review(review_id)
