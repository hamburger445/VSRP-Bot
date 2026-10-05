import asyncio
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from utils.applications import (
    application_confirm_embed,
    application_departments,
    application_panel_embed,
    application_questions_for,
    application_submission_embed,
    application_pending_role_id,
    application_approved_role_id,
    application_status_color,
    application_status_label,
    applications_channel_id,
    applications_panel_channel_id,
    department_label,
    reviewer_role_ids,
)
from utils.core import defer, reply, load_config, get_state, set_state
from utils.database import DatabaseError, DatabaseTimeoutError, get_db
from utils.embeds import build_embed, build_error_embed, build_success_embed
from utils.permissions import is_staff
from utils.verification import is_verified_user


async def _has_pending_application(user_id: int, department: str = "civilian") -> bool:
    db = await get_db()
    row = await db.execute_fetchone(
        "SELECT id FROM applications WHERE user_id = ? AND department = ? AND status = 'pending'",
        (user_id, department),
    )
    return bool(row)


async def _save_application(user_id: int, answers: list[tuple[str, str]], department: str) -> int:
    db = await get_db()
    row = await db.execute_fetchone(
        "INSERT INTO applications (user_id, status, department) VALUES (?, 'pending', ?) RETURNING id",
        (user_id, department),
    )
    if not row:
        raise DatabaseError("Failed to create application record")
    application_id = row["id"]
    for question, answer in answers:
        await db.execute(
            "INSERT INTO application_answers (application_id, question, answer) VALUES (?, ?, ?)",
            (application_id, question, answer),
        )
    await db.commit()
    return application_id


async def _update_submission_message(application_id: int, channel_id: int, message_id: int) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE applications SET channel_id = ?, message_id = ? WHERE id = ?",
        (channel_id, message_id, application_id),
    )
    await db.commit()


async def _fetch_application(application_id: int) -> dict | None:
    db = await get_db()
    return await db.execute_fetchone(
        "SELECT * FROM applications WHERE id = ?",
        (application_id,),
    )


async def _fetch_application_answers(application_id: int) -> list[tuple[str, str]]:
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT question, answer FROM application_answers WHERE application_id = ? ORDER BY id",
        (application_id,),
    )
    return [(row["question"], row["answer"]) for row in rows]


async def _collect_answers(
    bot: commands.Bot,
    user: discord.User,
    department: str,
) -> list[tuple[str, str]] | None:
    questions = application_questions_for(department)
    answers: list[tuple[str, str]] = []

    def check_message(message: discord.Message) -> bool:
        return (
            message.author.id == user.id
            and isinstance(message.channel, discord.DMChannel)
            and not message.author.bot
        )

    await user.send(embed=application_confirm_embed())
    for question in questions:
        q_text = question["question"] if isinstance(question, dict) else str(question)
        q_type = question.get("type", "text") if isinstance(question, dict) else "text"
        q_options = question.get("options", []) if isinstance(question, dict) else []

        if q_type == "select" and q_options:
            selected_value: str | None = None

            class QuestionSelect(discord.ui.Select):
                def __init__(self):
                    super().__init__(
                        placeholder="Select an option...",
                        min_values=1,
                        max_values=1,
                        options=[
                            discord.SelectOption(label=str(option), value=str(option))
                            for option in q_options
                        ],
                    )

                async def callback(self, interaction: discord.Interaction):
                    nonlocal selected_value
                    if interaction.user.id != user.id:
                        await interaction.response.send_message(
                            "This selection is not for you.",
                            ephemeral=True,
                        )
                        return
                    selected_value = self.values[0]
                    self.view.stop()
                    await interaction.response.defer(ephemeral=True)

            view = discord.ui.View(timeout=300)
            view.add_item(QuestionSelect())
            await user.send(f"**{q_text}**", view=view)
            await view.wait()
            if selected_value is None:
                await user.send(
                    "Application timed out. Please use `/apply` again when you are ready to complete your answers."
                )
                return None
            answer = selected_value
        else:
            await user.send(f"**{q_text}**\n(Reply with your answer or type `cancel` to stop.)")
            try:
                response = await bot.wait_for("message", check=check_message, timeout=300)
            except asyncio.TimeoutError:
                await user.send(
                    "Application timed out. Please use `/apply` again when you are ready to complete your answers."
                )
                return None

            if response.content.strip().lower() == "cancel":
                await user.send("Your application has been canceled. You can begin again with `/apply`.")
                return None

            answer = response.content.strip()

        answers.append((q_text, answer[:1000]))

    await user.send(
        "Thank you for your responses. Your application is being submitted for review."
    )
    return answers


