from datetime import datetime, timezone

from utils.database import get_db


def _now() -> datetime:
    return datetime.now(timezone.utc)


def format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h {minutes}m"


def _session_duration_seconds(session: dict, now: datetime | None = None) -> int:
    now = now or _now()
    started = session["started_at"]
    if isinstance(started, datetime) and started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)

    if session.get("ended_at"):
        ended = session["ended_at"]
        if isinstance(ended, datetime) and ended.tzinfo is None:
            ended = ended.replace(tzinfo=timezone.utc)
    else:
        ended = now

    break_seconds = int(session.get("break_seconds") or 0)
    if session.get("status") == "on_break" and session.get("break_started_at"):
        bs = session["break_started_at"]
        if isinstance(bs, datetime) and bs.tzinfo is None:
            bs = bs.replace(tzinfo=timezone.utc)
        break_seconds += int((now - bs).total_seconds())

    adjust = int(session.get("adjust_seconds") or 0)
    raw = int((ended - started).total_seconds()) - break_seconds + adjust
    return max(0, raw)


async def get_active_session(guild_id: int, user_id: int) -> dict | None:
    db = await get_db()
    return await db.execute_fetchone(
        """
        SELECT * FROM shift_sessions
        WHERE guild_id = ? AND user_id = ? AND deleted = 0
          AND ended_at IS NULL AND status IN ('active', 'on_break')
        ORDER BY id DESC LIMIT 1
        """,
        (guild_id, user_id),
    )


async def get_user_stats(guild_id: int, user_id: int) -> dict:
    db = await get_db()
    active = await get_active_session(guild_id, user_id)
    rows = await db.execute_fetchall(
        """
        SELECT * FROM shift_sessions
        WHERE guild_id = ? AND user_id = ? AND deleted = 0 AND ended_at IS NOT NULL
        ORDER BY ended_at DESC
        """,
        (guild_id, user_id),
    )
    durations = [_session_duration_seconds(row) for row in rows]
    total = sum(durations)
    count = len(durations)
    last = durations[0] if durations else 0
    average = int(total / count) if count else 0

    if active:
        status = "On Break" if active["status"] == "on_break" else "On Shift"
    else:
        status = "Off Duty"

    return {
        "status": status,
        "total_seconds": total,
        "last_seconds": last,
        "average_seconds": average,
        "active_session": active,
        "shift_count": count,
    }


async def start_shift(guild_id: int, user_id: int, *, admin_id: int | None = None) -> tuple[bool, str]:
    active = await get_active_session(guild_id, user_id)
    if active:
        return False, "That user already has an active shift."

    db = await get_db()
    await db.execute(
        """
        INSERT INTO shift_sessions (guild_id, user_id, status, started_at, started_by)
        VALUES (?, ?, 'active', ?, ?)
        """,
        (guild_id, user_id, _now(), admin_id),
    )
    await db.commit()
    return True, "Shift started."


async def stop_shift(guild_id: int, user_id: int, *, admin_id: int | None = None) -> tuple[bool, str]:
    active = await get_active_session(guild_id, user_id)
    if not active:
        return False, "No active shift to stop."

    now = _now()
    break_seconds = int(active.get("break_seconds") or 0)
    if active["status"] == "on_break" and active.get("break_started_at"):
        bs = active["break_started_at"]
        if isinstance(bs, datetime) and bs.tzinfo is None:
            bs = bs.replace(tzinfo=timezone.utc)
        break_seconds += int((now - bs).total_seconds())

    duration = _session_duration_seconds({**active, "break_seconds": break_seconds, "ended_at": now}, now)
    db = await get_db()
    await db.execute(
        """
        UPDATE shift_sessions
        SET status = 'ended', ended_at = ?, break_seconds = ?, break_started_at = NULL,
            ended_by = ?
        WHERE id = ?
        """,
        (now, break_seconds, admin_id, active["id"]),
    )
    await db.commit()
    return True, f"Shift ended. Duration: {format_duration(duration)}."


async def toggle_break(guild_id: int, user_id: int, *, admin_id: int | None = None) -> tuple[bool, str]:
    active = await get_active_session(guild_id, user_id)
    if not active:
        return False, "Start a shift before taking a break."

    db = await get_db()
    now = _now()
    if active["status"] == "on_break":
        bs = active.get("break_started_at")
        extra = 0
        if bs:
            if isinstance(bs, datetime) and bs.tzinfo is None:
                bs = bs.replace(tzinfo=timezone.utc)
            extra = int((now - bs).total_seconds())
        new_break = int(active.get("break_seconds") or 0) + extra
        await db.execute(
            """
            UPDATE shift_sessions
            SET status = 'active', break_seconds = ?, break_started_at = NULL
            WHERE id = ?
            """,
            (new_break, active["id"]),
        )
        await db.commit()
        return True, "Break ended. Back on shift."
    else:
        await db.execute(
            """
            UPDATE shift_sessions SET status = 'on_break', break_started_at = ?
            WHERE id = ?
            """,
            (now, active["id"]),
        )
        await db.commit()
        return True, "Break started."


async def list_active_shifts(guild_id: int) -> list[dict]:
    db = await get_db()
    return await db.execute_fetchall(
        """
        SELECT * FROM shift_sessions
        WHERE guild_id = ? AND deleted = 0 AND ended_at IS NULL
          AND status IN ('active', 'on_break')
        ORDER BY started_at ASC
        """,
        (guild_id,),
    )


async def list_user_shifts(guild_id: int, user_id: int, limit: int = 25) -> list[dict]:
    db = await get_db()
    return await db.execute_fetchall(
        """
        SELECT * FROM shift_sessions
        WHERE guild_id = ? AND user_id = ? AND deleted = 0
        ORDER BY started_at DESC LIMIT ?
        """,
        (guild_id, user_id, limit),
    )


async def delete_shift(shift_id: int) -> tuple[bool, str]:
    db = await get_db()
    row = await db.execute_fetchone("SELECT id FROM shift_sessions WHERE id = ? AND deleted = 0", (shift_id,))
    if not row:
        return False, "Shift not found."
    await db.execute("UPDATE shift_sessions SET deleted = 1 WHERE id = ?", (shift_id,))
    await db.commit()
    return True, f"Shift #{shift_id} deleted."


async def edit_shift_duration(shift_id: int, minutes: int) -> tuple[bool, str]:
    db = await get_db()
    row = await db.execute_fetchone("SELECT * FROM shift_sessions WHERE id = ? AND deleted = 0", (shift_id,))
    if not row:
        return False, "Shift not found."
    adjust = int(minutes) * 60
    await db.execute(
        "UPDATE shift_sessions SET adjust_seconds = ? WHERE id = ?",
        (adjust, shift_id),
    )
    await db.commit()
    return True, f"Shift #{shift_id} duration adjusted by {minutes} minute(s)."


async def get_shift(shift_id: int) -> dict | None:
    db = await get_db()
    return await db.execute_fetchone(
        "SELECT * FROM shift_sessions WHERE id = ? AND deleted = 0",
        (shift_id,),
    )
