"""Mirror bot data to JSON files in the GitHub repo (backup / secondary store)."""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

import aiohttp

from utils.core import get_github_repo, get_github_token

log = logging.getLogger("wcrp_bot.github")
_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=45, connect=15)
_known_branches: set[str] = set()
_file_sha: dict[tuple[str, str], str] = {}


def _enabled() -> bool:
    return bool(get_github_token())


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _api_url(path: str, branch: str | None = None) -> str:
    repo = get_github_repo()
    encoded_path = "/".join(path.strip("/").split("/"))
    return f"https://api.github.com/repos/{repo}/contents/{encoded_path}?ref={branch or get_github_repo_branch()}"


def get_github_repo_branch() -> str:
    from utils.core import load_config

    return load_config().get("github", {}).get("branch", "main")


async def ensure_github_branch(branch: str) -> bool:
    token = get_github_token()
    if not token or not branch:
        return False
    if branch in _known_branches:
        return True
    repo = get_github_repo()
    headers = _headers(token)
    base = f"https://api.github.com/repos/{repo}/git/ref/heads"
    async with aiohttp.ClientSession(timeout=_HTTP_TIMEOUT) as session:
        async with session.get(f"{base}/{branch}", headers=headers) as resp:
            if resp.status == 200:
                _known_branches.add(branch)
                return True
        source = get_github_repo_branch()
        async with session.get(f"{base}/{source}", headers=headers) as resp:
            if resp.status != 200:
                log.warning("GitHub could not read branch %s (%s)", source, resp.status)
                return False
            sha = (await resp.json())["object"]["sha"]
        async with session.post(
            f"https://api.github.com/repos/{repo}/git/refs",
            headers=headers,
            json={"ref": f"refs/heads/{branch}", "sha": sha},
        ) as resp:
            if resp.status in (201, 422):
                _known_branches.add(branch)
                return True
            text = await resp.text()
            log.warning("GitHub create branch %s failed (%s): %s", branch, resp.status, text[:200])
            return False


async def github_get_json(relative_path: str, *, branch: str | None = None) -> dict[str, Any] | None:
    token = get_github_token()
    if not token:
        return None

    url = _api_url(relative_path, branch)
    headers = _headers(token)
    async with aiohttp.ClientSession(timeout=_HTTP_TIMEOUT) as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 404:
                return None
            if resp.status != 200:
                body = await resp.text()
                log.warning("GitHub GET %s failed (%s): %s", relative_path, resp.status, body[:200])
                return None
            payload = await resp.json()
    sha = payload.get("sha")
    if sha:
        _file_sha[(relative_path, branch or get_github_repo_branch())] = sha
    try:
        raw = base64.b64decode(payload["content"]).decode("utf-8")
        return json.loads(raw)
    except (KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        log.warning("GitHub JSON decode failed for %s: %s", relative_path, exc)
        return None


async def github_put_json(
    relative_path: str,
    data: dict[str, Any],
    *,
    message: str,
    branch: str | None = None,
) -> bool:
    token = get_github_token()
    if not token:
        return False

    target_branch = branch or get_github_repo_branch()
    if branch:
        await ensure_github_branch(target_branch)

    content_bytes = json.dumps(data, indent=2, sort_keys=True).encode("utf-8")
    content_b64 = base64.b64encode(content_bytes).decode("ascii")

    headers = _headers(token)

    url = _api_url(relative_path, target_branch)
    sha = _file_sha.get((relative_path, target_branch))
    async with aiohttp.ClientSession(timeout=_HTTP_TIMEOUT) as session:
        if not sha:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    existing = await resp.json()
                    sha = existing.get("sha")
                    if sha:
                        _file_sha[(relative_path, target_branch)] = sha

        body: dict[str, Any] = {
            "message": message,
            "content": content_b64,
            "branch": target_branch,
        }
        if sha:
            body["sha"] = sha

        put_url = _api_url(relative_path).split("?")[0]
        async with session.put(put_url, headers=headers, json=body) as resp:
            if resp.status in (200, 201):
                payload = await resp.json()
                new_sha = (payload.get("content") or {}).get("sha")
                if new_sha:
                    _file_sha[(relative_path, target_branch)] = new_sha
                return True
            text = await resp.text()
            log.warning("GitHub PUT %s failed (%s): %s", relative_path, resp.status, text[:300])
            if resp.status in (409, 422):
                _file_sha.pop((relative_path, target_branch), None)
            return False


def guild_permissions_path(guild_id: int) -> str:
    from utils.core import load_config

    base = load_config().get("github", {}).get("data_path", "data")
    return f"{base}/guild_permissions/{guild_id}.json"


async def sync_guild_permissions_to_github(guild_id: int, permissions: dict[str, list[int]]) -> None:
    if not _enabled():
        return
    path = guild_permissions_path(guild_id)
    payload = {"guild_id": guild_id, "permissions": permissions}
    ok = await github_put_json(
        path,
        payload,
        message=f"Update guild permissions for {guild_id}",
    )
    if ok:
        log.info("Synced guild %s permissions to GitHub", guild_id)


async def load_guild_permissions_from_github(guild_id: int) -> dict[str, list[int]] | None:
    if not _enabled():
        return None
    raw = await github_get_json(guild_permissions_path(guild_id))
    if not raw:
        return None
    perms = raw.get("permissions")
    if isinstance(perms, dict):
        return perms
    return None