class ReviewReasonModal(discord.ui.Modal, title="Application Review Reason"):
    reason = discord.ui.TextInput(
        label="Review Reason",
        style=discord.TextStyle.long,
        required=False,
        placeholder="Optional reason for this decision",
        max_length=1000,
    )

    def __init__(self, application_id: int, accepted: bool):
        super().__init__()
        self.application_id = application_id
        self.accepted = accepted

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await defer(interaction)
        await _review_application(
            interaction,
            self.application_id,
            accepted=self.accepted,
            reason=self.reason.value.strip() or None,
        )


def _make_review_view(application_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)

    async def accept_callback(interaction: discord.Interaction):
        await _review_application(interaction, application_id, accepted=True)

    async def deny_callback(interaction: discord.Interaction):
        await _review_application(interaction, application_id, accepted=False)

    async def accept_reason_callback(interaction: discord.Interaction):
        await interaction.response.send_modal(ReviewReasonModal(application_id, accepted=True))

    async def deny_reason_callback(interaction: discord.Interaction):
        await interaction.response.send_modal(ReviewReasonModal(application_id, accepted=False))

    accept_button = discord.ui.Button(
        label="Accept Application",
        style=discord.ButtonStyle.success,
        custom_id=f"application_accept_{application_id}",
    )
    deny_button = discord.ui.Button(
        label="Deny Application",
        style=discord.ButtonStyle.danger,
        custom_id=f"application_deny_{application_id}",
    )
    accept_reason_button = discord.ui.Button(
        label="Accept with Reason",
        style=discord.ButtonStyle.success,
        custom_id=f"application_accept_reason_{application_id}",
    )
    deny_reason_button = discord.ui.Button(
        label="Deny with Reason",
        style=discord.ButtonStyle.danger,
        custom_id=f"application_deny_reason_{application_id}",
    )
    accept_button.callback = accept_callback
    deny_button.callback = deny_callback
    accept_reason_button.callback = accept_reason_callback
    deny_reason_button.callback = deny_reason_callback
    view.add_item(accept_button)
    view.add_item(deny_button)
    view.add_item(accept_reason_button)
    view.add_item(deny_reason_button)
    return view


async def _review_application(
    interaction: discord.Interaction,
    application_id: int,
    *,
    accepted: bool,
    reason: str | None = None,
) -> None:
    if not interaction.response.is_done():
        await defer(interaction)

    review_role_ids = reviewer_role_ids()
    allowed = False
    if interaction.guild:
        for role_id in review_role_ids:
            role = interaction.guild.get_role(role_id)
            if role and role in interaction.user.roles:
                allowed = True
                break
    if not allowed and not is_staff(interaction.user):
        await reply(interaction, "You do not have permission to review applications.", ephemeral=True)
        return

    application = await _fetch_application(application_id)
    if not application:
        await reply(interaction, "Application not found.", ephemeral=True)
        return

    if application["status"] != "pending":
        await reply(
            interaction,
            f"This application has already been {application_status_label(application['status']).lower()}.",
            ephemeral=True,
        )
        return

    new_status = "accepted" if accepted else "denied"
    db = await get_db()
    await db.execute(
        "UPDATE applications SET status = ?, reviewer_id = ?, reviewed_at = NOW(), review_reason = ? WHERE id = ?",
        (new_status, interaction.user.id, reason, application_id),
    )
    await db.commit()

    applicant = await interaction.client.fetch_user(application["user_id"])
    if applicant:
        description = (
            "Your application has been accepted! A member of staff will follow up with you shortly."
            if accepted
            else "Your application has been denied. If you have questions, please contact staff for more details."
        )
        if reason:
            description += f"\n\nReason: {reason}"
        try:
            await applicant.send(
                embed=build_embed(
                    title="Application Review Result",
                    description=description,
                    footer="WCRP Civilian Application System",
                )
            )
        except discord.HTTPException:
            pass

    if accepted and interaction.guild:
        member = interaction.guild.get_member(application["user_id"])
        if member:
            approved_role_id = application_approved_role_id()
            if approved_role_id:
                approved_role = interaction.guild.get_role(approved_role_id)
                if approved_role:
                    try:
                        await member.add_roles(approved_role, reason="Application accepted")
                    except discord.Forbidden:
                        pass

            pending_app_role_id = application_pending_role_id()
            if pending_app_role_id:
                pending_app_role = interaction.guild.get_role(pending_app_role_id)
                if pending_app_role and pending_app_role in member.roles:
                    try:
                        await member.remove_roles(pending_app_role, reason="Application accepted")
                    except discord.Forbidden:
                        pass

    if interaction.message and interaction.message.embeds:
        embed = interaction.message.embeds[0].copy()
    else:
        embed = build_embed(
            title="Civilian Application",
            description="Application review status updated.",
            footer="WCRP Civilian Application System",
        )

    embed.color = application_status_color(new_status)
    embed.title = f"Application {application_status_label(new_status)}"
    embed.add_field(name="Reviewed By", value=interaction.user.mention, inline=False)
    embed.add_field(name="Reviewer ID", value=str(interaction.user.id), inline=False)
    if reason:
        embed.add_field(name="Review Reason", value=reason, inline=False)

    if interaction.message:
        try:
            await interaction.message.edit(embed=embed, view=None)
        except discord.HTTPException:
            pass

    await reply(
        interaction,
        f"Application has been {application_status_label(new_status).lower()}.",
        ephemeral=True,
    )


class ApplicationStartButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Start Application",
            style=discord.ButtonStyle.primary,
            custom_id="application_start",
        )

    async def callback(self, interaction: discord.Interaction):
        await _begin_application(interaction)


class ApplicationPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(ApplicationStartButton())


async def _begin_application(interaction: discord.Interaction, department: str = "civilian") -> None:
    member = interaction.user
    if not isinstance(member, discord.Member):
        await reply(
            interaction,
            build_error_embed(
                "Application Failed",
                "This command must be used by a server member.",
                footer="WCRP Civilian Application System",
            ),
            ephemeral=True,
        )
        return

    if not interaction.guild:
        await reply(
            interaction,
            build_error_embed(
                "Application Failed",
                "Applications must be started from a server context.",
                footer="WCRP Civilian Application System",
            ),
            ephemeral=True,
        )
        return

    await defer(interaction, ephemeral=True)

    try:
        verified = await is_verified_user(member)
    except (DatabaseError, DatabaseTimeoutError):
        await reply(
            interaction,
            build_error_embed(
                "Database Unavailable",
                "The database is reconnecting. Please try again in a few seconds.",
                footer="WCRP Application System",
            ),
            ephemeral=True,
        )
        return

    if not verified:
        await reply(
            interaction,
            build_error_embed(
                "Verification Required",
                "You must verify your account before applying. Please complete verification first.",
                footer="WCRP Civilian Application System",
            ),
            ephemeral=True,
        )
        return

    if department not in application_departments():
        department = "civilian"

    try:
        pending = await _has_pending_application(member.id, department)
    except (DatabaseError, DatabaseTimeoutError):
        await reply(
            interaction,
            build_error_embed(
                "Database Unavailable",
                "The database is reconnecting. Please try again in a few seconds.",
                footer="WCRP Application System",
            ),
            ephemeral=True,
        )
        return

    if pending:
        await reply(
            interaction,
            build_error_embed(
                "Application Pending",
                f"You already have a pending **{department_label(department)}** application.",
                footer="WCRP Application System",
            ),
            ephemeral=True,
        )
        return

    try:
        await member.send(
            embed=build_embed(
                title=f"{department_label(department)} Application Started",
                description=(
                    "I have sent you the application questions in DMs. Answer them in order to submit your application."
                ),
                footer="WCRP Application System",
            )
        )
    except discord.Forbidden:
        await reply(
            interaction,
            build_error_embed(
                "DMs Disabled",
                "Please enable direct messages from server members and try again.",
                footer="WCRP Civilian Application System",
            ),
            ephemeral=True,
        )
        return

    await reply(
        interaction,
        "Please check your DMs to continue your application.",
        ephemeral=True,
    )

    interaction.client.loop.create_task(
        _run_application_flow(interaction.client, member, interaction.guild, department)
    )


async def _run_application_flow(
    bot: commands.Bot,
    member: discord.Member,
    guild: discord.Guild,
    department: str,
) -> None:
    answers = await _collect_answers(bot, member, department)
    if not answers:
        return

    try:
        application_id = await _save_application(member.id, answers, department)
    except DatabaseError:
        await member.send(
            embed=build_error_embed(
                "Application Failed",
                "There was a problem saving your application. Please contact staff.",
                footer="WCRP Civilian Application System",
            )
        )
        return

    submission_channel_id = applications_channel_id(department)
    channel = guild.get_channel(submission_channel_id)
    if not isinstance(channel, discord.TextChannel):
        await member.send(
            embed=build_error_embed(
                "Submission Failed",
                "The application submission channel could not be found. Please ask staff to configure it.",
                footer="WCRP Civilian Application System",
            )
        )
        return

    embed = application_submission_embed(member, answers, datetime.now(timezone.utc))
    embed.insert_field_at(
        0,
        name="Department",
        value=department_label(department),
        inline=False,
    )
    review_view = _make_review_view(application_id)
    try:
        message = await channel.send(embed=embed, view=review_view)
        await _update_submission_message(application_id, channel.id, message.id)
    except discord.HTTPException:
        await member.send(
            embed=build_error_embed(
                "Submission Failed",
                "I could not post your application to the review channel. Please ask staff for help.",
                footer="WCRP Civilian Application System",
            )
        )
        return

    await member.send(
        embed=build_success_embed(
            title="Application Submitted",
            description=(
                "Your application has been sent to staff for review. You will be notified when a decision is made."
            ),
            footer="WCRP Civilian Application System",
        )
    )


