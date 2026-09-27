import logging
from typing import Any

import aiohttp
import discord

from utils.core import format_datetime

log = logging.getLogger("vsrp_bot.erlc")

ERLC_API_URL = "https://api.erlc.gg/v2/server"


async def fetch_server_data(server_key: str, *, include_queue: bool = True) -> dict[str, Any]:
    params = {"Queue": "true"} if include_queue else {}
    headers = {"server-key": server_key}
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(ERLC_API_URL, headers=headers, params=params) as resp:
            if resp.status == 403:
                raise PermissionError("ERLC API rejected the server key.")
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"ERLC API returned {resp.status}: {body[:200]}")
            return await resp.json()


def build_server_embed(data: dict[str, Any]) -> discord.Embed:
    name = data.get("Name", "Unknown")
    current = data.get("CurrentPlayers", 0)
    maximum = data.get("MaxPlayers", 0)
    join_key = data.get("JoinKey", "N/A")
    verified = data.get("AccVerifiedReq", "Unknown")
    team_balance = "Enabled" if data.get("TeamBalance") else "Disabled"
    queue = data.get("Queue") or []
    queue_count = len(queue)

    embed = discord.Embed(
        title=f"ER:LC Server | {name}",
        color=0x3498DB,
    )
    embed.add_field(name="Players", value=f"{current}/{maximum}", inline=True)
    embed.add_field(name="Join Code", value=f"`{join_key}`", inline=True)
    embed.add_field(name="Queue", value=str(queue_count), inline=True)
    embed.add_field(name="Verification", value=str(verified), inline=True)
    embed.add_field(name="Team Balance", value=team_balance, inline=True)
    embed.add_field(name="Owner ID", value=str(data.get("OwnerId", "N/A")), inline=True)

    co_owners = data.get("CoOwnerIds") or []
    if co_owners:
        embed.add_field(
            name="Co-Owners",
            value=", ".join(f"`{owner_id}`" for owner_id in co_owners[:10]),
            inline=False,
        )

    embed.set_footer(text=f"Last updated {format_datetime(discord.utils.utcnow())}")
    return embed


def build_error_embed(message: str) -> discord.Embed:
    return discord.Embed(
        title="ER:LC Server Status",
        description=message,
        color=0xE74C3C,
    ).set_footer(text="Will retry on next update")
