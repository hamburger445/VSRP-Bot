import asyncio
import io

import discord
from discord import app_commands
from discord.ext import commands

from utils.helpers import log_action
from utils.core import load_config, get_state, set_state
from utils.database import DatabaseError, DatabaseTimeoutError
from utils.database import DatabaseError, DatabaseTimeoutError, get_db
from utils.core import defer, reply
from utils.permissions import is_staff
from utils.ticket_transcript import build_transcript


async def _get_ticket(channel_id: int) -> dict | None:
    db = await get_db()
    return await db.execute_fetchone(
        "SELECT id, user_id, category, status, frozen FROM support_tickets WHERE channel_id = ?",
        (channel_id,),
    )


async def _has_open_ticket(user_id: int) -> dict | None:
    db = await get_db()
    return await db.execute_fetchone(
        "SELECT channel_id FROM support_tickets WHERE user_id = ? AND status = 'open'",
        (user_id,),
    )


def _format_ticket_details(category_id: str, form_data: dict) -> str:
    if category_id == "general":
        return f"**Question**\n{form_data['question']}"
    if category_id == "report":
        lines = [f"**Roblox Username:** {form_data['roblox_username']}"]
        discord_id = form_data.get("discord_id", "").strip()
        if discord_id:
            lines.append(f"**Discord User ID:** {discord_id}")
        else:
            lines.append("**Discord User ID:** Not provided")
        lines.append(f"**Reason / Rule Broken:**\n{form_data['reason']}")
        lines.append(f"**Evidence:**\n{form_data['evidence']}")
        return "\n\n".join(lines)
    if category_id == "appeal":
        return (
            f"**Case #:** {form_data['case_number']}\n\n"
            f"**Moderation DM Message:**\n{form_data['mod_message']}"
        )
    return ""


def _staff_ping_role_id() -> int | None:
    cfg = load_config()
    tickets_cfg = cfg.get("support_tickets", {})
    return tickets_cfg.get("ping_role") or cfg.get("roles", {}).get("staff")


async def _log_ticket_event(
    guild: discord.Guild,
    *,
    title: str,
    description: str,
    transcript: str | None = None,
    filename: str | None = None,
) -> None:
    channel_id = load_config().get("channels", {}).get("ticket_logs")
    if not channel_id:
        return
    log_channel = guild.get_channel(channel_id)
    if not log_channel:
        return
    embed = discord.Embed(title=title, description=description[:4096], color=0x2B2D31)
    if transcript:
        file = discord.File(io.BytesIO(transcript.encode("utf-8")), filename=filename or "transcript.txt")
        await log_channel.send(embed=embed, file=file)
    else:
        await log_channel.send(embed=embed)


async def _open_ticket(
    interaction: discord.Interaction,
    category: dict,
    form_data: dict,
) -> None:
    category_id = category["id"]
    cfg = load_config()
    category_parent_id = cfg.get("categories", {}).get("tickets")
    support_roles = cfg.get("support_tickets", {}).get("support_roles", [])

    overwrites = {
        interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
        interaction.user: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, attach_files=True, read_message_history=True
        ),
        interaction.guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, manage_channels=True
        ),
    }
    for role_id in support_roles:
        role = interaction.guild.get_role(role_id)
        if role:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            )

    channel_name = f"ticket-{interaction.user.name}".lower().replace(" ", "-")[:32]
    parent = interaction.guild.get_channel(category_parent_id) if category_parent_id else None

    ticket_channel = await interaction.guild.create_text_channel(
        name=channel_name,
        overwrites=overwrites,
        category=parent if isinstance(parent, discord.CategoryChannel) else None,
        reason=f"Support ticket opened by {interaction.user}",
    )

    try:
        db = await get_db()
        await db.execute(
            "INSERT INTO support_tickets (channel_id, user_id, category, status, frozen) VALUES (?, ?, ?, 'open', 0)",
            (ticket_channel.id, interaction.user.id, category_id),
        )
        await db.commit()
    except DatabaseError:
        await ticket_channel.delete(reason="Database error during ticket creation")
        await reply(interaction, "Database error. Please try again.", ephemeral=True)
        return

    details = _format_ticket_details(category_id, form_data)
    embed = discord.Embed(
        title=category["label"],
        description=(
            f"Thank you {interaction.user.mention} for opening a support ticket.\n"
            "A staff member will assist you shortly.\n\n"
            "Use **Close Ticket** when resolved. Staff can use **Manage** to add/remove users or freeze the ticket."
        ),
        color=0x3498DB,
    )
    embed.add_field(name="Submission", value=details[:1024], inline=False)
    if len(details) > 1024:
        embed.add_field(name="Submission (continued)", value=details[1024:2048], inline=False)

    ping_role_id = _staff_ping_role_id()
    ping_content = f"<@&{ping_role_id}>" if ping_role_id else None

    view = TicketControlView()
    await ticket_channel.send(content=ping_content, embed=embed, view=view, allowed_mentions=discord.AllowedMentions(roles=True))

    await _log_ticket_event(
        interaction.guild,
        title="Support Ticket Opened",
        description=(
            f"**User:** {interaction.user.mention} (`{interaction.user.id}`)\n"
            f"**Category:** {category['label']}\n"
            f"**Channel:** {ticket_channel.mention}\n\n"
            f"{details}"
        ),
    )
    await log_action(
        interaction.client,
        "support_ticket",
        interaction.user.id,
        details={"action": "opened", "category": category_id, "channel_id": ticket_channel.id, "form": form_data},
        channel_key="support",
    )

    await reply(interaction, f"Support ticket created: {ticket_channel.mention}", ephemeral=True)


