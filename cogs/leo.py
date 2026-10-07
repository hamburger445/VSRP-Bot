import json

import discord
from discord import app_commands
from discord.ext import commands

from cogs.leo_forms_ui import (
    CitationOptionsView,
    ServeWarrantView,
    WarrantOptionsView,
)
from utils.core import defer, format_datetime, load_config, reply
from utils.database import DatabaseError, DatabaseTimeoutError, get_db
from utils.helpers import (
    department_display_name,
    log_action,
    notify_citation_issued,
    notify_warrant_cleared,
    notify_warrant_issued,
)
from utils.leo_case import allocate_case_number, format_case_number
from utils.leo_forms import (
    build_citation_embed,
    build_served_warrant_embed,
    build_warrant_embed,
    citation_summary,
    parse_form_json,
    ticket_status_emoji,
    warrant_form_to_reason,
    warrant_status_emoji,
)
from utils.permissions import is_leo
from utils.tickets import enforce_ticket_thresholds
from utils.users import ensure_user


def _dept_roles(guild: discord.Guild, department: str) -> list[discord.Role]:
    dept_cfg = load_config().get("roles", {}).get("departments", {})
    val = dept_cfg.get(department)
    role_ids = [val] if isinstance(val, int) else (val or [])
    return [r for rid in role_ids if (r := guild.get_role(rid))]


class LeoPanelView(discord.ui.View):
    def __init__(self, cog: "LawEnforcement"):
        super().__init__(timeout=300)
        self.cog = cog

    async def _deny(self, interaction: discord.Interaction) -> bool:
        if not is_leo(interaction.user):
            await reply(interaction, "Law enforcement or staff only.", ephemeral=True)
            return True
        return False

    @discord.ui.button(label="Issue Citation", style=discord.ButtonStyle.primary)
    async def cite_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self._deny(interaction):
            return
        await reply(
            interaction,
            "Use `/citation member:@user` to open the **Virginia Uniform Summons** form.",
            ephemeral=True,
        )

    @discord.ui.button(label="Issue Warrant", style=discord.ButtonStyle.danger)
    async def warrant_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self._deny(interaction):
            return
        await reply(
            interaction,
            "Use `/warrant member:@user` to open the warrant form.",
            ephemeral=True,
        )

    @discord.ui.button(label="Check Warrants", style=discord.ButtonStyle.secondary)
    async def check_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self._deny(interaction):
            return
        await reply(interaction, "Use `/warrant-check member` to view warrants.", ephemeral=True)

    @discord.ui.button(label="Page Department", style=discord.ButtonStyle.secondary)
    async def page_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self._deny(interaction):
            return
        await reply(interaction, "Use `/pager send department message` to page a department.", ephemeral=True)


