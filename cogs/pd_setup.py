import discord
from discord import app_commands
from discord.ext import commands

from utils.core import defer, pd_guild_id
from utils.wpd_setup import run_wpd_setup


class PDSetup(commands.Cog):
    pd = app_commands.Group(name="pd", description="Wytheville Police Department server tools")

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @pd.command(
        name="setup",
        description="⚠️ Wipe and rebuild the PD server from the WSP template (WPD names)",
    )
    async def pd_setup(self, interaction: discord.Interaction):
        pd_id = pd_guild_id()
        if not interaction.guild or not pd_id or interaction.guild_id != pd_id:
            await interaction.response.send_message(
                "This command can only be used on the **City PD** server.",
                ephemeral=True,
            )
            return
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Run this in a server.", ephemeral=True)
            return
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "You need **Administrator** permission to run WPD setup. Nothing was changed.",
                ephemeral=True,
            )
            return

        try:
            await defer(interaction)
        except discord.NotFound:
            return

        report = await run_wpd_setup(interaction.guild)
        await interaction.followup.send(embed=report.to_embed(), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(PDSetup(bot))