class GeneralSupportModal(discord.ui.Modal, title="General Support"):
    def __init__(self, category: dict):
        super().__init__()
        self.category = category
        self.question = discord.ui.TextInput(
            label="Your Question",
            style=discord.TextStyle.paragraph,
            placeholder="Describe what you need help with...",
            required=True,
            max_length=1000,
        )
        self.add_item(self.question)

    async def on_submit(self, interaction: discord.Interaction):
        await defer(interaction, ephemeral=True)
        try:
            existing = await _has_open_ticket(interaction.user.id)
            if existing:
                channel = interaction.guild.get_channel(existing["channel_id"])
                await reply(
                    interaction,
                    f"You already have an open support ticket: {channel.mention if channel else 'unknown'}",
                    ephemeral=True,
                )
                return
        except (DatabaseTimeoutError, DatabaseError):
            await reply(interaction, "Database error. Please try again.", ephemeral=True)
            return

        await _open_ticket(interaction, self.category, {"question": self.question.value})


class ReportModal(discord.ui.Modal, title="Player Report"):
    def __init__(self, category: dict):
        super().__init__()
        self.category = category
        self.roblox_username = discord.ui.TextInput(
            label="Roblox Username of Offender",
            placeholder="Exact Roblox username",
            required=True,
            max_length=100,
        )
        self.discord_id = discord.ui.TextInput(
            label="Discord User ID (optional)",
            placeholder="Leave blank if unknown",
            required=False,
            max_length=20,
        )
        self.reason = discord.ui.TextInput(
            label="Reason / Rule Broken",
            style=discord.TextStyle.paragraph,
            placeholder="What rule was broken and what happened?",
            required=True,
            max_length=1000,
        )
        self.evidence = discord.ui.TextInput(
            label="Evidence",
            style=discord.TextStyle.paragraph,
            placeholder="Links, screenshots, or a detailed description",
            required=True,
            max_length=1500,
        )
        self.add_item(self.roblox_username)
        self.add_item(self.discord_id)
        self.add_item(self.reason)
        self.add_item(self.evidence)

    async def on_submit(self, interaction: discord.Interaction):
        await defer(interaction, ephemeral=True)
        discord_id = self.discord_id.value.strip()
        if discord_id and not discord_id.isdigit():
            await reply(interaction, "Discord User ID must be a numeric ID or left blank.", ephemeral=True)
            return

        try:
            existing = await _has_open_ticket(interaction.user.id)
            if existing:
                channel = interaction.guild.get_channel(existing["channel_id"])
                await reply(
                    interaction,
                    f"You already have an open support ticket: {channel.mention if channel else 'unknown'}",
                    ephemeral=True,
                )
                return
        except (DatabaseTimeoutError, DatabaseError):
            await reply(interaction, "Database error. Please try again.", ephemeral=True)
            return

        await _open_ticket(
            interaction,
            self.category,
            {
                "roblox_username": self.roblox_username.value.strip(),
                "discord_id": discord_id,
                "reason": self.reason.value.strip(),
                "evidence": self.evidence.value.strip(),
            },
        )


