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
from utils.helpers import department_display_name, log_action, notify_warrant_cleared
from utils.leo_case import format_case_number
from utils.leo_forms import ticket_status_emoji, warrant_status_emoji
from utils.form_tokens import sign_form_token
from utils.form_urls import public_form_base
from utils.leo_pdf import pdf_file
from utils.leo_submit import submit_citation, submit_serve_warrant, submit_warrant
from utils.permissions import is_leo


def _paper_form_url(kind: str, interaction: discord.Interaction, member: discord.Member) -> str | None:
    base = public_form_base()
    if not base or not interaction.guild_id:
        return None
    if not getattr(interaction.client, "form_server_running", False):
        return None
    token = sign_form_token(
        kind=kind,
        guild_id=interaction.guild_id,
        officer_id=interaction.user.id,
        subject_id=member.id,
    )
    return f"{base}/f/{kind}?token={token}"


class PaperFormView(discord.ui.View):
    def __init__(self, url: str):
        super().__init__(timeout=600)
        self.add_item(discord.ui.Button(style=discord.ButtonStyle.link, label="Open paper form", url=url))


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
        try:
            result = await submit_citation(
                self.bot,
                guild=interaction.guild,
                officer=interaction.user,
                subject=member,
                form_data=form_data,
                fine=fine,
            )
        except (DatabaseTimeoutError, DatabaseError):
            await reply(interaction, "Database error. Please try again.", ephemeral=True)
            return
        await interaction.followup.send(
            f"{ticket_status_emoji('unpaid')} Citation **#{result['case_number']}** issued to {member.mention}.",
            ephemeral=True,
            file=pdf_file(result["pdf"], result["filename"]),
        )

    async def _finalize_warrant(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        form_data: dict,
    ) -> None:
        if not interaction.response.is_done():
            await defer(interaction, ephemeral=True)
        try:
            result = await submit_warrant(
                self.bot,
                guild=interaction.guild,
                officer=interaction.user,
                subject=member,
                form_data=form_data,
            )
        except (DatabaseTimeoutError, DatabaseError):
            await reply(interaction, "Database error. Please try again.", ephemeral=True)
            return
        await interaction.followup.send(
            f"{warrant_status_emoji(1, 'active')} Warrant **#{result['case_number']}** created for {member.mention}.",
            ephemeral=True,
            file=pdf_file(result["pdf"], result["filename"]),
        )

    async def _finalize_serve_warrant(
        self,
        interaction: discord.Interaction,
        warrant_id: int,
        warrant_row: dict,
        serve_data: dict,
    ) -> None:
        await defer(interaction, ephemeral=True)
        try:
            result = await submit_serve_warrant(
                self.bot,
                guild=interaction.guild,
                officer=interaction.user,
                warrant_id=warrant_id,
                serve_data=serve_data,
            )
        except ValueError as exc:
            await reply(interaction, str(exc), ephemeral=True)
            return
        except (DatabaseTimeoutError, DatabaseError):
            await reply(interaction, "Database error. Please try again.", ephemeral=True)
            return
        await interaction.followup.send(
            f"{warrant_status_emoji(0, 'served')} Warrant **#{result['case_number']}** marked **served** (record kept on file).",
            ephemeral=True,
            file=pdf_file(result["pdf"], result["filename"]),
        )

    @app_commands.command(name="citation", description="Issue a Virginia Uniform Summons (full form)")
    @app_commands.describe(member="Member being cited")
    async def citation(self, interaction: discord.Interaction, member: discord.Member):
        if not is_leo(interaction.user):
            await reply(interaction, "Law enforcement or staff only.", ephemeral=True)
            return
        if not await defer(interaction, ephemeral=True):
            return
        url = _paper_form_url("citation", interaction, member)
        if url:
            await reply(
                interaction,
                f"**Virginia Uniform Summons** for {member.mention}\n"
                "Open the paper form, fill it in, and submit. It posts to Discord with a PDF.",
                view=PaperFormView(url),
                ephemeral=True,
            )
            return
        view = CitationOptionsView(self, member)
        await reply(
            interaction,
            f"**Virginia Uniform Summons** for {member.mention}\n"
            "Set `PUBLIC_FORM_URL` on the host to use the website. Filling in Discord for now.",
            view=view,
            ephemeral=True,
        )

    @app_commands.command(name="warrant", description="Issue a warrant (Virginia form)")
    @app_commands.describe(member="Subject of the warrant")
    async def warrant(self, interaction: discord.Interaction, member: discord.Member):
        if not is_leo(interaction.user):
            await reply(interaction, "Law enforcement or staff only.", ephemeral=True)
            return
        if not await defer(interaction, ephemeral=True):
            return
        url = _paper_form_url("warrant", interaction, member)
        if url:
            await reply(
                interaction,
                f"**Warrant** for {member.mention}\n"
                "Open the paper form, fill it in, and submit. Discord gets the PDF.",
                view=PaperFormView(url),
                ephemeral=True,
            )
            return
        view = WarrantOptionsView(self, member)
        view._author_id = interaction.user.id
        await reply(
            interaction,
            f"**Warrant form** for {member.mention}\n"
            "Set `PUBLIC_FORM_URL` on the host to use the website. Filling in Discord for now.",
            view=view,
            ephemeral=True,
        )

    @app_commands.command(name="serve-warrant", description="Mark an active warrant as served")
    @app_commands.describe(member="Member whose warrant was served")
    async def serve_warrant(self, interaction: discord.Interaction, member: discord.Member):
        if not is_leo(interaction.user):
            await reply(interaction, "Law enforcement or staff only.", ephemeral=True)
            return
        if not await defer(interaction, ephemeral=True):
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
        url = _paper_form_url("serve", interaction, member)
        if url:
            await reply(
                interaction,
                f"Record **served warrant** for {member.mention} on the paper form.",
                view=PaperFormView(url),
                ephemeral=True,
            )
            return
        view = ServeWarrantView(self, rows)
        await reply(
            interaction,
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
        if not await defer(interaction, ephemeral=True):
            return
        if not is_leo(interaction.user):
            await reply(interaction, "Law enforcement or staff only.", ephemeral=True)
            return
        roles = _dept_roles(interaction.guild, department.value)
        if not roles:
            await reply(interaction, f"Department **{department.name}** is not configured.", ephemeral=True)
            return
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
                "`/citation` — paper Uniform Summons website (posts PDF to Discord)\n"
                "`/warrant` — paper warrant website (posts PDF to Discord)\n"
                "`/serve-warrant` — paper served-warrant website\n"
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
