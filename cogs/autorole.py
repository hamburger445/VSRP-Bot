import asyncio
import logging

import discord
from discord.ext import commands

from utils.core import is_main_guild, load_config
from utils.verification import pending_verify_role_id, verification_channel_id

log = logging.getLogger("vsrp_bot.autorole")


class AutoRole(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._synced = False

    def _member_role_id(self) -> int | None:
        role_id = load_config().get("roles", {}).get("member")
        return int(role_id) if role_id else None

    def _application_pending_role_id(self) -> int | None:
        role_id = load_config().get("applications", {}).get("pending_role_id")
        return int(role_id) if role_id else None

    def _guild_id(self) -> int | None:
        guild_id = load_config().get("guild", {}).get("id")
        return guild_id if guild_id else None

    def _get_role(self, guild: discord.Guild, role_id: int | None) -> discord.Role | None:
        if not role_id:
            return None
        return guild.get_role(role_id)

    async def _assign_role(self, member: discord.Member, role: discord.Role) -> bool:
        if role in member.roles:
            return False
        try:
            await member.add_roles(role, reason="Auto-assigned member role")
            return True
        except discord.Forbidden:
            log.warning("Missing permissions to assign role %s to %s", role.id, member.id)
        except discord.HTTPException:
            log.warning("Failed to assign role %s to %s", role.id, member.id)
        return False

    def _welcome_channel_id(self) -> int | None:
        channel_id = load_config().get("channels", {}).get("welcome")
        return int(channel_id) if channel_id else None

    async def _send_welcome(self, member: discord.Member) -> None:
        if not is_main_guild(member.guild.id):
            return
        channel_id = self._welcome_channel_id()
        if not channel_id:
            return
        channel = member.guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            log.warning("Welcome channel %s not found", channel_id)
            return

        channels = load_config().get("channels", {})
        rules = channels.get("rules")
        info = channels.get("info_panel")
        verify = verification_channel_id()
        tickets = channels.get("tickets")

        rules_line = f"Read the server rules in <#{rules}>" if rules else "Read the server rules in #rules"
        info_line = (
            f"Check out <#{info}> for important information"
            if info
            else "Check out #information for important information"
        )
        verify_line = f"Verify yourself in <#{verify}>" if verify else "Verify yourself in #verify"
        ticket_hint = f"Feel free to open a ticket in <#{tickets}>." if tickets else "Feel free to open a ticket."

        text = (
            "**Welcome to the server!**\n\n"
            f"Welcome, {member.mention}! We're glad to have you here.\n\n"
            "**Before getting started, make sure to:**\n"
            f"• {rules_line}\n"
            f"• {info_line}\n"
            f"• {verify_line}\n\n"
            f"If you have any questions or need help, {ticket_hint}\n\n"
            "Enjoy your time here and welcome to the community!"
        )
        try:
            await channel.send(text)
        except discord.HTTPException:
            log.warning("Failed to send welcome message for %s", member.id)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return

        await self._send_welcome(member)

        role_ids = [
            pending_verify_role_id(),
            self._application_pending_role_id(),
        ]
        for role_id in role_ids:
            role = self._get_role(member.guild, role_id)
            if role:
                await self._assign_role(member, role)

    @commands.Cog.listener()
    async def on_ready(self):
        if self._synced:
            return
        self._synced = True
        self.bot.loop.create_task(self._sync_existing_members())

    async def _sync_existing_members(self) -> None:
        await self.bot.wait_until_ready()

        guild_id = self._guild_id()
        role_ids = [
            pending_verify_role_id(),
            self._application_pending_role_id(),
        ]
        if not guild_id or not any(role_ids):
            return

        guild = self.bot.get_guild(guild_id)
        if not guild:
            log.warning("Configured guild %s not found for member role sync", guild_id)
            return

        roles = [guild.get_role(role_id) for role_id in role_ids if role_id]
        if not any(roles):
            log.warning("No configured member or pending roles found in guild %s", guild_id)
            return

        try:
            await guild.chunk()
        except discord.HTTPException:
            pass

        assigned = 0
        for member in guild.members:
            if member.bot:
                continue
            for role in roles:
                if role and await self._assign_role(member, role):
                    assigned += 1
                    await asyncio.sleep(0.25)

        if assigned:
            log.info("Assigned member role to %d existing member(s) in %s", assigned, guild.name)


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoRole(bot))
