import asyncio

import logging

import sys

import discord
from discord import app_commands
from discord.ext import commands

from utils.core import (
    all_guild_ids,
    all_guild_objects,
    format_sync_summary,
    get_token,
    load_config,
    materialize_config_from_example,
    main_guild_objects,
    pd_guild_objects,
    restrict_cog_guilds,
    shift_guild_objects,
    sync_app_commands,
)
from utils.database import close_db, get_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("vsrp_bot")

COGS = [
    "cogs.erlc_status",
    "cogs.sessions",
    "cogs.profiles",
    "cogs.leo",
    "cogs.admin",
    "cogs.economy",
    "cogs.blackjack",
    "cogs.shop",
    "cogs.owner_panel",
    "cogs.reaction_roles",
    "cogs.tickets",
    "cogs.info_panel",
    "cogs.department_pings",
    "cogs.verification",
    "cogs.applications",
    "cogs.moderation",
    "cogs.help",
    "cogs.autorole",
    "cogs.misc",
    "cogs.shifts",
    "cogs.pd_setup",
    "cogs.setup",
]

MAIN_ONLY_MODULES = frozenset({
    "cogs.erlc_status",
    "cogs.sessions",
    "cogs.profiles",
    "cogs.leo",
    "cogs.admin",
    "cogs.economy",
    "cogs.blackjack",
    "cogs.shop",
    "cogs.owner_panel",
    "cogs.reaction_roles",
    "cogs.tickets",
    "cogs.info_panel",
    "cogs.department_pings",
    "cogs.verification",
    "cogs.applications",
    "cogs.autorole",
})

SHIFT_ONLY_MODULES = frozenset({"cogs.shifts"})
PD_ONLY_MODULES = frozenset({"cogs.pd_setup"})

ALL_GUILD_MODULES = frozenset({
    "cogs.moderation",
    "cogs.help",
    "cogs.misc",
    "cogs.setup",
})

ACTIVITY_TYPES = {
    "playing": discord.ActivityType.playing,
    "watching": discord.ActivityType.watching,
    "listening": discord.ActivityType.listening,
    "competing": discord.ActivityType.competing,
}


class VSRPBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.reactions = True
        self.config = load_config()
        prefix = self.config.get("bot", {}).get("prefix", "-")
        super().__init__(command_prefix=prefix, intents=intents)

    async def setup_hook(self):
        await get_db()

        @self.tree.error
        async def on_tree_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
            if isinstance(error, app_commands.CommandNotFound):
                msg = (
                    "That command is outdated or unavailable. "
                    "Try `/leo-panel`, `/citation`, `/pager send`, or `/profile`."
                )
                try:
                    if interaction.response.is_done():
                        await interaction.followup.send(msg, ephemeral=True)
                    else:
                        await interaction.response.send_message(msg, ephemeral=True)
                except discord.HTTPException:
                    pass
                return
            log.exception("Slash command error", exc_info=error)

        failed_cogs: list[str] = []
        for cog in COGS:
            try:
                await self.load_extension(cog)
                log.info("Loaded cog: %s", cog)
            except Exception:
                failed_cogs.append(cog)
                log.exception("Failed to load cog: %s", cog)

        if failed_cogs:
            log.error("Cogs that failed to load: %s", ", ".join(failed_cogs))

        for cog in self.cogs.values():
            mod = cog.__module__
            if mod in MAIN_ONLY_MODULES:
                restrict_cog_guilds(self, cog, main_guild_objects())
            elif mod in SHIFT_ONLY_MODULES:
                restrict_cog_guilds(self, cog, shift_guild_objects())
            elif mod in PD_ONLY_MODULES:
                restrict_cog_guilds(self, cog, pd_guild_objects())
            elif mod in ALL_GUILD_MODULES:
                restrict_cog_guilds(self, cog, all_guild_objects())

        registered = []
        for gid in all_guild_ids():
            g = discord.Object(id=gid)
            registered.extend(f"{cmd.name}@{gid}" for cmd in self.tree.get_commands(guild=g))
        log.info("Guild-scoped slash commands: %s", ", ".join(sorted(set(registered))) or "(none)")

        results = await self._sync_commands(all_guild_ids())
        if results is not None:
            log.info("Startup slash sync: %s", format_sync_summary(results).replace("**", ""))
        self._resynced_commands = True

    async def _sync_commands(self, guild_ids: list[int]) -> dict[int, list[str]] | None:
        try:
            return await sync_app_commands(self.tree, guild_ids)
        except Exception:
            log.exception("Slash command sync failed")
            return None

    async def on_ready(self):
        if not getattr(self, "_resynced_commands", False):
            self._resynced_commands = True
            await self._sync_commands(all_guild_ids())

        bot_cfg = self.config.get("bot", {})
        activity_name = bot_cfg.get("activity_name", "VSRP Sessions")
        activity_type = ACTIVITY_TYPES.get(
            bot_cfg.get("activity_type", "watching"), discord.ActivityType.watching
        )
        await self.change_presence(
            activity=discord.Activity(type=activity_type, name=activity_name)
        )

        log.info("Logged in as %s (%s)", self.user, self.user.id)

        from utils.permissions import load_guild_permissions

        for gid in all_guild_ids():
            try:
                await load_guild_permissions(gid)
            except Exception:
                log.exception("Failed to preload permissions for guild %s", gid)

        if not getattr(self, "_expiry_task_started", False):
            self._expiry_task_started = True
            self.loop.create_task(self._expiry_loop())

        if not getattr(self, "_shift_ban_infra_started", False):
            self._shift_ban_infra_started = True
            self.loop.create_task(self._provision_shift_ban_infrastructure())

    async def _provision_shift_ban_infrastructure(self) -> None:
        from utils.soft_ban import provision_shift_ban_infrastructure

        await self.wait_until_ready()
        try:
            await provision_shift_ban_infrastructure(self)
        except Exception:
            log.exception("Shift soft-ban infrastructure provisioning failed")

    async def _expiry_loop(self):
        from utils.users import process_expirations

        await self.wait_until_ready()
        while not self.is_closed():
            try:
                await process_expirations(self)
            except Exception:
                log.exception("Expiration check failed")
            await asyncio.sleep(3600)

    async def close(self):
        await close_db()
        await super().close()


async def main():
    materialize_config_from_example()
    bot = VSRPBot()
    async with bot:
        await bot.start(get_token())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Bot stopped.")
    except ValueError as exc:
        log.error("%s", exc)
        sys.exit(1)
    except discord.LoginFailure:
        log.error(
            "Discord rejected the bot token (401 Unauthorized). "
            "Reset your token in the Developer Portal and update DISCORD_TOKEN."
        )
        sys.exit(1)
