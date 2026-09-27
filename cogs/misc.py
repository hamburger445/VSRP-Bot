import re

import discord
from discord import app_commands
from discord.ext import commands

from utils.core import reply
from utils.permissions import is_owner

SIXTY_SEVEN = re.compile(r"\b67\b")


class Misc(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        if not message.content:
            return
        if SIXTY_SEVEN.search(message.content):
            await message.channel.send("It's 2026 and you're saying 67. Lock in bro")

    @app_commands.command(name="say", description="Make the bot say something (owners only)")
    @app_commands.describe(
        message="What the bot should say",
        channel="Channel to send in (defaults to current channel)",
    )
    async def say(
        self,
        interaction: discord.Interaction,
        message: str,
        channel: discord.TextChannel | None = None,
    ):
        if not is_owner(interaction.user):
            await reply(interaction, "Owners only.", ephemeral=True)
            return

        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await reply(interaction, "Could not determine a text channel.", ephemeral=True)
            return

        await target.send(message)
        await reply(
            interaction,
            f"Message sent in {target.mention}.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Misc(bot))
