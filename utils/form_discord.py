"""Discord REST helpers for the standalone forms website (no gateway)."""

from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp

from utils.core import get_token, load_config

log = logging.getLogger("wcrp_bot.form_discord")
API = "https://discord.com/api/v10"


class FormUser:
    def __init__(self, user_id: int, name: str):
        self.id = int(user_id)
        self.display_name = name or str(user_id)
        self.mention = f"<@{self.id}>"

    def __str__(self) -> str:
        return self.display_name


def _headers() -> dict[str, str] | None:
    try:
        token = get_token()
    except ValueError:
        return None
    return {"Authorization": f"Bot {token}", "User-Agent": "WCRP-Forms"}


async def discord_user(user_id: int) -> dict[str, Any]:
    headers = _headers()
    if not headers:
        return {"id": str(user_id), "username": str(user_id), "global_name": None}
    timeout = aiohttp.ClientTimeout(total=4)
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async with session.get(f"{API}/users/{int(user_id)}") as resp:
            if resp.status != 200:
                return {"id": str(user_id), "username": str(user_id), "global_name": None}
            return await resp.json()


async def discord_member(guild_id: int, user_id: int) -> dict[str, Any]:
    headers = _headers()
    if not headers:
        return {}
    timeout = aiohttp.ClientTimeout(total=4)
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async with session.get(f"{API}/guilds/{int(guild_id)}/members/{int(user_id)}") as resp:
            if resp.status != 200:
                return {}
            return await resp.json()


def display_name(user: dict[str, Any], member: dict[str, Any] | None = None) -> str:
    if member:
        nick = member.get("nick")
        if nick:
            return str(nick)
        u = member.get("user") or {}
        return str(u.get("global_name") or u.get("username") or user.get("username") or "")
    return str(user.get("global_name") or user.get("username") or user.get("id") or "")


async def post_channel_message(
    channel_id: int,
    *,
    content: str = "",
    embed: dict[str, Any] | None = None,
    filename: str | None = None,
    file_bytes: bytes | None = None,
    components: list | None = None,
) -> dict[str, Any] | None:
    payload: dict[str, Any] = {}
    if content:
        payload["content"] = content
    if embed:
        payload["embeds"] = [embed]
    if components:
        payload["components"] = components
    timeout = aiohttp.ClientTimeout(total=20)
    headers = _headers()
    if not headers:
        log.warning("No Discord token; cannot post form to channel")
        return None
    async with aiohttp.ClientSession(timeout=timeout) as session:
        if file_bytes and filename:
            form = aiohttp.FormData()
            form.add_field("payload_json", json.dumps(payload), content_type="application/json")
            form.add_field("files[0]", file_bytes, filename=filename, content_type="application/pdf")
            async with session.post(f"{API}/channels/{int(channel_id)}/messages", data=form, headers=headers) as resp:
                if resp.status not in (200, 201):
                    log.warning("Discord upload %s: %s", resp.status, (await resp.text())[:300])
                    return None
                return await resp.json()
        headers = {**headers, "Content-Type": "application/json"}
        async with session.post(
            f"{API}/channels/{int(channel_id)}/messages",
            json=payload,
            headers=headers,
        ) as resp:
            if resp.status not in (200, 201):
                log.warning("Discord message %s: %s", resp.status, (await resp.text())[:300])
                return None
            return await resp.json()


def review_components(review_id: int) -> list:
    return [
        {
            "type": 1,
            "components": [
                {
                    "type": 2,
                    "style": 3,
                    "label": "Approve",
                    "custom_id": f"wcrpdoc:approve:{int(review_id)}",
                },
                {
                    "type": 2,
                    "style": 4,
                    "label": "Reject",
                    "custom_id": f"wcrpdoc:reject:{int(review_id)}",
                },
            ],
        }
    ]


def channel_id_for(key: str) -> int | None:
    raw = load_config().get("channels", {}).get(key)
    try:
        return int(raw) if raw else None
    except (TypeError, ValueError):
        return None
