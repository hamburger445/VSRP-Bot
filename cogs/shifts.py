import discord
from discord import app_commands
from discord.ext import commands

from utils.core import reply, is_shift_guild, server_label
from utils.permissions import can_shift, has_permission, is_admin
from utils.shifts import (
    _session_duration_seconds,
    delete_shift,
    edit_shift_duration,
    format_duration,
    get_user_stats,
    list_active_shifts,
    list_user_shifts,
    start_shift,
    stop_shift,
    toggle_break,
)


def _can_shift_admin(member: discord.Member) -> bool:
    return has_permission(member, "shift_admin") or is_admin(member)


def _can_use_shifts(member: discord.Member) -> bool:
    return can_shift(member) or is_admin(member)


def _shift_embed(member: discord.Member, stats: dict, *, title: str | None = None) -> discord.Embed:
    embed = discord.Embed(
        title=title or f"Shift Monitor | {member.display_name}",
        color=0x2ECC71 if stats["status"] != "Off Duty" else 0x95A5A6,
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Status", value=stats["status"], inline=True)
    embed.add_field(name="Total Shift Time", value=format_duration(stats["total_seconds"]), inline=True)
    embed.add_field(name="Last Shift Duration", value=format_duration(stats["last_seconds"]), inline=True)
    embed.add_field(name="Average Shift Time", value=format_duration(stats["average_seconds"]), inline=True)
    embed.add_field(name="Completed Shifts", value=str(stats["shift_count"]), inline=True)
    if stats.get("active_session"):
        embed.add_field(
            name="Current Shift",
            value=format_duration(_session_duration_seconds(stats["active_session"])),
            inline=True,
        )
    embed.set_footer(text=server_label(member.guild.id))
    return embed


def _active_shifts_embed(guild: discord.Guild) -> discord.Embed:
    return discord.Embed(title="Active Shifts", color=0x3498DB)


async def _build_active_shifts_embed(guild: discord.Guild) -> discord.Embed:
    rows = await list_active_shifts(guild.id)
    embed = _active_shifts_embed(guild)
    if not rows:
        embed.description = "No active shifts."
        return embed
    for row in rows[:25]:
        user = guild.get_member(row["user_id"])
        name = user.display_name if user else f"ID {row['user_id']}"
        status = "On Break" if row["status"] == "on_break" else "On Shift"
        embed.add_field(
            name=f"#{row['id']} | {name}",
            value=f"{status} | {format_duration(_session_duration_seconds(row))}",
            inline=False,
        )
    return embed


async def _build_history_embed(member: discord.Member) -> discord.Embed:
    rows = await list_user_shifts(member.guild.id, member.id, 15)
    embed = discord.Embed(title=f"Shift History | {member.display_name}", color=0x2B2D31)
    if not rows:
        embed.description = "No shift records."
        return embed
    for row in rows:
        dur = format_duration(_session_duration_seconds(row))
        embed.add_field(
            name=f"Shift #{row['id']} | {row['status']}",
            value=f"Duration: {dur}",
            inline=False,
        )
    return embed


class ShiftDeleteModal(discord.ui.Modal, title="Delete Shift"):
    shift_id_input = discord.ui.TextInput(
        label="Shift ID",
        placeholder="Enter the shift ID to delete",
        required=True,
        max_length=10,
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not is_admin(interaction.user):
            await interaction.response.send_message("Admin only.", ephemeral=True)
            return
        try:
            shift_id = int(self.shift_id_input.value.strip())
        except ValueError:
            await interaction.response.send_message("Invalid shift ID.", ephemeral=True)
            return
        ok, msg = await delete_shift(shift_id)
        await interaction.response.send_message(msg, ephemeral=True)


class ShiftEditModal(discord.ui.Modal, title="Edit Shift Duration"):
    shift_id_input = discord.ui.TextInput(label="Shift ID", required=True, max_length=10)
    minutes_input = discord.ui.TextInput(
        label="Adjustment (minutes)",
        placeholder="Positive or negative minutes",
        required=True,
        max_length=6,
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not is_admin(interaction.user):
            await interaction.response.send_message("Admin only.", ephemeral=True)
            return
        try:
            shift_id = int(self.shift_id_input.value.strip())
            minutes = int(self.minutes_input.value.strip())
        except ValueError:
            await interaction.response.send_message("Invalid shift ID or minutes.", ephemeral=True)
            return
        ok, msg = await edit_shift_duration(shift_id, minutes)
        await interaction.response.send_message(msg, ephemeral=True)


class ShiftBackView(discord.ui.View):
    def __init__(self, target: discord.Member, admin: discord.Member):
        super().__init__(timeout=300)
        self.target = target
        self.admin = admin

    @discord.ui.button(label="Back to Admin Panel", style=discord.ButtonStyle.primary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _can_shift_admin(interaction.user):
            await interaction.response.send_message("You don't have permission to manage shifts.", ephemeral=True)
            return
        stats = await get_user_stats(interaction.guild_id, self.target.id)
        embed = _shift_embed(self.target, stats, title=f"Shift Admin | {self.target.display_name}")
        view = ShiftAdminView(self.target, stats, self.admin)
        await interaction.response.edit_message(content=None, embed=embed, view=view)


class ShiftAdminSelect(discord.ui.Select):
    def __init__(self, target: discord.Member):
        self.target = target
        options = [
            discord.SelectOption(
                label="List Active Shifts",
                description="View everyone currently on shift",
                value="list",
            ),
            discord.SelectOption(
                label="View History",
                description=f"Shift history for {target.display_name}",
                value="history",
            ),
            discord.SelectOption(
                label="Delete Shift",
                description="Remove a shift record by ID (admin)",
                value="delete",
            ),
            discord.SelectOption(
                label="Edit Duration",
                description="Adjust a shift duration by ID (admin)",
                value="edit",
            ),
        ]
        super().__init__(placeholder="Admin actions...", options=options, row=1)

    async def callback(self, interaction: discord.Interaction):
        if not _can_shift_admin(interaction.user):
            await interaction.response.send_message("You don't have permission to manage shifts.", ephemeral=True)
            return

        action = self.values[0]
        if action == "list":
            embed = await _build_active_shifts_embed(interaction.guild)
            view = ShiftBackView(self.target, interaction.user)
            await interaction.response.edit_message(content=None, embed=embed, view=view)
            return

        if action == "history":
            embed = await _build_history_embed(self.target)
            view = ShiftBackView(self.target, interaction.user)
            await interaction.response.edit_message(content=None, embed=embed, view=view)
            return

        if action == "delete":
            if not _can_shift_admin(interaction.user):
                await interaction.response.send_message("You don't have permission to delete shifts.", ephemeral=True)
                return
            await interaction.response.send_modal(ShiftDeleteModal())
            return

        if action == "edit":
            if not _can_shift_admin(interaction.user):
                await interaction.response.send_message("You don't have permission to edit shifts.", ephemeral=True)
                return
            await interaction.response.send_modal(ShiftEditModal())


class ShiftManageView(discord.ui.View):
    def __init__(self, target: discord.Member, stats: dict, *, admin: discord.Member | None = None):
        super().__init__(timeout=300)
        self.target = target
        self.admin = admin
        on_duty = stats["status"] != "Off Duty"
        on_break = stats["status"] == "On Break"

        start_btn = discord.ui.Button(
            label="Start",
            style=discord.ButtonStyle.success,
            disabled=on_duty,
            row=0,
        )
        break_btn = discord.ui.Button(
            label="End Break" if on_break else "Break",
            style=discord.ButtonStyle.secondary,
            disabled=not on_duty,
            row=0,
        )
        stop_btn = discord.ui.Button(
            label="Stop",
            style=discord.ButtonStyle.danger,
            disabled=not on_duty,
            row=0,
        )
        start_btn.callback = self._start
        break_btn.callback = self._break
        stop_btn.callback = self._stop
        self.add_item(start_btn)
        self.add_item(break_btn)
        self.add_item(stop_btn)

    async def _staff_ok(self, interaction: discord.Interaction) -> bool:
        if self.admin:
            if not _can_shift_admin(interaction.user):
                await interaction.response.send_message("You don't have permission to manage shifts.", ephemeral=True)
                return False
            return True
        if interaction.user.id != self.target.id:
            await interaction.response.send_message("This panel is not for you.", ephemeral=True)
            return False
        return True

    async def _refresh(self, interaction: discord.Interaction, message: str) -> None:
        stats = await get_user_stats(interaction.guild_id, self.target.id)
        if self.admin:
            embed = _shift_embed(self.target, stats, title=f"Shift Admin | {self.target.display_name}")
            view = ShiftAdminView(self.target, stats, self.admin)
        else:
            embed = _shift_embed(self.target, stats)
            view = ShiftManageView(self.target, stats)
        await interaction.response.edit_message(content=message, embed=embed, view=view)

    async def _start(self, interaction: discord.Interaction):
        if not await self._staff_ok(interaction):
            return
        ok, msg = await start_shift(
            interaction.guild_id,
            self.target.id,
            admin_id=interaction.user.id if self.admin else None,
        )
        if not ok:
            await interaction.response.send_message(msg, ephemeral=True)
            return
        await self._refresh(interaction, msg)

    async def _break(self, interaction: discord.Interaction):
        if not await self._staff_ok(interaction):
            return
        ok, msg = await toggle_break(
            interaction.guild_id,
            self.target.id,
            admin_id=interaction.user.id if self.admin else None,
        )
        if not ok:
            await interaction.response.send_message(msg, ephemeral=True)
            return
        await self._refresh(interaction, msg)

    async def _stop(self, interaction: discord.Interaction):
        if not await self._staff_ok(interaction):
            return
        ok, msg = await stop_shift(
            interaction.guild_id,
            self.target.id,
            admin_id=interaction.user.id if self.admin else None,
        )
        if not ok:
            await interaction.response.send_message(msg, ephemeral=True)
            return
        await self._refresh(interaction, msg)


class ShiftAdminView(ShiftManageView):
    def __init__(self, target: discord.Member, stats: dict, admin: discord.Member):
        super().__init__(target, stats, admin=admin)
        self.add_item(ShiftAdminSelect(target))


class Shifts(commands.Cog):
    shift = app_commands.Group(name="shift", description="Shift monitoring (FD / PD servers)")

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @shift.command(name="manage", description="Manage your shift with start, break, and stop buttons")
    async def shift_manage(self, interaction: discord.Interaction):
        if not interaction.guild or not is_shift_guild(interaction.guild_id):
            await reply(interaction, "Shift commands are only available on FD and PD servers.", ephemeral=True)
            return
        member = interaction.user
        if not isinstance(member, discord.Member):
            await reply(interaction, "This command can only be used in a server.", ephemeral=True)
            return
        if not _can_use_shifts(member):
            await reply(interaction, "You don't have permission to use shifts.", ephemeral=True)
            return
        stats = await get_user_stats(interaction.guild_id, member.id)
        embed = _shift_embed(member, stats)
        view = ShiftManageView(member, stats)
        await reply(interaction, embed=embed, view=view, ephemeral=True)

    @shift.command(name="admin", description="Admin panel to manage a member's shift")
    @app_commands.describe(member="Member to manage")
    async def shift_admin_panel(self, interaction: discord.Interaction, member: discord.Member):
        if not _can_shift_admin(interaction.user):
            await reply(interaction, "You don't have permission to manage shifts.", ephemeral=True)
            return
        if not interaction.guild or not is_shift_guild(interaction.guild_id):
            await reply(interaction, "Shift admin is only available on FD and PD servers.", ephemeral=True)
            return
        stats = await get_user_stats(interaction.guild_id, member.id)
        embed = _shift_embed(member, stats, title=f"Shift Admin | {member.display_name}")
        embed.description = (
            "Use the buttons to control this member's shift.\n"
            "Use the dropdown for list, history, delete, and edit actions."
        )
        view = ShiftAdminView(member, stats, interaction.user)
        await reply(interaction, embed=embed, view=view, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Shifts(bot))
