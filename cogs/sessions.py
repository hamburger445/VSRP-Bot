import discord
from discord import app_commands
from discord.ext import commands

from utils.core import load_config
from utils.database import DatabaseError, DatabaseTimeoutError, get_db
from utils.core import defer, reply
from utils.permissions import is_session_host

CHECK_EMOJI = "\N{WHITE HEAVY CHECK MARK}"


async def _purge_channel(channel: discord.TextChannel) -> int:
    total = 0
    while True:
        deleted = await channel.purge(limit=100)
        total += len(deleted)
        if len(deleted) < 100:
            break
    return total


class Sessions(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._startups: dict[int, dict] = {}
        self._sessions: dict[int, dict] = {}

    def _cfg(self) -> dict:
        return load_config()

    def _channel_id(self, key: str) -> int | None:
        return self._cfg().get("channels", {}).get(key)

    def _guild_has_pending_startup(self, guild_id: int) -> bool:
        return any(
            s["guild_id"] == guild_id and not s.get("launched")
            for s in self._startups.values()
        )

    def _is_check_emoji(self, emoji) -> bool:
        return str(emoji) == CHECK_EMOJI

    @app_commands.command(name="startup", description="Start a session vote with check reactions (staff)")
    @app_commands.describe(reactions="How many check reactions are needed to start the session")
    async def startup(
        self,
        interaction: discord.Interaction,
        reactions: app_commands.Range[int, 1, 50],
    ):
        if not is_session_host(interaction.user):
            await reply(interaction, "Staff only.", ephemeral=True)
            return
        if not isinstance(interaction.channel, discord.TextChannel):
            await reply(interaction, "This command must be used in a text channel.", ephemeral=True)
            return
        if interaction.guild_id in self._sessions:
            await reply(interaction, "A session is already active. Use `/over` to end it first.", ephemeral=True)
            return
        if self._guild_has_pending_startup(interaction.guild_id):
            await reply(interaction, "A session startup vote is already in progress.", ephemeral=True)
            return

        await defer(interaction, ephemeral=True)
        embed = discord.Embed(
            title="Session Startup",
            description=(
                f"{interaction.user.mention} is starting a session.\n\n"
                f"If you know you can join, react with {CHECK_EMOJI}.\n"
                f"**{reactions}** reactions are needed to begin."
            ),
            color=0x3498DB,
        )
        embed.set_footer(text=f"Hosted by {interaction.user.display_name}")

        message = await interaction.channel.send(
            "@everyone",
            embed=embed,
            allowed_mentions=discord.AllowedMentions(everyone=True),
        )
        await message.add_reaction(CHECK_EMOJI)

        self._startups[message.id] = {
            "guild_id": interaction.guild_id,
            "channel_id": interaction.channel.id,
            "host_id": interaction.user.id,
            "required": reactions,
            "launched": False,
        }

        await reply(
            interaction,
            f"Session startup posted in {interaction.channel.mention}. "
            f"Waiting for **{reactions}** {CHECK_EMOJI} reactions.",
            ephemeral=True,
        )

    async def _launch_session(self, startup: dict, vote_message: discord.Message) -> None:
        guild = self.bot.get_guild(startup["guild_id"])
        if not guild:
            return

        host = guild.get_member(startup["host_id"])
        host_mention = host.mention if host else f"<@{startup['host_id']}>"
        vote_count = startup["required"]
        join_code = self._cfg().get("sessions", {}).get("join_code", "VSRC")

        notify_channel_id = self._channel_id("session_host_notify")
        if notify_channel_id:
            notify_channel = guild.get_channel(notify_channel_id)
            if isinstance(notify_channel, discord.TextChannel):
                await notify_channel.send(
                    f"{host_mention} your session is at **{vote_count}** votes. The session will begin shortly."
                )

        channel = guild.get_channel(startup["channel_id"])
        if not isinstance(channel, discord.TextChannel):
            return

        announce_embed = discord.Embed(
            title="Session Starting Soon",
            description=(
                "The session will begin shortly.\n\n"
                f"Everyone should join using code **{join_code}** to join the roleplay session."
            ),
            color=0x27AE60,
        )
        announce_embed.set_footer(text=f"Hosted by {host.display_name if host else startup['host_id']}")

        announce = await channel.send(
            "@everyone",
            embed=announce_embed,
            allowed_mentions=discord.AllowedMentions(everyone=True),
        )

        try:
            await announce.create_thread(name="server chat", auto_archive_duration=1440)
        except discord.HTTPException:
            pass

        try:
            db = await get_db()
            await db.execute(
                """
                INSERT INTO session_state (guild_id, active, started_by, started_at)
                VALUES (?, 1, ?, NOW())
                ON CONFLICT(guild_id) DO UPDATE SET
                    active = 1, started_by = excluded.started_by, started_at = NOW()
                """,
                (startup["guild_id"], startup["host_id"]),
            )
            await db.commit()
        except (DatabaseTimeoutError, DatabaseError):
            pass

        self._sessions[startup["guild_id"]] = {
            "host_id": startup["host_id"],
            "channel_id": startup["channel_id"],
            "vote_message_id": vote_message.id,
            "announce_message_id": announce.id,
        }

    async def _maybe_launch(self, message_id: int) -> None:
        startup = self._startups.get(message_id)
        if not startup or startup.get("launched"):
            return

        guild = self.bot.get_guild(startup["guild_id"])
        if not guild:
            return
        channel = guild.get_channel(startup["channel_id"])
        if not isinstance(channel, discord.TextChannel):
            return

        try:
            message = await channel.fetch_message(message_id)
        except discord.HTTPException:
            return

        reaction = discord.utils.get(message.reactions, emoji=CHECK_EMOJI)
        if not reaction or reaction.count < startup["required"]:
            return

        startup["launched"] = True
        await self._launch_session(startup, message)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.message_id not in self._startups:
            return
        if not self._is_check_emoji(payload.emoji):
            return
        await self._maybe_launch(payload.message_id)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        if payload.message_id not in self._startups:
            return
        startup = self._startups[payload.message_id]
        if startup.get("launched"):
            return
        if not self._is_check_emoji(payload.emoji):
            return
        await self._maybe_launch(payload.message_id)

    @app_commands.command(name="over", description="End the active session and purge the startup channel")
    async def over(self, interaction: discord.Interaction):
        session = self._sessions.get(interaction.guild_id)
        if not session:
            await reply(interaction, "No active session to end.", ephemeral=True)
            return

        is_host = interaction.user.id == session["host_id"]
        if not is_host and not is_session_host(interaction.user):
            await reply(interaction, "Only the session host or staff can end the session.", ephemeral=True)
            return

        channel = interaction.guild.get_channel(session["channel_id"])
        if not isinstance(channel, discord.TextChannel):
            await reply(interaction, "Session channel not found.", ephemeral=True)
            return

        await defer(interaction, ephemeral=True)

        try:
            db = await get_db()
            await db.execute(
                "UPDATE session_state SET active = 0, started_by = NULL, started_at = NULL WHERE guild_id = ?",
                (interaction.guild_id,),
            )
            await db.commit()
        except (DatabaseTimeoutError, DatabaseError):
            await reply(interaction, "Database error. Please try again.", ephemeral=True)
            return

        deleted = await _purge_channel(channel)
        embed = discord.Embed(
            title="Session Over",
            description="The roleplay session is now over. Thank you for playing.",
            color=0xE74C3C,
        )
        embed.set_footer(text=f"Ended by {interaction.user.display_name}")
        await channel.send(embed=embed)

        for message_id, startup in list(self._startups.items()):
            if startup["guild_id"] == interaction.guild_id:
                del self._startups[message_id]
        del self._sessions[interaction.guild_id]

        await reply(
            interaction,
            f"Session ended. Purged **{deleted}** message(s) in {channel.mention}.",
            ephemeral=True,
        )

    @app_commands.command(name="session-status", description="Check if a roleplay session is active")
    async def session_status(self, interaction: discord.Interaction):
        await defer(interaction, ephemeral=True)
        session = self._sessions.get(interaction.guild_id)
        if session:
            host = interaction.guild.get_member(session["host_id"])
            host_name = host.display_name if host else "Unknown"
            channel = interaction.guild.get_channel(session["channel_id"])
            channel_name = channel.mention if channel else "unknown"
            await reply(
                interaction,
                f"Session is **active** | Host: **{host_name}** | Channel: {channel_name}",
                ephemeral=True,
            )
            return

        try:
            db = await get_db()
            row = await db.execute_fetchone(
                "SELECT active, started_by, started_at FROM session_state WHERE guild_id = ?",
                (interaction.guild_id,),
            )
        except (DatabaseTimeoutError, DatabaseError):
            await reply(interaction, "Database error. Please try again.", ephemeral=True)
            return

        if not row or not row["active"]:
            await reply(interaction, "No session is currently active.", ephemeral=True)
            return

        starter = interaction.guild.get_member(row["started_by"])
        starter_name = starter.display_name if starter else "Unknown"
        await reply(
            interaction,
            f"Session is marked active in database | Host: **{starter_name}** | Started: {row['started_at']}",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Sessions(bot))
