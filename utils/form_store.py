"""Drafts and review records in the GitHub JSON database."""

from __future__ import annotations

import json
from typing import Any

from utils.database import get_db


async def save_draft(*, jti: str, kind: str, officer_id: int, subject_id: int, guild_id: int, payload: dict) -> None:
    db = await get_db()
    existing = await db.execute_fetchone("SELECT id FROM form_drafts WHERE jti = ?", (jti,))
    blob = json.dumps(payload)
    if existing:
        await db.execute(
            "UPDATE form_drafts SET kind = ?, payload = ? WHERE jti = ?",
            (kind, blob, jti),
        )
    else:
        await db.execute(
            """
            INSERT INTO form_drafts (jti, kind, officer_id, subject_id, guild_id, payload)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (jti, kind, officer_id, subject_id, guild_id, blob),
        )
    await db.commit()


async def load_draft(jti: str) -> dict[str, Any]:
    db = await get_db()
    row = await db.execute_fetchone("SELECT payload, kind FROM form_drafts WHERE jti = ?", (jti,))
    if not row:
        return {}
    raw = row.get("payload")
    if isinstance(raw, dict):
        data = raw
    else:
        try:
            data = json.loads(raw or "{}")
        except json.JSONDecodeError:
            data = {}
    if not isinstance(data, dict):
        data = {}
    data["_kind"] = row.get("kind")
    return data


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