class AppealModal(discord.ui.Modal, title="Ban Appeal"):
    def __init__(self, category: dict):
        super().__init__()
        self.category = category
        self.case_number = discord.ui.TextInput(
            label="Case #",
            placeholder="Your case number",
            required=True,
            max_length=50,
        )
        self.mod_message = discord.ui.TextInput(
            label="Exact Moderation DM Message",
            style=discord.TextStyle.paragraph,
            placeholder="Paste the exact message you received in DMs",
            required=True,
            max_length=1500,
        )
        self.add_item(self.case_number)
        self.add_item(self.mod_message)

    async def on_submit(self, interaction: discord.Interaction):
        await defer(interaction, ephemeral=True)
        try:
            existing = await _has_open_ticket(interaction.user.id)
            if existing:
                channel = interaction.guild.get_channel(existing["channel_id"])
                await reply(
                    interaction,
                    f"You already have an open support ticket: {channel.mention if channel else 'unknown'}",
                    ephemeral=True,
                )
                return
        except (DatabaseTimeoutError, DatabaseError):
            await reply(interaction, "Database error. Please try again.", ephemeral=True)
            return

        await _open_ticket(
            interaction,
            self.category,
            {
                "case_number": self.case_number.value.strip(),
                "mod_message": self.mod_message.value.strip(),
            },
        )


def _modal_for_category(category: dict) -> discord.ui.Modal:
    category_id = category["id"]
    if category_id == "general":
        return GeneralSupportModal(category)
    if category_id == "report":
        return ReportModal(category)
    if category_id == "appeal":
        return AppealModal(category)
    return GeneralSupportModal(category)


class TicketCategorySelect(discord.ui.Select):
    def __init__(self, categories: list[dict]):
        options = [
            discord.SelectOption(
                label=cat["label"],
                value=cat["id"],
                description=cat.get("description", "")[:100],
            )
            for cat in categories
        ]
        super().__init__(
            placeholder="Select a Ticket Category",
            options=options,
            custom_id="ticket_category_select",
        )
        self.categories = categories

    async def callback(self, interaction: discord.Interaction):
        category_id = self.values[0]
        category = next((c for c in self.categories if c["id"] == category_id), None)
        if not category:
            await reply(interaction, "Invalid category.", ephemeral=True)
            return

        try:
            existing = await _has_open_ticket(interaction.user.id)
            if existing:
                channel = interaction.guild.get_channel(existing["channel_id"])
                await reply(
                    interaction,
                    f"You already have an open support ticket: {channel.mention if channel else 'unknown'}",
                    ephemeral=True,
                )
                return
        except (DatabaseTimeoutError, DatabaseError):
            await reply(interaction, "Database error. Please try again.", ephemeral=True)
            return

        await interaction.response.send_modal(_modal_for_category(category))


class TicketOpenView(discord.ui.View):
    def __init__(self, categories: list[dict]):
        super().__init__(timeout=None)
        self.add_item(TicketCategorySelect(categories))


class TicketManageSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="Select a management action...",
            options=[
                discord.SelectOption(label="Add User", value="add_user", description="Grant a user access to this ticket"),
                discord.SelectOption(label="Remove User", value="remove_user", description="Revoke a user's access"),
                discord.SelectOption(label="Freeze Ticket", value="freeze", description="Prevent the ticket owner from sending messages"),
                discord.SelectOption(label="Unfreeze Ticket", value="unfreeze", description="Allow the ticket owner to send messages again"),
            ],
            custom_id="ticket_manage_select",
        )

    async def callback(self, interaction: discord.Interaction):
        if not is_staff(interaction.user):
            await reply(interaction, "Staff only.", ephemeral=True)
            return

        action = self.values[0]
        ticket = await _get_ticket(interaction.channel_id)
        if not ticket:
            await reply(interaction, "This is not a support ticket channel.", ephemeral=True)
            return

        if action == "add_user":
            view = discord.ui.View(timeout=120)
            view.add_item(TicketAddUserSelect())
            await reply(interaction, "Select a user to add to this ticket:", view=view, ephemeral=True)
        elif action == "remove_user":
            view = discord.ui.View(timeout=120)
            selector = TicketRemoveUserSelect(interaction.channel)
            if not selector.options:
                await reply(interaction, "No removable users found on this ticket.", ephemeral=True)
                return
            view.add_item(selector)
            await reply(interaction, "Select a user to remove from this ticket:", view=view, ephemeral=True)
        elif action == "freeze":
            await _set_ticket_frozen(interaction, ticket, frozen=True)
        elif action == "unfreeze":
            await _set_ticket_frozen(interaction, ticket, frozen=False)


