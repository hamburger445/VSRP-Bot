import asyncio
import discord
from discord import app_commands
from discord.ext import commands

from utils.embeds import build_embed, build_error_embed, build_success_embed
from utils.permissions import is_staff
from utils.core import reply, load_config, get_state, set_state
from utils.database import DatabaseError, DatabaseTimeoutError
from utils.verification import (
    account_age_days,
    build_verification_embed,
    is_verified_user,
    mark_verified,
    verification_channel_id,
)


async def _process_verification(interaction: discord.Interaction) -> None:
    channel_id = verification_channel_id()
    if channel_id and interaction.channel_id != channel_id:
        await reply(
            interaction,
            embed=build_error_embed(
                "Wrong Channel",
                "Please use `/verify` in the designated verification channel.",
                footer="WCRP Verification System",
            ),
            ephemeral=True,
        )
        return

    member = interaction.user
    if not isinstance(member, discord.Member):
        await reply(
            interaction,
            embed=build_error_embed(
                "Verification Failed",
                "This command must be used in a server member context.",
                footer="WCRP Verification System",
            ),
            ephemeral=True,
        )
        return

    if await is_verified_user(member):
        await reply(
            interaction,
            embed=build_embed(
                title="Already Verified",
                description="You are already verified and may continue to applications.",
                footer="WCRP Verification System",
            ),
            ephemeral=True,
        )
        return

    age = account_age_days(member)
    if age < 14:
        await reply(
            interaction,
            embed=build_error_embed(
                "Account Too New",
                "Your account must be at least 14 days old to verify. Please try again later.",
                footer="WCRP Verification System",
            ),
            ephemeral=True,
        )
        return

    try:
        dm_embed = build_embed(
            title="Verification in Progress",
            description=(
                "Thank you for beginning verification. You are being marked as verified for department access and applications.\n\n"
                "If your DMs are closed, please enable them temporarily and try again."
            ),
            footer="WCRP Verification System",
        )
        await member.send(embed=dm_embed)
    except discord.Forbidden:
        await reply(
            interaction,
            embed=build_error_embed(
                "DMs Disabled",
                "Please enable direct messages from server members temporarily so verification can continue.",
                footer="WCRP Verification System",
            ),
            ephemeral=True,
        )
        return

    await mark_verified(member.id)

    success_embed = build_success_embed(
        title="Verified Successfully",
        description=(
            "Your account has been verified. You can now access department information and civilian applications."
        ),
        footer="WCRP Verification System",
    )
    try:
        await member.send(embed=success_embed)
    except discord.HTTPException:
        pass

    await reply(
        interaction,
        embed=build_embed(
            title="Verified",
            description="Verification complete.",
            footer="WCRP Verification System",
        ),
        ephemeral=True,
    )


class Verification(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="verify", description="Verify your account to access departments and applications.")
    async def verify(self, interaction: discord.Interaction):
        await _process_verification(interaction)

    # verification-panel command removed — verification panel is auto-posted on startup.

    async def setup_persistent_views(self):
        pass

    async def post_panel(self):
        channel_id = verification_channel_id()
        guild_id = load_config().get("guild", {}).get("id")
        if not channel_id or not guild_id:
            return
        guild = self.bot.get_guild(int(guild_id))
        if not guild:
            return
        target = guild.get_channel(int(channel_id))
        if not isinstance(target, discord.TextChannel):
            return
        embed = build_verification_embed()
        state_key = f"verification_panel:{channel_id}"
        try:
            msg_id = await get_state(state_key)
            if msg_id:
                try:
                    msg = await target.fetch_message(int(msg_id))
                    try:
                        await msg.edit(embed=embed)
                        return
                    except discord.NotFound:
                        pass
                except (discord.NotFound, discord.HTTPException, ValueError):
                    pass
            async for message in target.history(limit=50):
                if message.author.id != self.bot.user.id or not message.embeds:
                    continue
                title = message.embeds[0].title or ""
                if title == embed.title:
                    try:
                        await message.edit(embed=embed)
                        await set_state(state_key, str(message.id))
                        return
                    except Exception:
                        break
            msg = await target.send(embed=embed)
            await set_state(state_key, str(msg.id))
        except (DatabaseError, DatabaseTimeoutError):
            try:
                await target.send(embed=embed)
            except Exception:
                pass

    async def _ensure_posted(self):
        await self.bot.wait_until_ready()
        try:
            await asyncio.sleep(1)
            await self.post_panel()
        except Exception:
            return

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        try:
            channel_id = payload.channel_id
            state_key = f"verification_panel:{channel_id}"
            msg_id = await get_state(state_key)
            if msg_id and str(payload.message_id) == str(msg_id):
                await self.post_panel()
        except Exception:
            return


async def setup(bot: commands.Bot):
    cog = Verification(bot)
    await bot.add_cog(cog)
    await cog.setup_persistent_views()
    bot.loop.create_task(cog._ensure_posted())