class LawEnforcement(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _finalize_citation(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        form_data: dict,
        fine: int,
    ) -> None:
        if not interaction.response.is_done():
            await defer(interaction, ephemeral=True)

        case_no = await allocate_case_number()
        violation = citation_summary(form_data)
        form_json = json.dumps(form_data)

        try:
            db = await get_db()
            await ensure_user(db, member.id)
            row = await db.execute_fetchone(
                """
                INSERT INTO citation_tickets (
                    user_id, violation, fine, issued_by, payment_status, form_json, case_number
                )
                VALUES (?, ?, ?, ?, 'unpaid', ?, ?)
                RETURNING id
                """,
                (member.id, violation, fine, interaction.user.id, form_json, case_no),
            )
            await db.commit()
            ticket_id = row["id"] if row else 0
        except (DatabaseTimeoutError, DatabaseError):
            await reply(interaction, "Database error. Please try again.", ephemeral=True)
            return

        footer = f"Issued by {interaction.user.display_name} · Ticket #{ticket_id}"
        embed = build_citation_embed(
            form_data,
            case_no=case_no,
            subject=member,
            fine=fine,
            footer=footer,
        )

        await notify_citation_issued(
            self.bot,
            member.id,
            ticket_id=ticket_id,
            violation=violation,
            fine=fine,
            issued_by=interaction.user.display_name,
            guild=interaction.guild,
        )
        await enforce_ticket_thresholds(self.bot, interaction.guild, member.id)
        await log_action(
            self.bot,
            "citation_ticket",
            interaction.user.id,
            target_id=member.id,
            details={"case_number": case_no, "violation": violation, "fine": fine},
            channel_key="tickets",
        )
        channel_id = load_config().get("channels", {}).get("citations")
        if channel_id and interaction.guild:
            ch = interaction.guild.get_channel(channel_id)
            if ch:
                await ch.send(embed=embed)
        await interaction.followup.send(
            f"{ticket_status_emoji('unpaid')} Citation **#{format_case_number(case_no)}** issued to {member.mention}.",
            ephemeral=True,
        )

    async def _finalize_warrant(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        form_data: dict,
    ) -> None:
        if not interaction.response.is_done():
            await defer(interaction, ephemeral=True)

        case_no = await allocate_case_number()
        reason = warrant_form_to_reason(form_data)
        form_json = json.dumps(form_data)

        try:
            db = await get_db()
            await ensure_user(db, member.id)
            row = await db.execute_fetchone(
                """
                INSERT INTO warrants (
                    user_id, reason, issued_by, active, status, form_json, case_number
                )
                VALUES (?, ?, ?, 1, 'active', ?, ?)
                RETURNING id
                """,
                (member.id, reason, interaction.user.id, form_json, case_no),
            )
            await db.commit()
            warrant_id = row["id"] if row else 0
        except (DatabaseTimeoutError, DatabaseError):
            await reply(interaction, "Database error. Please try again.", ephemeral=True)
            return

        footer = f"Issued by {interaction.user.display_name} · Warrant #{warrant_id}"
        embed = build_warrant_embed(
            form_data,
            case_no=case_no,
            subject=member,
            footer=footer,
        )

        await notify_warrant_issued(
            self.bot,
            member.id,
            reason=reason,
            issued_by=interaction.user.display_name,
            guild=interaction.guild,
        )
        await log_action(
            self.bot,
            "warrant",
            interaction.user.id,
            target_id=member.id,
            details={"case_number": case_no, "reason": reason},
        )
        channel_id = load_config().get("channels", {}).get("warrants")
        if channel_id and interaction.guild:
            ch = interaction.guild.get_channel(channel_id)
            if ch:
                await ch.send(embed=embed)
        await interaction.followup.send(
            f"{warrant_status_emoji(1, 'active')} Warrant **#{format_case_number(case_no)}** created for {member.mention}.",
            ephemeral=True,
        )

    async def _finalize_serve_warrant(
        self,
        interaction: discord.Interaction,
        warrant_id: int,
        warrant_row: dict,
        serve_data: dict,
    ) -> None:
        await defer(interaction, ephemeral=True)
        form_data = parse_form_json(warrant_row.get("form_json"))
        case_no = warrant_row.get("case_number") or str(warrant_id)
        serve_json = json.dumps(serve_data)

        try:
            db = await get_db()
            result = await db.execute(
                """
                UPDATE warrants
                SET active = 0, status = 'served', cleared_at = NOW(),
                    served_at = NOW(), served_by = ?, serve_form_json = ?
                WHERE id = ? AND active = 1
                """,
                (interaction.user.id, serve_json, warrant_id),
            )
            await db.commit()
        except (DatabaseTimeoutError, DatabaseError):
            await reply(interaction, "Database error. Please try again.", ephemeral=True)
            return
        if result.rowcount == 0:
            await reply(interaction, "That warrant is no longer active.", ephemeral=True)
            return

        user_id = warrant_row["user_id"]
        member = interaction.guild.get_member(user_id) if interaction.guild else None
        embed = build_served_warrant_embed(form_data, serve_data, case_no=case_no, subject=member)

        channel_id = load_config().get("channels", {}).get("warrants")
        if channel_id and interaction.guild:
            ch = interaction.guild.get_channel(channel_id)
            if ch:
                await ch.send(embed=embed)

        await log_action(
            self.bot,
            "warrant_served",
            interaction.user.id,
            target_id=user_id,
            details={"warrant_id": warrant_id, "case_number": case_no},
        )
        await reply(
            interaction,
            f"{warrant_status_emoji(0, 'served')} Warrant **#{format_case_number(case_no)}** marked **served** (record kept on file).",
            ephemeral=True,
        )

    @app_commands.command(name="citation", description="Issue a Virginia Uniform Summons (full form)")
    @app_commands.describe(member="Member being cited")
    async def citation(self, interaction: discord.Interaction, member: discord.Member):
        if not is_leo(interaction.user):
            await reply(interaction, "Law enforcement or staff only.", ephemeral=True)
            return
        view = CitationOptionsView(self, member)
        await interaction.response.send_message(
            f"**Virginia Uniform Summons** for {member.mention}\n"
            "Use the dropdowns, then **Continue** to fill in court date, codes, and fine.",
            view=view,
            ephemeral=True,
        )

    @app_commands.command(name="warrant", description="Issue a warrant (Virginia form)")
    @app_commands.describe(member="Subject of the warrant")
    async def warrant(self, interaction: discord.Interaction, member: discord.Member):
        if not is_leo(interaction.user):
            await reply(interaction, "Law enforcement or staff only.", ephemeral=True)
            return
        view = WarrantOptionsView(self, member)
        view._author_id = interaction.user.id
        await interaction.response.send_message(
            f"**Warrant form** for {member.mention}\n"
            "Select options below, then **Continue to details**.",
            view=view,
            ephemeral=True,
        )

    @app_commands.command(name="serve-warrant", description="Mark an active warrant as served")
    @app_commands.describe(member="Member whose warrant was served")
    async def serve_warrant(self, interaction: discord.Interaction, member: discord.Member):
        if not is_leo(interaction.user):
            await reply(interaction, "Law enforcement or staff only.", ephemeral=True)
            return
        try:
            db = await get_db()
            rows = await db.execute_fetchall(
                """
                SELECT id, user_id, reason, form_json, case_number
                FROM warrants WHERE user_id = ? AND active = 1 ORDER BY id DESC
                """,
                (member.id,),
            )
        except (DatabaseTimeoutError, DatabaseError):
            await reply(interaction, "Database error.", ephemeral=True)
            return
        if not rows:
            await reply(interaction, f"No active warrants for {member.mention}.", ephemeral=True)
            return
        view = ServeWarrantView(self, rows)
        await interaction.response.send_message(
            f"Select a warrant to mark **served** for {member.mention}:",
            view=view,
            ephemeral=True,
        )

    async def _remove_warrant(self, interaction: discord.Interaction, warrant_id: int) -> None:
        if not is_leo(interaction.user):
            await reply(interaction, "Law enforcement or staff only.", ephemeral=True)
            return
        await defer(interaction, ephemeral=True)
        try:
            db = await get_db()
            warrant = await db.execute_fetchone(
                "SELECT user_id, reason FROM warrants WHERE id = ? AND active = 1",
                (warrant_id,),
            )
            if not warrant:
                await reply(interaction, "Active warrant not found.", ephemeral=True)
                return
            result = await db.execute(
                "UPDATE warrants SET active = 0, status = 'cleared', cleared_at = NOW() WHERE id = ? AND active = 1",
                (warrant_id,),
            )
            await db.commit()
        except (DatabaseTimeoutError, DatabaseError):
            await reply(interaction, "Database error. Please try again.", ephemeral=True)
            return
        if result.rowcount == 0:
            await reply(interaction, "Active warrant not found.", ephemeral=True)
            return
        await notify_warrant_cleared(
            self.bot,
            warrant["user_id"],
            warrant_id=warrant_id,
            reason=warrant["reason"],
            cleared_by=interaction.user.display_name,
            guild=interaction.guild,
        )
        await log_action(self.bot, "warrant_removal", interaction.user.id, details={"warrant_id": warrant_id})
        await reply(
            interaction,
            f"{warrant_status_emoji(0, 'cleared')} Warrant #{warrant_id} cleared (record kept).",
            ephemeral=True,
        )

    @app_commands.command(name="removewarrant", description="Clear an active warrant by ID (administrative)")
    @app_commands.describe(warrant_id="Warrant ID")
    async def remove_warrant(self, interaction: discord.Interaction, warrant_id: int):
        await self._remove_warrant(interaction, warrant_id)

    @app_commands.command(name="warrant-check", description="Check warrants for a member")
    @app_commands.describe(member="Member to check")
    async def warrant_check(self, interaction: discord.Interaction, member: discord.Member):
        if not is_leo(interaction.user):
            await reply(interaction, "Law enforcement or staff only.", ephemeral=True)
            return
        await defer(interaction, ephemeral=True)
        try:
            db = await get_db()
            rows = await db.execute_fetchall(
                """
                SELECT id, reason, status, active, case_number, created_at
                FROM warrants WHERE user_id = ? ORDER BY id DESC LIMIT 15
                """,
                (member.id,),
            )
        except (DatabaseTimeoutError, DatabaseError):
            await reply(interaction, "Database error. Please try again.", ephemeral=True)
            return
        if not rows:
            await reply(interaction, f"{member.display_name} has no warrants on record.", ephemeral=True)
            return
        embed = discord.Embed(title=f"Warrants | {member.display_name}", color=0x992D22)
        for row in rows:
            emoji = warrant_status_emoji(int(row["active"] or 0), row.get("status"))
            case = format_case_number(row.get("case_number") or row["id"])
            st = (row.get("status") or "unknown").title()
            embed.add_field(
                name=f"{emoji} Case #{case} · {st}",
                value=f"{row['reason']}\nIssued: {format_datetime(row['created_at'])}",
                inline=False,
            )
        await reply(interaction, embed=embed, ephemeral=True)

    pager = app_commands.Group(name="pager", description="Department pager commands")

    @pager.command(name="send", description="Send a pager alert to a department")
    @app_commands.describe(department="Department to page", message="Alert message")
    @app_commands.choices(
        department=[
            app_commands.Choice(name="Wytheville Fire & Rescue", value="fire1"),
            app_commands.Choice(name="Rural Retreat Volunteer Fire Department", value="fire2"),
            app_commands.Choice(
                name="Max Meadows Volunteer Fire Department",
                value="fire3",
            ),
            app_commands.Choice(name="Wythe County Sheriffs Office", value="police1"),
            app_commands.Choice(name="Wytheville Police Department", value="police2"),
            app_commands.Choice(name="EMS", value="ems"),
            app_commands.Choice(name="DOT", value="dot"),
            app_commands.Choice(name="Dispatch", value="dispatch"),
        ]
    )
    async def pager_send(
        self,
        interaction: discord.Interaction,
        department: app_commands.Choice[str],
        message: str,
    ):
        if not is_leo(interaction.user):
            await reply(interaction, "Law enforcement or staff only.", ephemeral=True)
            return
        roles = _dept_roles(interaction.guild, department.value)
        if not roles:
            await reply(interaction, f"Department **{department.name}** is not configured.", ephemeral=True)
            return
        await defer(interaction, ephemeral=True)
        channel_id = load_config().get("channels", {}).get("department_pings") or interaction.channel_id
        channel = interaction.guild.get_channel(channel_id) or interaction.channel
        mentions = " ".join(r.mention for r in roles)
        dept_name = department_display_name(department.value)
        embed = discord.Embed(title=f"Pager | {dept_name}", description=message, color=0xE74C3C)
        embed.set_footer(text=f"Paged by {interaction.user.display_name}")
        await channel.send(content=mentions, embed=embed, allowed_mentions=discord.AllowedMentions(roles=True))
        await reply(interaction, f"Pager sent to {department.name}.", ephemeral=True)

    @app_commands.command(name="leo-panel", description="Open the law enforcement action panel")
    async def leo_panel(self, interaction: discord.Interaction):
        if not is_leo(interaction.user):
            await reply(interaction, "Law enforcement or staff only.", ephemeral=True)
            return
        embed = discord.Embed(
            title="Law Enforcement Panel",
            description=(
                "**Commands:**\n"
                "`/citation` — Virginia Uniform Summons (dropdowns + forms)\n"
                "`/warrant` — Warrant of arrest/search (dropdowns + forms)\n"
                "`/serve-warrant` — Mark warrant served (keeps permanent record)\n"
                "`/warrant-check` — View warrant history with status\n"
                "`/removewarrant` — Administratively clear active warrant\n"
                "`/pager send` — Page a department\n"
                "`/payticket` — Pay citations (citizens)\n\n"
                "🔴 Active · 🟢 Served/Paid · ⚪ Cleared"
            ),
            color=0x2B2D31,
        )
        view = LeoPanelView(self)
        await reply(interaction, embed=embed, view=view, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(LawEnforcement(bot))
