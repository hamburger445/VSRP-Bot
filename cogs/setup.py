import discord
from discord import app_commands
from discord.ext import commands

from utils.core import reply, server_label
from utils.permissions import (
    PERMISSION_DEFINITIONS,
    is_admin,
    is_god,
    load_guild_permissions,
    permission_keys_for_guild,
    set_permission_roles,
    setup_intro_for_guild,
)


def _role_list(guild: discord.Guild, role_ids: list[int]) -> str:
    if not role_ids:
        return "*Not configured*"
    mentions = []
    for role_id in role_ids[:10]:
        role = guild.get_role(role_id)
        mentions.append(role.mention if role else f"`{role_id}`")
    value = ", ".join(mentions)
    if len(role_ids) > 10:
        value += f" (+{len(role_ids) - 10} more)"
    return value


def _format_permissions_embed(guild: discord.Guild, permissions: dict[str, list[int]]) -> discord.Embed:
    keys = permission_keys_for_guild(guild.id)
    embed = discord.Embed(
        title=f"Permission Setup | {server_label(guild.id)}",
        description=(
            f"**{server_label(guild.id)}** (`{guild.id}`)\n\n"
            f"{setup_intro_for_guild(guild.id)}\n\n"
            "Pick a question below, then select roles.\n"
            "You can also use `-role <partial name> warn moderate` in chat."
        ),
        color=0x3498DB,
    )
    for key in keys:
        meta = PERMISSION_DEFINITIONS[key]
        embed.add_field(
            name=meta["question"],
            value=_role_list(guild, permissions.get(key, [])),
            inline=False,
        )
    embed.set_footer(text="Admin permission grants all others automatically on this server")
    return embed


class PermissionTypeSelect(discord.ui.Select):
    def __init__(self, guild_id: int):
        keys = permission_keys_for_guild(guild_id)
        options = [
            discord.SelectOption(
                label=PERMISSION_DEFINITIONS[key]["question"][:100],
                value=key,
                description=PERMISSION_DEFINITIONS[key]["description"][:100],
            )
            for key in keys
        ]
        super().__init__(
            placeholder="Choose a permission question...",
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        key = self.values[0]
        meta = PERMISSION_DEFINITIONS[key]
        view = PermissionSetupView(interaction.client, interaction.guild.id, key)
        embed = discord.Embed(
            title=meta["question"],
            description=(
                f"{meta['description']}\n\n"
                "Select one or more roles below. This **replaces** the current roles for this permission."
            ),
            color=0x3498DB,
        )
        perms = await load_guild_permissions(interaction.guild.id)
        embed.add_field(name="Current Roles", value=_role_list(interaction.guild, perms.get(key, [])), inline=False)
        await interaction.response.edit_message(content=None, embed=embed, view=view)


class GuildRoleSelect(discord.ui.RoleSelect):
    def __init__(self, bot: commands.Bot, guild_id: int, permission_key: str):
        meta = PERMISSION_DEFINITIONS[permission_key]
        super().__init__(
            placeholder="Select roles...",
            min_values=0,
            max_values=25,
            row=1,
        )
        self.bot = bot
        self.guild_id = guild_id
        self.permission_key = permission_key
        self._question = meta["question"]

    async def callback(self, interaction: discord.Interaction):
        role_ids = [role.id for role in self.values]
        perms = await set_permission_roles(self.guild_id, self.permission_key, role_ids)
        embed = _format_permissions_embed(interaction.guild, perms)
        view = SetupPermissionsView(self.bot, self.guild_id)
        await interaction.response.edit_message(
            content=f"Updated: **{self._question}** — {len(role_ids)} role(s) assigned.",
            embed=embed,
            view=view,
        )


class BackToMenuButton(discord.ui.Button):
    def __init__(self, bot: commands.Bot, guild_id: int):
        super().__init__(label="Back to Questions", style=discord.ButtonStyle.secondary, row=2)
        self.bot = bot
        self.guild_id = guild_id

    async def callback(self, interaction: discord.Interaction):
        perms = await load_guild_permissions(self.guild_id)
        embed = _format_permissions_embed(interaction.guild, perms)
        view = SetupPermissionsView(self.bot, self.guild_id)
        await interaction.response.edit_message(content=None, embed=embed, view=view)


class PermissionSetupView(discord.ui.View):
    def __init__(self, bot: commands.Bot, guild_id: int, permission_key: str):
        super().__init__(timeout=600)
        self.add_item(GuildRoleSelect(bot, guild_id, permission_key))
        self.add_item(BackToMenuButton(bot, guild_id))


class SetupPermissionsView(discord.ui.View):
    def __init__(self, bot: commands.Bot, guild_id: int):
        super().__init__(timeout=600)
        self.bot = bot
        self.guild_id = guild_id
        self.add_item(PermissionTypeSelect(guild_id))


class Setup(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="setup-permissions",
        description="Configure server roles for staff, moderation, shifts, LEO, or sessions",
    )
    @app_commands.default_permissions(administrator=True)
    async def setup_permissions(self, interaction: discord.Interaction):
        if not is_admin(interaction.user) and not is_god(interaction.user):
            await reply(interaction, "Admin only.", ephemeral=True)
            return
        if not interaction.guild:
            await reply(interaction, "Run this in a server.", ephemeral=True)
            return

        perms = await load_guild_permissions(interaction.guild.id)
        embed = _format_permissions_embed(interaction.guild, perms)
        view = SetupPermissionsView(self.bot, interaction.guild.id)
        await reply(interaction, embed=embed, view=view, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Setup(bot))
