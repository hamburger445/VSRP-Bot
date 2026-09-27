import discord
from discord import app_commands
from discord.ext import commands

from utils.database import DatabaseError, DatabaseTimeoutError, get_db
from utils.core import defer, reply
from utils.permissions import is_staff


class ReactionRoles(commands.Cog):
    reactionrole = app_commands.Group(name="reactionrole", description="Reaction role panel setup")

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.user_id == self.bot.user.id:
            return
        await self._handle_reaction(payload, add=True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        await self._handle_reaction(payload, add=False)

    async def _handle_reaction(self, payload: discord.RawReactionActionEvent, add: bool) -> None:
        try:
            db = await get_db()
            row = await db.execute_fetchone(
                "SELECT message_id FROM reaction_roles WHERE message_id = ?",
                (payload.message_id,),
            )
            if not row:
                return

            emoji_str = str(payload.emoji)
            entry = await db.execute_fetchone(
                "SELECT role_id FROM reaction_role_entries WHERE message_id = ? AND emoji = ?",
                (payload.message_id, emoji_str),
            )
            if not entry:
                return
        except (DatabaseTimeoutError, DatabaseError):
            return

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return
        member = guild.get_member(payload.user_id)
        role = guild.get_role(entry["role_id"])
        if not member or not role:
            return

        try:
            if add:
                await member.add_roles(role, reason="Reaction role")
            else:
                await member.remove_roles(role, reason="Reaction role removed")
        except discord.Forbidden:
            pass

    @reactionrole.command(name="create", description="Create a reaction role panel (staff only)")
    @app_commands.describe(
        title="Panel title",
        description="Panel description",
        channel="Channel to post the panel in (defaults to current)",
    )
    async def reactionrole_create(
        self,
        interaction: discord.Interaction,
        title: str,
        description: str,
        channel: discord.TextChannel | None = None,
    ):
        if not is_staff(interaction.user):
            await reply(interaction, "Staff only.", ephemeral=True)
            return

        await defer(interaction, ephemeral=True)
        target = channel or interaction.channel
        embed = discord.Embed(
            title=title,
            description=description + "\n\n_React below to get your roles._",
            color=discord.Color.purple(),
        )
        message = await target.send(embed=embed)

        try:
            db = await get_db()
            await db.execute(
                """
                INSERT INTO reaction_roles (message_id, channel_id, guild_id, title)
                VALUES (?, ?, ?, ?)
                """,
                (message.id, target.id, interaction.guild_id, title),
            )
            await db.commit()
        except DatabaseTimeoutError:
            await reply(interaction, "The database took too long. Please try again.", ephemeral=True)
            return
        except DatabaseError:
            await reply(interaction, "A database error occurred. Please try again.", ephemeral=True)
            return

        await reply(
            interaction,
            f"Reaction role panel created in {target.mention}.\n"
            f"Use `/reactionrole add` to add emoji to role mappings.\n"
            f"Message ID: `{message.id}`",
            ephemeral=True,
        )

    @reactionrole.command(name="add", description="Add an emoji to role mapping to a panel")
    @app_commands.describe(
        message_id="Message ID of the reaction role panel",
        emoji="Emoji to react with",
        role="Role to assign",
    )
    async def reactionrole_add(
        self,
        interaction: discord.Interaction,
        message_id: str,
        emoji: str,
        role: discord.Role,
    ):
        if not is_staff(interaction.user):
            await reply(interaction, "Staff only.", ephemeral=True)
            return

        await defer(interaction, ephemeral=True)
        try:
            db = await get_db()
            panel = await db.execute_fetchone(
                "SELECT channel_id FROM reaction_roles WHERE message_id = ? AND guild_id = ?",
                (int(message_id), interaction.guild_id),
            )
            if not panel:
                await reply(interaction, "Reaction role panel not found.", ephemeral=True)
                return

            await db.execute(
                """
                INSERT INTO reaction_role_entries (message_id, emoji, role_id)
                VALUES (?, ?, ?)
                ON CONFLICT(message_id, emoji) DO UPDATE SET role_id = excluded.role_id
                """,
                (int(message_id), emoji, role.id),
            )
            await db.commit()
        except DatabaseTimeoutError:
            await reply(interaction, "The database took too long. Please try again.", ephemeral=True)
            return
        except DatabaseError:
            await reply(interaction, "A database error occurred. Please try again.", ephemeral=True)
            return

        channel = interaction.guild.get_channel(panel["channel_id"])
        if channel:
            try:
                message = await channel.fetch_message(int(message_id))
                await message.add_reaction(emoji)
            except (discord.NotFound, discord.HTTPException):
                pass

        await reply(
            interaction,
            f"Added {emoji} to {role.mention} on panel `{message_id}`.",
            ephemeral=True,
        )

    @reactionrole.command(name="remove", description="Remove an emoji mapping from a panel")
    @app_commands.describe(message_id="Panel message ID", emoji="Emoji to remove")
    async def reactionrole_remove(
        self,
        interaction: discord.Interaction,
        message_id: str,
        emoji: str,
    ):
        if not is_staff(interaction.user):
            await reply(interaction, "Staff only.", ephemeral=True)
            return

        await defer(interaction, ephemeral=True)
        try:
            db = await get_db()
            result = await db.execute(
                "DELETE FROM reaction_role_entries WHERE message_id = ? AND emoji = ?",
                (int(message_id), emoji),
            )
            await db.commit()
        except DatabaseTimeoutError:
            await reply(interaction, "The database took too long. Please try again.", ephemeral=True)
            return
        except DatabaseError:
            await reply(interaction, "A database error occurred. Please try again.", ephemeral=True)
            return

        if result.rowcount == 0:
            await reply(interaction, "Mapping not found.", ephemeral=True)
            return
        await reply(interaction, f"Removed {emoji} from panel `{message_id}`.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ReactionRoles(bot))
