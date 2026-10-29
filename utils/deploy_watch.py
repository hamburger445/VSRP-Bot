"""Watch GitHub for a new commit and exit so the panel script can re-pull."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import aiohttp

from utils.core import ROOT, get_github_repo, get_github_token

log = logging.getLogger("wcrp_bot.deploy")

DEPLOY_SHA_FILE = ROOT / ".deploy-sha"
RESTART_CODE = int(os.getenv("DEPLOY_EXIT_CODE", "42"))


def _branch() -> str:
    return os.getenv("GITHUB_BRANCH", "main").strip() or "main"


def _poll_seconds() -> int:
    raw = os.getenv("DEPLOY_POLL_SECONDS", "20")
    try:
        return max(10, int(raw))
    except ValueError:
        return 20


def watch_enabled() -> bool:
    from utils.health import on_render

    if on_render():
        return False
    flag = os.getenv("DEPLOY_WATCH", "").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return False
    if flag in {"1", "true", "yes", "on"}:
        return True
    # WispByte / Pterodactyl always live under /home/container
    if Path("/home/container").is_dir():
        return True
    return DEPLOY_SHA_FILE.exists()


def read_deployed_sha() -> str:
    if not DEPLOY_SHA_FILE.exists():
        return os.getenv("DEPLOY_SHA", "").strip()
    return DEPLOY_SHA_FILE.read_text(encoding="utf-8").strip()


async def fetch_head_sha() -> str | None:
    token = get_github_token()
    repo = get_github_repo()
    branch = _branch()
    url = f"https://api.github.com/repos/{repo}/commits/{branch}"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "wcrp-bot-deploy-watch",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status != 200:
                log.warning("Deploy watch: GitHub %s for %s@%s", resp.status, repo, branch)
                return None
            data = await resp.json()
    sha = (data or {}).get("sha")
    return str(sha) if sha else None


def request_restart(new_sha: str) -> None:
    log.warning("New GitHub commit %s — exiting %s so WispByte can pull and restart", new_sha[:12], RESTART_CODE)
    os._exit(RESTART_CODE)


async def deploy_watch_loop() -> None:
    if not watch_enabled():
        log.info("Deploy watch off (set DEPLOY_WATCH=1 or run via panel-start.sh)")
        return
    current = read_deployed_sha()
    interval = _poll_seconds()
    log.info("Deploy watch on (%ss) current=%s", interval, current[:12] or "(unknown)")
    await asyncio.sleep(8)
    while True:
        try:
            head = await fetch_head_sha()
        except Exception:
            log.exception("Deploy watch: failed to read GitHub")
        else:
            if head:
                if not current:
                    current = head
                elif head != current:
                    request_restart(head)
        await asyncio.sleep(interval)