class TicketAddUserSelect(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(placeholder="Choose a user to add...", max_values=1)

    async def callback(self, interaction: discord.Interaction):
        if not is_staff(interaction.user):
            await reply(interaction, "Staff only.", ephemeral=True)
            return
        member = self.values[0]
        if not isinstance(member, discord.Member):
            member = interaction.guild.get_member(member.id)
        if not member:
            await reply(interaction, "User not found in this server.", ephemeral=True)
            return
        await interaction.channel.set_permissions(
            member,
            view_channel=True,
            send_messages=True,
            attach_files=True,
            read_message_history=True,
        )
        await interaction.channel.send(f"{member.mention} was added to this ticket by {interaction.user.mention}.")
        await reply(interaction, f"Added {member.mention} to the ticket.", ephemeral=True)


class TicketRemoveUserSelect(discord.ui.Select):
    def __init__(self, channel: discord.TextChannel):
        options = []
        for target, overwrite in channel.overwrites.items():
            if isinstance(target, discord.Member) and overwrite.view_channel:
                if target.id == channel.guild.me.id:
                    continue
                options.append(
                    discord.SelectOption(
                        label=target.display_name,
                        value=str(target.id),
                        description=f"Remove {target.display_name}",
                    )
                )
        super().__init__(placeholder="Choose a user to remove...", options=options[:25])

    async def callback(self, interaction: discord.Interaction):
        if not is_staff(interaction.user):
            await reply(interaction, "Staff only.", ephemeral=True)
            return
        user_id = int(self.values[0])
        ticket = await _get_ticket(interaction.channel_id)
        if ticket and user_id == ticket["user_id"]:
            await reply(interaction, "Cannot remove the ticket owner. Freeze the ticket instead.", ephemeral=True)
            return
        member = interaction.guild.get_member(user_id)
        if member:
            await interaction.channel.set_permissions(member, overwrite=None)
            await interaction.channel.send(
                f"{member.mention} was removed from this ticket by {interaction.user.mention}."
            )
            await reply(interaction, f"Removed {member.mention} from the ticket.", ephemeral=True)
        else:
            await reply(interaction, "User not found.", ephemeral=True)


async def _set_ticket_frozen(interaction: discord.Interaction, ticket: dict, *, frozen: bool) -> None:
    owner = interaction.guild.get_member(ticket["user_id"])
    if not owner:
        await reply(interaction, "Ticket owner not found.", ephemeral=True)
        return
    try:
        db = await get_db()
        await db.execute(
            "UPDATE support_tickets SET frozen = ? WHERE channel_id = ?",
            (1 if frozen else 0, interaction.channel_id),
        )
        await db.commit()
    except (DatabaseTimeoutError, DatabaseError):
        await reply(interaction, "Database error. Please try again.", ephemeral=True)
        return

    await interaction.channel.set_permissions(
        owner,
        view_channel=True,
        send_messages=not frozen,
        attach_files=not frozen,
        read_message_history=True,
    )
    state = "frozen" if frozen else "unfrozen"
    await interaction.channel.send(
        f"Ticket {state} by {interaction.user.mention}."
        + (" The owner can no longer send messages." if frozen else " The owner can send messages again.")
    )
    await reply(interaction, f"Ticket {state}.", ephemeral=True)


class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, custom_id="ticket_close_btn")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await defer(interaction, ephemeral=False)
        try:
            ticket = await _get_ticket(interaction.channel_id)
            if not ticket:
                await reply(interaction, "This is not a support ticket channel.", ephemeral=True)
                return
            is_owner = interaction.user.id == ticket["user_id"]
            if not is_staff(interaction.user) and not is_owner:
                await reply(interaction, "Only the ticket owner or staff can close this ticket.", ephemeral=True)
                return

            transcript = await build_transcript(interaction.channel)
            filename = f"ticket-{interaction.channel.name}-{interaction.channel.id}.txt"

            db = await get_db()
            await db.execute(
                "UPDATE support_tickets SET status = 'closed' WHERE channel_id = ?",
                (interaction.channel_id,),
            )
            await db.commit()
        except (DatabaseTimeoutError, DatabaseError):
            await reply(interaction, "Database error. Please try again.", ephemeral=True)
            return

        owner = interaction.guild.get_member(ticket["user_id"])
        owner_str = owner.mention if owner else f"`{ticket['user_id']}`"
        await _log_ticket_event(
            interaction.guild,
            title="Support Ticket Closed",
            description=(
                f"**Closed by:** {interaction.user.mention} (`{interaction.user.id}`)\n"
                f"**Owner:** {owner_str}\n"
                f"**Category:** {ticket['category']}\n"
                f"**Channel:** #{interaction.channel.name} (`{interaction.channel_id}`)\n"
                f"**Frozen:** {'Yes' if ticket.get('frozen') else 'No'}"
            ),
            transcript=transcript,
            filename=filename,
        )
        await log_action(
            interaction.client,
            "support_ticket",
            interaction.user.id,
            target_id=ticket["user_id"],
            details={"action": "closed", "channel_id": interaction.channel_id},
            channel_key="support",
        )

        await reply(interaction, "Closing ticket in 5 seconds. Transcript saved to ticket logs.")
        await interaction.channel.send("This ticket has been closed. Transcript has been saved.")
        await asyncio.sleep(5)
        await interaction.channel.delete(reason=f"Ticket closed by {interaction.user}")

    @discord.ui.button(label="Manage", style=discord.ButtonStyle.primary, custom_id="ticket_manage_btn")
    async def manage_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            await reply(interaction, "Staff only.", ephemeral=True)
            return
        ticket = await _get_ticket(interaction.channel_id)
        if not ticket:
            await reply(interaction, "This is not a support ticket channel.", ephemeral=True)
            return
        view = discord.ui.View(timeout=120)
        view.add_item(TicketManageSelect())
        await reply(interaction, "Select a management action:", view=view, ephemeral=True)


