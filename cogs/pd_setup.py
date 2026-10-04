import discord
from discord import app_commands
from discord.ext import commands

from utils.core import defer, pd_guild_id, reply
from utils.wpd_setup import run_wpd_setup


class PDSetup(commands.Cog):
    pd = app_commands.Group(name="pd", description="Wytheville Police Department server tools")

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @pd.command(
        name="setup",
        description="Create or update the Wytheville Police Department server structure",
    )
    async def pd_setup(self, interaction: discord.Interaction):
        pd_id = pd_guild_id()
        if not interaction.guild or not pd_id or interaction.guild_id != pd_id:
            await reply(
                interaction,
                "This command can only be used on the **City PD** server.",
                ephemeral=True,
            )
            return
        if not isinstance(interaction.user, discord.Member):
            await reply(interaction, "Run this in a server.", ephemeral=True)
            return
        if not interaction.user.guild_permissions.administrator:
            await reply(interaction, "You need **Administrator** permission to run WPD setup.", ephemeral=True)
            return

        await defer(interaction)
        report = await run_wpd_setup(interaction.guild)
        await interaction.followup.send(embed=report.to_embed(), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(PDSetup(bot))
