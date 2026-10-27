"""Document-editor links and Discord approve/reject buttons. HTTP is Render, not WispByte."""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from utils.form_store import get_review, set_review_status
from utils.form_urls import public_form_base
from utils.permissions import is_leo, is_staff

log = logging.getLogger("wcrp_bot.webforms")


class WebForms(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        base = public_form_base()
        self.bot.form_server_running = bool(base)
        if base:
            log.info("Document editor: %s", base)
        else:
            log.warning("No public HTTPS URL yet. On Render this is RENDER_EXTERNAL_URL after the first deploy.")

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        cid = ""
        if interaction.data:
            cid = str(interaction.data.get("custom_id") or "")
        if not (cid.startswith("wcrpdoc:") or cid.startswith("vsrpdoc:")):
            return
        parts = cid.split(":")
        if len(parts) != 3:
            return
        _, action, raw_id = parts
        if not is_leo(interaction.user) and not is_staff(interaction.user):
            await interaction.response.send_message("Only law enforcement or staff can review documents.", ephemeral=True)
            return
        try:
            review_id = int(raw_id)
        except ValueError:
            return
        row = await get_review(review_id)
        if not row:
            await interaction.response.send_message("That submission was not found.", ephemeral=True)
            return
        if row.get("status") != "pending":
            await interaction.response.send_message(f"Already {row.get('status')}.", ephemeral=True)
            return
        status = "approved" if action == "approve" else "rejected"
        updated = await set_review_status(review_id, status, interaction.user.id)
        if not updated or updated.get("status") != status:
            await interaction.response.send_message("Could not update this review.", ephemeral=True)
            return
        color = 0x27AE60 if status == "approved" else 0x992D22
        embed = discord.Embed(
            title=f"Document {status}",
            description=f"Case **#{row.get('case_number')}** ({row.get('kind')}) was **{status}** by {interaction.user.mention}.",
            color=color,
        )
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(WebForms(bot))
