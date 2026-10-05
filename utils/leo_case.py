"""Sequential LEO case numbers (0000+)."""

from __future__ import annotations

from utils.core import get_state, set_state

_CASE_STATE_KEY = "leo_case_sequence"


async def allocate_case_number() -> str:
    raw = await get_state(_CASE_STATE_KEY)
    try:
        n = int(raw) if raw else 0
    except ValueError:
        n = 0
    n += 1
    await set_state(_CASE_STATE_KEY, str(n))
    return f"{n:04d}"


def format_case_number(value: str | int | None) -> str:
    if value is None:
        return "0000"
    if isinstance(value, int):
        return f"{value:04d}"
    s = str(value).strip()
    if s.isdigit():
        return f"{int(s):04d}"
    return s[:16]