class Tickets(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _ensure_posted(self):
        await self.bot.wait_until_ready()
        try:
            await asyncio.sleep(1)
            await self.post_panel()
        except Exception:
            return

    # ticket panel command removed — ticket panel is auto-posted on startup.

    async def setup_persistent_views(self):
        cfg = load_config().get("support_tickets", {})
        categories = cfg.get("categories", [])
        if categories:
            self.bot.add_view(TicketOpenView(categories))
        self.bot.add_view(TicketControlView())

    async def post_panel(self):
        cfg = load_config().get("support_tickets", {})
        categories = cfg.get("categories", [])
        if not categories:
            return
        channel_id = load_config().get("channels", {}).get("tickets")
        guild_id = load_config().get("guild", {}).get("id")
        if not channel_id or not guild_id:
            return
        guild = self.bot.get_guild(int(guild_id))
        if not guild:
            return
        target = guild.get_channel(int(channel_id))
        if not isinstance(target, discord.TextChannel):
            return
        embed = discord.Embed(
            title="Support Center",
            description=(
                "Need assistance? Select a category from the menu below to create a support ticket. "
                "Please review the information for your chosen category before submitting your ticket.\n\n"
                "**General Support**\n"
                "Ask questions, report bugs, request assistance, or receive help from the staff team.\n\n"
                "**Player Report**\n"
                "Report a player for rule violations. Include the player's username, a detailed explanation of what occurred, "
                "and any supporting evidence such as screenshots or videos.\n\n"
                "**Appeal**\n"
                "Appeal a moderation action. Include your case number, the moderation message you received, "
                "and a detailed explanation of why you believe the punishment should be reviewed."
            ),
            color=0x3498DB,
        )
        embed.set_footer(
            text="Please do not open multiple tickets for the same issue. Misuse of the ticket system or false reports may result in moderation action."
        )
        view = TicketOpenView(categories)
        state_key = f"ticket_panel:{channel_id}"
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
                if title == "Support Center":
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
            state_key = f"ticket_panel:{channel_id}"
            msg_id = await get_state(state_key)
            if msg_id and str(payload.message_id) == str(msg_id):
                await self.post_panel()
        except Exception:
            return


async def setup(bot: commands.Bot):
    cog = Tickets(bot)
    await bot.add_cog(cog)
    await cog.setup_persistent_views()
    bot.loop.create_task(cog._ensure_posted())