class Applications(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def post_panel(self):
        panel_channel_id = applications_panel_channel_id()
        guild_id = load_config().get("guild", {}).get("id")
        if panel_channel_id and guild_id:
            guild = self.bot.get_guild(int(guild_id))
            if guild:
                target = guild.get_channel(int(panel_channel_id))
                if isinstance(target, discord.TextChannel):
                    embed = application_panel_embed()
                    view = ApplicationPanelView()
                    state_key = f"applications_panel:{panel_channel_id}"
                    try:
                        msg_id = await get_state(state_key)
                        if msg_id:
                            try:
                                msg = await target.fetch_message(int(msg_id))
                                try:
                                    await msg.edit(embed=embed, view=view)
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
                                    await message.edit(embed=embed, view=view)
                                    await set_state(state_key, str(message.id))
                                    return
                                except Exception:
                                    break
                        msg = await target.send(embed=embed, view=view)
                        await set_state(state_key, str(msg.id))
                    except (DatabaseError, DatabaseTimeoutError):
                        try:
                            await target.send(embed=embed, view=view)
                        except Exception:
                            pass

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        try:
            channel_id = payload.channel_id
            state_key = f"applications_panel:{channel_id}"
            msg_id = await get_state(state_key)
            if msg_id and str(payload.message_id) == str(msg_id):
                await self.post_panel()
        except Exception:
            return

    async def _ensure_posted(self):
        await self.bot.wait_until_ready()
        try:
            await asyncio.sleep(1)
            await self.post_panel()
        except Exception:
            return

    async def setup_persistent_views(self):
        self.bot.add_view(ApplicationPanelView())

    @app_commands.command(name="apply", description="Begin a department or civilian application")
    @app_commands.describe(department="Department to apply for")
    @app_commands.choices(
        department=[
            app_commands.Choice(name="Civilian / WCVA", value="civilian"),
            app_commands.Choice(name="Wytheville Police Department", value="wpd"),
            app_commands.Choice(name="Wythe County Sheriffs Office", value="wcso"),
            app_commands.Choice(name="Wythe County Fire & Rescue", value="wfd"),
        ]
    )
    async def apply(
        self,
        interaction: discord.Interaction,
        department: app_commands.Choice[str] | None = None,
    ):
        dept_key = department.value if department else "civilian"
        await _begin_application(interaction, dept_key)

    @app_commands.command(name="application-status", description="View your application status by department")
    async def application_status(self, interaction: discord.Interaction):
        await defer(interaction, ephemeral=True)
        try:
            db = await get_db()
            rows = await db.execute_fetchall(
                """
                SELECT id, status, department, created_at, reviewed_at, review_reason
                FROM applications WHERE user_id = ?
                ORDER BY created_at DESC LIMIT 10
                """,
                (interaction.user.id,),
            )
        except (DatabaseError, DatabaseTimeoutError):
            await reply(
                interaction,
                build_error_embed(
                    "Database Unavailable",
                    "The database is reconnecting. Please try again in a few seconds.",
                    footer="WCRP Application System",
                ),
                ephemeral=True,
            )
            return

        if not rows:
            await reply(
                interaction,
                build_embed(
                    title="Application Status",
                    description="You do not have any submitted applications. Use `/apply` to start one.",
                    footer="WCRP Application System",
                ),
                ephemeral=True,
            )
            return

        pending = [r for r in rows if r["status"] == "pending"]
        lines = []
        for row in rows:
            dept = department_label(row.get("department") or "civilian")
            emoji = {"pending": "🟡", "accepted": "🟢", "denied": "🔴"}.get(row["status"], "⚪")
            lines.append(
                f"{emoji} **{dept}** — {application_status_label(row['status'])} "
                f"(#{row['id']}, {row['created_at']})"
            )
        desc = "\n".join(lines)
        if pending:
            desc = f"You have **{len(pending)}** pending application(s).\n\n" + desc

        embed = build_embed(
            title="Application Status",
            description=desc,
            footer="WCRP Application System",
        )
        await reply(interaction, embed=embed, ephemeral=True)

    # Panel command removed — applications panel is auto-posted on startup.


async def setup(bot: commands.Bot):
    cog = Applications(bot)
    await bot.add_cog(cog)
    await cog.setup_persistent_views()
    bot.loop.create_task(cog._ensure_posted())
