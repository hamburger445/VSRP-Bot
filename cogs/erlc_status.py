import asyncio
import logging

import discord
from discord.ext import commands

from utils.core import get_state, get_erlc_server_key, load_config, set_state
from utils.database import DatabaseError, DatabaseTimeoutError
from utils.erlc import build_error_embed, build_server_embed, fetch_server_data

log = logging.getLogger("vsrp_bot.erlc_status")


class ErlcStatus(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._task: asyncio.Task | None = None

    def _cfg(self) -> dict:
        return load_config().get("erlc", {})

    def _channel_id(self) -> int | None:
        channel_id = self._cfg().get("channel")
        if channel_id:
            return int(channel_id)
        return load_config().get("channels", {}).get("session_announcements")

    def _interval(self) -> int:
        return max(30, int(self._cfg().get("update_interval_seconds", 60)))

    def _state_key(self, channel_id: int) -> str:
        return f"erlc_status_message:{channel_id}"

    async def cog_load(self):
        if not get_erlc_server_key():
            log.warning("ERLC_SERVER_KEY not set; server status updates disabled.")
            return
        self._task = asyncio.get_running_loop().create_task(self._update_loop())

    async def cog_unload(self):
        if self._task:
            self._task.cancel()

    async def _update_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                await self._refresh_status()
            except asyncio.CancelledError:
                raise
            except (DatabaseError, DatabaseTimeoutError) as exc:
                log.warning("ERLC status skipped (database unavailable): %s", exc)
            except discord.HTTPException as exc:
                log.warning("ERLC status skipped (Discord unavailable): %s", exc)
            except Exception:
                log.exception("ERLC status update failed")
            await asyncio.sleep(self._interval())

    async def _load_message_id(self, state_key: str) -> str | None:
        try:
            return await get_state(state_key)
        except (DatabaseError, DatabaseTimeoutError) as exc:
            log.warning("Could not load ERLC message id from database: %s", exc)
            return None

    async def _save_message_id(self, state_key: str, message_id: int) -> None:
        try:
            await set_state(state_key, str(message_id))
        except (DatabaseError, DatabaseTimeoutError) as exc:
            log.warning("Could not save ERLC message id to database: %s", exc)

    async def _find_existing_status_message(self, channel: discord.TextChannel) -> discord.Message | None:
        state_key = self._state_key(self._channel_id() or 0)
        message_id_raw = await self._load_message_id(state_key)
        if message_id_raw:
            try:
                return await channel.fetch_message(int(message_id_raw))
            except (discord.NotFound, discord.HTTPException, ValueError):
                pass

        try:
            async for message in channel.history(limit=25):
                if message.author.id != self.bot.user.id or not message.embeds:
                    continue
                title = message.embeds[0].title or ""
                if title.startswith("ER:LC Server"):
                    return message
        except (discord.HTTPException, AttributeError):
            pass
        return None

    async def _refresh_status(self):
        if not self.bot.is_ready():
            return

        server_key = get_erlc_server_key()
        channel_id = self._channel_id()
        if not server_key or not channel_id:
            return

        guild_id = load_config().get("guild", {}).get("id")
        guild = self.bot.get_guild(guild_id) if guild_id else None
        if not guild:
            return

        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            log.warning("ERLC status channel %s not found or not a text channel.", channel_id)
            return

        try:
            data = await fetch_server_data(server_key)
            embed = build_server_embed(data)
        except Exception as exc:
            log.warning("ERLC fetch failed: %s", exc)
            embed = build_error_embed(f"Could not fetch server data.\n`{exc}`")

        state_key = self._state_key(channel_id)
        message = await self._find_existing_status_message(channel)

        if message is not None:
            try:
                await message.edit(embed=embed)
                await self._save_message_id(state_key, message.id)
                return
            except discord.NotFound:
                log.info("ERLC status message was deleted; creating a new one.")
            except discord.HTTPException as exc:
                log.warning("Could not edit ERLC status message: %s", exc)
                return

        message = await channel.send(embed=embed)
        await self._save_message_id(state_key, message.id)


async def setup(bot: commands.Bot):
    await bot.add_cog(ErlcStatus(bot))
