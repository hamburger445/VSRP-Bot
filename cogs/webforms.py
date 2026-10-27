"""Start the paper-forms website next to the Discord gateway."""

from __future__ import annotations

import logging

from discord.ext import commands

from utils.form_server import start_form_server
from utils.form_urls import form_bind_port, public_form_base

log = logging.getLogger("vsrp_bot.webforms")


class WebForms(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._runner = None

    async def cog_load(self):
        self.bot.form_server_running = False
        try:
            self._runner = await start_form_server(self.bot)
            self.bot.form_server_running = True
            base = public_form_base() or f"(set PUBLIC_FORM_URL; listening on port {form_bind_port()})"
            log.info("Officers open forms at %s", base)
        except OSError:
            log.exception("Could not bind the forms website (port in use or not allocated)")
            self._runner = None

    async def cog_unload(self):
        if self._runner:
            await self._runner.cleanup()
            self._runner = None


async def setup(bot: commands.Bot):
    await bot.add_cog(WebForms(bot))
