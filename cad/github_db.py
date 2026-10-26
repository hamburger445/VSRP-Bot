"""Persist CAD tables as JSON in the GitHub repo (Contents API)."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from cad.github_sql import dump_state, load_state, run
from utils.core import get_github_token
from utils.github_store import github_get_json, github_put_json

log = logging.getLogger("vsrp_bot.cad.github_db")

_lock = asyncio.Lock()
_state: dict[str, Any] | None = None
_dirty = False


def db_path() -> str:
    return os.getenv("CAD_GITHUB_PATH", "data/cad/db.json")


def github_enabled() -> bool:
    mode = os.getenv("CAD_DATABASE", "auto").strip().lower()
    if mode in {"github", "gh", "json"}:
        return True
    if mode in {"postgres", "postgresql", "neon"}:
        return False
    return bool(get_github_token())


async def _ensure_loaded() -> None:
    global _state
    if _state is not None:
        return
    raw = await github_get_json(db_path())
    _state = load_state(raw if isinstance(raw, dict) else {})
    log.info("CAD GitHub database loaded (%s tables)", len(_state.get("tables") or {}))


async def persist() -> None:
    global _dirty
    if not _dirty or _state is None:
        return
    ok = await github_put_json(
        db_path(),
        dump_state(_state),
        message="Update CAD JSON database",
    )
    if ok:
        _dirty = False
        log.info("CAD GitHub database saved")
    else:
        log.warning("CAD GitHub database save failed")


class GithubConn:
    def transaction(self):
        return _NullTx()

    async def fetchval(self, sql: str, *args):
        await _ensure_loaded()
        return run(_state, sql, args, "fetchval")

    async def fetchrow(self, sql: str, *args):
        await _ensure_loaded()
        return run(_state, sql, args, "fetchrow")

    async def fetch(self, sql: str, *args):
        await _ensure_loaded()
        return run(_state, sql, args, "fetch")

    async def execute(self, sql: str, *args):
        global _dirty
        await _ensure_loaded()
        result = run(_state, sql, args, "execute")
        if str(sql).lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            _dirty = True
        return result


class _NullTx:
    async def __aenter__(self):
        return GithubConn()

    async def __aexit__(self, *_exc):
        return False


@asynccontextmanager
async def github_connection():
    async with _lock:
        await _ensure_loaded()
        conn = GithubConn()
        try:
            yield conn
        finally:
            await persist()
