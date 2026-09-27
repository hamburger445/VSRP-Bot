import json
import re
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

from utils.core import format_datetime, load_config
from utils.database import get_db
from utils.helpers import log_action
from utils.permissions import (
    PERMISSION_DEFINITIONS,
    add_role_to_permissions,
    can_ban,
    can_moderate,
    can_warn,
    find_roles_by_query,
    is_admin,
    is_god,
    load_guild_permissions,
    permission_keys_for_guild,
    permissions_for_role,
    resolve_permission_keys,
)

_DURATION_RE = re.compile(r"^(\d+)(s|m|h|d|w)$", re.IGNORECASE)
_MAX_MUTE = timedelta(days=28)
_PREFIX = lambda: load_config().get("bot", {}).get("prefix", "-")


def _appeals_channel_id() -> int | None:
    return load_config().get("moderation", {}).get("appeals")


def _parse_duration(raw: str | None) -> timedelta | None:
    if not raw:
        return timedelta(minutes=10)
    match = _DURATION_RE.match(raw.strip())
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2).lower()
    if unit == "s":
        return timedelta(seconds=amount)
    if unit == "m":
        return timedelta(minutes=amount)
    if unit == "h":
        return timedelta(hours=amount)
    if unit == "d":
        return timedelta(days=amount)
    if unit == "w":
        return timedelta(weeks=amount)
    return None


def _hierarchy_ok(moderator: discord.Member, target: discord.Member) -> tuple[bool, str]:
    if is_god(target) and not is_god(moderator):
        return False, "You cannot moderate an owner."
    if target.id == moderator.id:
        return False, "You cannot moderate yourself."
    if target.id == moderator.guild.me.id:
        return False, "You cannot moderate the bot."
    if not is_god(moderator) and target.top_role >= moderator.top_role:
        return False, "You cannot moderate a member with an equal or higher role."
    return True, ""


async def _get_case(case_id: int) -> dict | None:
    db = await get_db()
    return await db.execute_fetchone("SELECT * FROM mod_cases WHERE id = ?", (case_id,))


async def _has_appeal(case_id: int) -> bool:
    db = await get_db()
    row = await db.execute_fetchone("SELECT 1 FROM mod_appeals WHERE case_id = ? LIMIT 1", (case_id,))
    return row is not None


async def _delete_case_record(case_id: int) -> dict | None:
    case = await _get_case(case_id)
    if not case:
        return None
    db = await get_db()
    await db.execute("DELETE FROM mod_cases WHERE id = ?", (case_id,))
    await db.commit()
    return case


async def _handle_case_delete(interaction: discord.Interaction, case_id: int) -> None:
    if interaction.response.is_done():
        return
    if not can_moderate(interaction.user):
        await interaction.response.send_message("You don't have permission to delete cases.", ephemeral=True)
        return
    case = await _delete_case_record(case_id)
    if not case:
        await interaction.response.send_message(f"Case #{case_id} not found.", ephemeral=True)
        return

    embed = discord.Embed(
        title=f"Case #{case_id} Deleted",
        description=(
            f"**User:** `{case['user_id']}`\n"
            f"**Action:** {case['action_type'].replace('_', ' ').title()}\n"
            f"**Reason:** {case['reason'][:500]}\n\n"
            f"Removed from record by {interaction.user.mention}."
        ),
        color=0x95A5A6,
    )
    if interaction.response.is_done():
        if interaction.message:
            await interaction.message.edit(embed=embed, view=None)
    else:
        await interaction.response.edit_message(embed=embed, view=None)


async def _create_case(
    guild: discord.Guild,
    target_id: int,
    action_type: str,
    reason: str,
    moderator_id: int,
    *,
    appealable: bool = True,
    duration: str | None = None,
    extra: dict | None = None,
) -> int:
    db = await get_db()
    row = await db.execute_fetchone(
        """
        INSERT INTO mod_cases
        (guild_id, user_id, action_type, reason, moderator_id, active, appealable, duration, extra)
        VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
        RETURNING id
        """,
        (
            guild.id,
            target_id,
            action_type,
            reason[:2000],
            moderator_id,
            1 if appealable else 0,
            duration,
            json.dumps(extra) if extra else None,
        ),
    )
    await db.commit()
    return row["id"]


async def _case_embed(case: dict, guild: discord.Guild, *, title: str | None = None) -> discord.Embed:
    mod = guild.get_member(case["moderator_id"])
    user = guild.get_member(case["user_id"])
    appealed = await _has_appeal(case["id"])
    embed = discord.Embed(
        title=title or f"Case #{case['id']} | {case['action_type'].replace('_', ' ').title()}",
        color=0xE74C3C if case["active"] else 0x95A5A6,
    )
    embed.add_field(name="User", value=user.mention if user else f"`{case['user_id']}`", inline=True)
    embed.add_field(name="Moderator", value=mod.mention if mod else f"`{case['moderator_id']}`", inline=True)
    embed.add_field(
        name="Active",
        value="Appealed" if appealed else ("Yes" if case["active"] else "No"),
        inline=True,
    )
    embed.add_field(name="Reason", value=case["reason"][:1024], inline=False)
    if case.get("duration"):
        embed.add_field(name="Duration", value=case["duration"], inline=True)
    if case.get("extra"):
        try:
            extra = json.loads(case["extra"])
            for k, v in extra.items():
                embed.add_field(name=k.replace("_", " ").title(), value=str(v)[:1024], inline=False)
        except json.JSONDecodeError:
            pass
    embed.set_footer(text=f"Case #{case['id']} | {format_datetime(case['created_at'])}")
    return embed


async def _dm_case_notice(
    bot: commands.Bot,
    guild: discord.Guild,
    target: discord.Member,
    *,
    case_id: int,
    title: str,
    fields: dict[str, str],
    appealable: bool,
) -> None:
    lines = [f"**Case #{case_id}**"]
    lines.extend(f"**{k}:** {v}" for k, v in fields.items())
    description = "\n".join(lines)
    if appealable:
        description += "\n\nYou may submit an appeal using the button below."

    embed = discord.Embed(title=title, description=description, color=0xE74C3C)
    view = None
    if appealable:
        view = AppealOpenView(case_id)
        bot.add_view(view)
    try:
        user = target
        if not isinstance(user, discord.Member):
            user = await bot.fetch_user(target.id)
        await user.send(embed=embed, view=view)
    except discord.HTTPException:
        pass


async def _open_appeal_form(interaction: discord.Interaction, case_id: int) -> None:
    case = await _get_case(case_id)
    if not case:
        await interaction.response.send_message("This case no longer exists.", ephemeral=True)
        return
    if interaction.user.id != case["user_id"]:
        await interaction.response.send_message("This appeal is not for you.", ephemeral=True)
        return
    if not case["active"] or not case["appealable"]:
        await interaction.response.send_message("This case cannot be appealed.", ephemeral=True)
        return
    db = await get_db()
    pending = await db.execute_fetchone(
        "SELECT id FROM mod_appeals WHERE case_id = ? AND status = 'pending'",
        (case_id,),
    )
    if pending:
        await interaction.response.send_message("You already have a pending appeal for this case.", ephemeral=True)
        return
    await interaction.response.send_modal(AppealModal(case_id))


class CaseDeleteView(discord.ui.View):
    def __init__(self, case_id: int):
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(
                label="Delete Case",
                style=discord.ButtonStyle.danger,
                custom_id=f"case_delete_{case_id}",
            )
        )


class AppealOpenView(discord.ui.View):
    def __init__(self, case_id: int):
        super().__init__(timeout=None)
        self.case_id = case_id
        btn = discord.ui.Button(
            label="Submit Appeal",
            style=discord.ButtonStyle.primary,
            custom_id=f"appeal_case_{case_id}",
        )
        btn.callback = self._submit
        self.add_item(btn)

    async def _submit(self, interaction: discord.Interaction):
        await _open_appeal_form(interaction, self.case_id)


class AppealModal(discord.ui.Modal, title="Moderation Appeal"):
    def __init__(self, case_id: int):
        super().__init__()
        self.case_id = case_id
        self.events = discord.ui.TextInput(
            label="Events leading to moderation",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000,
        )
        self.why_reduced = discord.ui.TextInput(
            label="Why should action be reduced/removed?",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000,
        )
        self.rules_ack = discord.ui.TextInput(
            label="Rules violated? Which rule(s)?",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=500,
        )
        self.learned_prevent = discord.ui.TextInput(
            label="What you learned & how you'll prevent this",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000,
        )
        self.additional = discord.ui.TextInput(
            label="Additional context for staff",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=1000,
        )
        self.add_item(self.events)
        self.add_item(self.why_reduced)
        self.add_item(self.rules_ack)
        self.add_item(self.learned_prevent)
        self.add_item(self.additional)

    async def on_submit(self, interaction: discord.Interaction):
        case = await _get_case(self.case_id)
        if not case:
            await interaction.response.send_message("Case not found.", ephemeral=True)
            return
        if interaction.user.id != case["user_id"]:
            await interaction.response.send_message("This appeal is not for you.", ephemeral=True)
            return

        db = await get_db()
        row = await db.execute_fetchone(
            """
            INSERT INTO mod_appeals
            (case_id, user_id, events, why_reduced, rules_ack, learned_prevent, additional)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (
                self.case_id,
                interaction.user.id,
                self.events.value.strip(),
                self.why_reduced.value.strip(),
                self.rules_ack.value.strip(),
                self.learned_prevent.value.strip(),
                self.additional.value.strip() or "",
            ),
        )
        await db.commit()
        appeal_id = row["id"]

        channel_id = _appeals_channel_id()
        guild = interaction.client.get_guild(case["guild_id"])
        if not channel_id or not guild:
            await interaction.response.send_message("Appeals channel not configured.", ephemeral=True)
            return
        channel = guild.get_channel(channel_id)
        if not channel:
            await interaction.response.send_message("Appeals channel not found.", ephemeral=True)
            return

        embed = discord.Embed(title=f"Appeal | Case #{self.case_id}", color=0xF39C12)
        embed.add_field(name="User", value=f"{interaction.user} (`{interaction.user.id}`)", inline=False)
        embed.add_field(name="Action", value=case["action_type"].replace("_", " ").title(), inline=True)
        embed.add_field(name="Original Reason", value=case["reason"][:1024], inline=False)
        embed.add_field(name="Events", value=self.events.value[:1024], inline=False)
        embed.add_field(name="Why Reduced/Removed", value=self.why_reduced.value[:1024], inline=False)
        embed.add_field(name="Rules Acknowledged", value=self.rules_ack.value[:1024], inline=False)
        embed.add_field(name="Learned & Prevention", value=self.learned_prevent.value[:1024], inline=False)
        if self.additional.value.strip():
            embed.add_field(name="Additional Context", value=self.additional.value[:1024], inline=False)
        embed.set_footer(text=f"Appeal #{appeal_id}")

        review_view = _make_review_view(appeal_id)
        interaction.client.add_view(review_view)
        msg = await channel.send(embed=embed, view=review_view)

        await db.execute(
            "UPDATE mod_appeals SET message_id = ?, channel_id = ? WHERE id = ?",
            (msg.id, channel.id, appeal_id),
        )
        await db.commit()

        await interaction.response.send_message(
            f"Your appeal for case **#{self.case_id}** has been submitted for staff review.",
            ephemeral=True,
        )


def _make_review_view(appeal_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)

    async def accept_cb(interaction: discord.Interaction):
        await _review_appeal(interaction, appeal_id, accepted=True)

    async def deny_cb(interaction: discord.Interaction):
        await _review_appeal(interaction, appeal_id, accepted=False)

    accept_btn = discord.ui.Button(
        label="Accept Appeal",
        style=discord.ButtonStyle.success,
        custom_id=f"appeal_accept_{appeal_id}",
    )
    deny_btn = discord.ui.Button(
        label="Deny Appeal",
        style=discord.ButtonStyle.danger,
        custom_id=f"appeal_deny_{appeal_id}",
    )
    accept_btn.callback = accept_cb
    deny_btn.callback = deny_cb
    view.add_item(accept_btn)
    view.add_item(deny_btn)
    return view


async def _review_appeal(interaction: discord.Interaction, appeal_id: int, *, accepted: bool) -> None:
    if not can_moderate(interaction.user):
        await interaction.response.send_message("You don't have permission to review appeals.", ephemeral=True)
        return

    db = await get_db()
    appeal = await db.execute_fetchone("SELECT * FROM mod_appeals WHERE id = ?", (appeal_id,))
    if not appeal:
        await interaction.response.send_message("Appeal not found.", ephemeral=True)
        return
    if appeal["status"] != "pending":
        await interaction.response.send_message(f"This appeal was already **{appeal['status']}**.", ephemeral=True)
        return

    case = await _get_case(appeal["case_id"])
    if not case:
        await interaction.response.send_message("Case not found.", ephemeral=True)
        return

    guild = interaction.guild
    status = "accepted" if accepted else "denied"
    await db.execute(
        "UPDATE mod_appeals SET status = ?, reviewed_by = ? WHERE id = ?",
        (status, interaction.user.id, appeal_id),
    )

    if accepted:
        await db.execute("UPDATE mod_cases SET active = 0 WHERE id = ?", (case["id"],))
        await _reverse_punishment(guild, case)
    await db.commit()

    embed = interaction.message.embeds[0].copy() if interaction.message.embeds else discord.Embed()
    embed.color = 0x27AE60 if accepted else 0xE74C3C
    embed.title = f"Appeal {'Accepted' if accepted else 'Denied'} | Case #{case['id']}"
    embed.add_field(
        name="Reviewed By",
        value=f"{interaction.user.mention} (`{interaction.user.id}`)",
        inline=False,
    )
    if accepted:
        embed.add_field(name="Outcome", value="Moderation removed. Case marked inactive.", inline=False)

    await interaction.response.edit_message(embed=embed, view=None)

    try:
        user = await interaction.client.fetch_user(case["user_id"])
        outcome = "accepted" if accepted else "denied"
        await user.send(
            embed=discord.Embed(
                title=f"Appeal {outcome.title()} | Case #{case['id']}",
                description=(
                    f"Your appeal for case **#{case['id']}** was **{outcome}** by {interaction.user}."
                    + (" The moderation action has been reversed." if accepted else "")
                ),
                color=0x27AE60 if accepted else 0xE74C3C,
            )
        )
    except discord.HTTPException:
        pass


async def _reverse_punishment(guild: discord.Guild, case: dict) -> None:
    action = case["action_type"]
    user_id = case["user_id"]
    try:
        if action in ("ban", "modban"):
            await guild.unban(discord.Object(id=user_id), reason=f"Appeal accepted case #{case['id']}")
        elif action == "mute":
            member = guild.get_member(user_id)
            if member:
                await member.timeout(None, reason=f"Appeal accepted case #{case['id']}")
    except discord.HTTPException:
        pass


class StrikeModal(discord.ui.Modal, title="Issue Strike"):
    def __init__(self, cog: "Moderation", target: discord.Member, moderator: discord.Member):
        super().__init__()
        self.cog = cog
        self.target = target
        self.moderator = moderator
        self.roblox_username = discord.ui.TextInput(label="Roblox Username", required=True, max_length=100)
        self.strike_number = discord.ui.TextInput(label="Strike Number", placeholder="1, 2, or 3", required=True, max_length=1)
        self.proof = discord.ui.TextInput(label="Proof", style=discord.TextStyle.paragraph, required=True, max_length=1500)
        self.add_item(self.roblox_username)
        self.add_item(self.strike_number)
        self.add_item(self.proof)

    async def on_submit(self, interaction: discord.Interaction):
        if self.strike_number.value.strip() not in ("1", "2", "3"):
            await interaction.response.send_message("Strike number must be 1, 2, or 3.", ephemeral=True)
            return
        strike_num = int(self.strike_number.value.strip())
        reason = f"Strike {strike_num} | {self.roblox_username.value.strip()}"
        case_id = await _create_case(
            interaction.guild,
            self.target.id,
            "strike",
            reason,
            self.moderator.id,
            extra={"roblox_username": self.roblox_username.value.strip(), "strike_number": strike_num, "proof": self.proof.value.strip()[:500]},
        )
        await _dm_case_notice(
            self.cog.bot,
            interaction.guild,
            self.target,
            case_id=case_id,
            title=f"Strike {strike_num} Issued",
            fields={
                "Roblox Username": self.roblox_username.value.strip(),
                "Strike Number": str(strike_num),
                "Proof": self.proof.value.strip()[:500],
                "Moderator": str(self.moderator),
            },
            appealable=True,
        )
        await log_action(self.cog.bot, "mod_strike", self.moderator.id, target_id=self.target.id, details={"case_id": case_id}, channel_key="moderation")
        await interaction.response.send_message(f"Strike **{strike_num}** issued. Case **#{case_id}**.", ephemeral=True)


class BanModal(discord.ui.Modal, title="Issue Moderation Ban"):
    def __init__(self, cog: "Moderation", target: discord.Member, moderator: discord.Member):
        super().__init__()
        self.cog = cog
        self.target = target
        self.moderator = moderator
        self.roblox_username = discord.ui.TextInput(label="Roblox Username", required=True, max_length=100)
        self.ban_type = discord.ui.TextInput(label="Ban Type", placeholder="appealable or unappealable", required=True, max_length=20)
        self.proof = discord.ui.TextInput(label="Proof", style=discord.TextStyle.paragraph, required=True, max_length=1500)
        self.add_item(self.roblox_username)
        self.add_item(self.ban_type)
        self.add_item(self.proof)

    async def on_submit(self, interaction: discord.Interaction):
        ban_type = self.ban_type.value.strip().lower()
        if ban_type not in ("appealable", "unappealable"):
            await interaction.response.send_message("Ban type must be `appealable` or `unappealable`.", ephemeral=True)
            return
        appealable = ban_type == "appealable"
        reason = f"{ban_type.title()} ban | {self.roblox_username.value.strip()}"
        case_id = await _create_case(
            interaction.guild,
            self.target.id,
            "modban",
            reason,
            self.moderator.id,
            appealable=appealable,
            extra={"roblox_username": self.roblox_username.value.strip(), "ban_type": ban_type, "proof": self.proof.value.strip()[:500]},
        )
        await _dm_case_notice(
            self.cog.bot,
            interaction.guild,
            self.target,
            case_id=case_id,
            title="You Have Been Banned",
            fields={
                "Roblox Username": self.roblox_username.value.strip(),
                "Ban Type": ban_type.title(),
                "Proof": self.proof.value.strip()[:500],
                "Moderator": str(self.moderator),
            },
            appealable=appealable,
        )
        discord_banned = False
        if is_admin(self.moderator) and _hierarchy_ok(self.moderator, self.target)[0]:
            if interaction.guild.me.guild_permissions.ban_members:
                try:
                    await self.target.ban(reason=f"Case #{case_id}: {reason}", delete_message_days=0)
                    discord_banned = True
                except discord.HTTPException:
                    pass
        await log_action(self.cog.bot, "mod_ban_record", self.moderator.id, target_id=self.target.id, details={"case_id": case_id}, channel_key="moderation")
        extra = " Discord ban applied." if discord_banned else ""
        await interaction.response.send_message(f"Ban recorded. Case **#{case_id}**.{extra}", ephemeral=True)


class Moderation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_check(self, ctx: commands.Context) -> bool:
        if not ctx.guild or not isinstance(ctx.author, discord.Member):
            return False
        if not (can_moderate(ctx.author) or can_warn(ctx.author) or can_ban(ctx.author)):
            await ctx.send("You don't have permission to use moderation commands.")
            return False
        return True

    async def _log_mod(self, ctx: commands.Context, action: str, target_id: int, details: dict) -> None:
        await log_action(self.bot, action, ctx.author.id, target_id=target_id, details=details, channel_key="moderation")

    async def _record_action(
        self,
        ctx: commands.Context,
        target: discord.Member | int,
        action_type: str,
        reason: str,
        *,
        appealable: bool = True,
        duration: str | None = None,
        dm_title: str | None = None,
        dm_fields: dict | None = None,
    ) -> int:
        target_id = target if isinstance(target, int) else target.id
        case_id = await _create_case(
            ctx.guild,
            target_id,
            action_type,
            reason,
            ctx.author.id,
            appealable=appealable,
            duration=duration,
        )
        if dm_fields is not None and not isinstance(target, int):
            await _dm_case_notice(
                self.bot,
                ctx.guild,
                target,
                case_id=case_id,
                title=dm_title or action_type.replace("_", " ").title(),
                fields=dm_fields,
                appealable=appealable,
            )
        await self._log_mod(ctx, f"mod_{action_type}", target_id, {"case_id": case_id, "reason": reason})
        return case_id

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        cid = interaction.data.get("custom_id", "") if interaction.data else ""
        if cid.startswith("case_delete_"):
            case_id = int(cid.removeprefix("case_delete_"))
            await _handle_case_delete(interaction, case_id)

    @app_commands.command(name="strike", description="Issue a strike to a member (staff)")
    async def strike_slash(self, interaction: discord.Interaction, member: discord.Member):
        if not can_moderate(interaction.user):
            await interaction.response.send_message("You don't have permission to issue strikes.", ephemeral=True)
            return
        ok, msg = _hierarchy_ok(interaction.user, member)
        if not ok:
            await interaction.response.send_message(msg, ephemeral=True)
            return
        await interaction.response.send_modal(StrikeModal(self, member, interaction.user))

    @app_commands.command(name="modban", description="Record a ban and optionally Discord ban (admin)")
    async def modban_slash(self, interaction: discord.Interaction, member: discord.Member):
        if not can_ban(interaction.user):
            await interaction.response.send_message("You don't have permission to ban members.", ephemeral=True)
            return
        ok, msg = _hierarchy_ok(interaction.user, member)
        if not ok:
            await interaction.response.send_message(msg, ephemeral=True)
            return
        await interaction.response.send_modal(BanModal(self, member, interaction.user))

    @commands.command(name="modlogs")
    async def modlogs(self, ctx: commands.Context, user: discord.Member):
        if not can_moderate(ctx.author):
            await ctx.send("You don't have permission to view mod logs.")
            return
        db = await get_db()
        rows = await db.execute_fetchall(
            "SELECT * FROM mod_cases WHERE user_id = ? ORDER BY id DESC LIMIT 25",
            (user.id,),
        )
        if not rows:
            await ctx.send(f"No moderation cases for {user.mention}.")
            return
        embed = discord.Embed(title=f"Mod Logs | {user.display_name}", color=0x2B2D31)
        for row in rows:
            status = "Appealed" if await _has_appeal(row["id"]) else ("Active" if row["active"] else "Inactive")
            embed.add_field(
                name=f"Case #{row['id']} | {row['action_type'].title()} | {status}",
                value=f"**Reason:** {row['reason'][:200]}\n**Date:** {format_datetime(row['created_at'])}",
                inline=False,
            )
        await ctx.send(embed=embed)

    @commands.command(name="case")
    async def case_cmd(self, ctx: commands.Context, arg1: str, arg2: int | None = None):
        if arg1.lower() == "delete":
            if not can_moderate(ctx.author):
                await ctx.send("You don't have permission to delete cases.")
                return
            if arg2 is None:
                await ctx.send(f"Usage: `{_PREFIX()}case delete <case #>`")
                return
            case = await _delete_case_record(arg2)
            if not case:
                await ctx.send(f"Case #{arg2} not found.")
                return
            await ctx.send(f"Case #{arg2} removed from {case['user_id']}'s record.")
            return

        try:
            case_id = int(arg1)
        except ValueError:
            await ctx.send(f"Usage: `{_PREFIX()}case <case #>` or `{_PREFIX()}case delete <case #>`")
            return

        if not can_moderate(ctx.author):
            await ctx.send("You don't have permission to view cases.")
            return

        case = await _get_case(case_id)
        if not case:
            await ctx.send(f"Case #{case_id} not found.")
            return
        await ctx.send(embed=await _case_embed(case, ctx.guild), view=CaseDeleteView(case_id))

    @commands.command(name="reason")
    async def reason_cmd(self, ctx: commands.Context, case_id: int, *, new_reason: str):
        if not can_moderate(ctx.author):
            await ctx.send("You don't have permission to edit case reasons.")
            return
        case = await _get_case(case_id)
        if not case:
            await ctx.send(f"Case #{case_id} not found.")
            return
        db = await get_db()
        await db.execute("UPDATE mod_cases SET reason = ? WHERE id = ?", (new_reason[:2000], case_id))
        await db.commit()
        await ctx.send(f"Case #{case_id} reason updated.")

    @commands.command(name="ban")
    async def ban(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        if not can_ban(ctx.author):
            await ctx.send("You don't have permission to ban members.")
            return
        ok, msg = _hierarchy_ok(ctx.author, member)
        if not ok:
            await ctx.send(msg)
            return
        case_id = await self._record_action(
            ctx, member, "ban", reason,
            dm_title="You have been banned",
            dm_fields={"Server": ctx.guild.name, "Reason": reason, "Moderator": str(ctx.author)},
        )
        try:
            await member.ban(reason=f"Case #{case_id}: {reason}", delete_message_days=0)
        except discord.HTTPException:
            await ctx.send(f"Case #{case_id} created but Discord ban failed.")
            return
        await ctx.send(f"Banned {member.mention}. Case **#{case_id}**.")

    @commands.command(name="kick")
    async def kick(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        if not can_moderate(ctx.author):
            await ctx.send("You don't have permission to kick members.")
            return
        ok, msg = _hierarchy_ok(ctx.author, member)
        if not ok:
            await ctx.send(msg)
            return
        case_id = await self._record_action(
            ctx, member, "kick", reason,
            dm_title="You have been kicked",
            dm_fields={"Server": ctx.guild.name, "Reason": reason, "Moderator": str(ctx.author)},
        )
        try:
            await member.kick(reason=f"Case #{case_id}: {reason}")
        except discord.HTTPException:
            await ctx.send(f"Case #{case_id} created but kick failed.")
            return
        await ctx.send(f"Kicked {member.mention}. Case **#{case_id}**.")

    @commands.command(name="mute")
    async def mute(self, ctx: commands.Context, member: discord.Member, *, args: str = ""):
        if not can_moderate(ctx.author):
            await ctx.send("You don't have permission to mute members.")
            return
        ok, msg = _hierarchy_ok(ctx.author, member)
        if not ok:
            await ctx.send(msg)
            return
        reason = "No reason provided"
        duration_label = "10m"
        delta = timedelta(minutes=10)
        if args.strip():
            parts = args.split(maxsplit=1)
            parsed = _parse_duration(parts[0])
            if parsed is not None:
                delta = parsed
                duration_label = parts[0]
                if len(parts) > 1:
                    reason = parts[1]
            else:
                reason = args.strip()
        if delta > _MAX_MUTE:
            await ctx.send("Maximum mute duration is 28 days.")
            return
        until = discord.utils.utcnow() + delta
        case_id = await self._record_action(
            ctx, member, "mute", reason, duration=duration_label,
            dm_title="You have been muted",
            dm_fields={"Duration": duration_label, "Reason": reason, "Moderator": str(ctx.author)},
        )
        try:
            await member.timeout(until, reason=f"Case #{case_id}: {reason}")
        except discord.HTTPException:
            await ctx.send(f"Case #{case_id} created but mute failed.")
            return
        await ctx.send(f"Muted {member.mention} for {duration_label}. Case **#{case_id}**.")

    @commands.command(name="unmute")
    async def unmute(self, ctx: commands.Context, member: discord.Member, *, reason: str = "Mute lifted"):
        if not can_moderate(ctx.author):
            await ctx.send("You don't have permission to unmute members.")
            return
        ok, msg = _hierarchy_ok(ctx.author, member)
        if not ok:
            await ctx.send(msg)
            return
        if member.timed_out_until is None:
            await ctx.send(f"{member.mention} is not muted.")
            return
        case_id = await self._record_action(ctx, member, "unmute", reason, appealable=False)
        try:
            await member.timeout(None, reason=f"Case #{case_id}: {reason}")
        except discord.HTTPException:
            await ctx.send("Failed to unmute.")
            return
        await ctx.send(f"Unmuted {member.mention}. Case **#{case_id}**.")

    @commands.command(name="unban")
    async def unban(self, ctx: commands.Context, user_id: int, *, reason: str = "Ban lifted"):
        if not can_ban(ctx.author):
            await ctx.send("You don't have permission to unban members.")
            return
        try:
            ban_entry = await ctx.guild.fetch_ban(discord.Object(id=user_id))
            await ctx.guild.unban(ban_entry.user, reason=reason)
        except discord.NotFound:
            await ctx.send("That user is not banned.")
            return
        except discord.HTTPException:
            await ctx.send("Failed to unban.")
            return
        case_id = await self._record_action(ctx, user_id, "unban", reason, appealable=False)
        await ctx.send(f"Unbanned `{ban_entry.user}`. Case **#{case_id}**.")

    @commands.command(name="warn")
    async def warn(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        if not can_warn(ctx.author):
            await ctx.send("You don't have permission to warn members.")
            return
        ok, msg = _hierarchy_ok(ctx.author, member)
        if not ok:
            await ctx.send(msg)
            return
        case_id = await self._record_action(
            ctx, member, "warn", reason,
            dm_title="You have received a warning",
            dm_fields={"Reason": reason, "Moderator": str(ctx.author)},
        )
        embed = discord.Embed(
            title="Member Warned",
            description=f"{member.mention} has been issued a warning.",
            color=0xF1C40F,
        )
        embed.add_field(name="Case", value=f"#{case_id}", inline=True)
        embed.add_field(name="Moderator", value=str(ctx.author), inline=True)
        embed.add_field(name="Reason", value=reason[:1024], inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="purge", aliases=["clear"])
    async def purge(self, ctx: commands.Context, amount: int):
        if not can_moderate(ctx.author):
            await ctx.send("You don't have permission to purge messages.")
            return
        if amount < 1:
            await ctx.send("Amount must be at least 1.")
            return
        if not isinstance(ctx.channel, discord.TextChannel):
            await ctx.send("This command can only be used in a text channel.")
            return
        remaining = amount + 1
        total = 0
        while remaining > 0:
            batch = min(remaining, 100)
            deleted = await ctx.channel.purge(limit=batch)
            if not deleted:
                break
            total += len(deleted)
            remaining -= len(deleted)
            if len(deleted) < batch:
                break
        count = max(0, total - 1)
        await self._log_mod(ctx, "mod_purge", ctx.author.id, {"channel_id": ctx.channel.id, "amount": count})
        msg = await ctx.send(f"Purged {count} message(s).")
        await msg.delete(delay=5)

    @commands.command(name="role")
    async def role_cmd(self, ctx: commands.Context, *, args: str = ""):
        """View or assign bot permissions to a role. Partial role names work."""
        if not is_admin(ctx.author):
            await ctx.send("Admin only.")
            return
        if not ctx.guild:
            return

        parts = args.strip().split()
        if not parts:
            allowed = permission_keys_for_guild(ctx.guild.id)
            await ctx.send(
                f"Usage: `{_PREFIX()}role <partial role name> [permissions...]`\n"
                f"Example: `{_PREFIX()}role mod warn moderate`\n"
                f"Permissions on this server: {', '.join(allowed)}"
            )
            return

        role_query = parts[0]
        perm_keys = resolve_permission_keys(parts[1:])
        allowed = set(permission_keys_for_guild(ctx.guild.id))
        invalid = [k for k in perm_keys if k not in allowed]
        if invalid:
            await ctx.send(
                f"Not available on this server: **{', '.join(invalid)}**.\n"
                f"Use: {', '.join(sorted(allowed))}"
            )
            return
        matches = find_roles_by_query(ctx.guild, role_query)

        if not matches and re.fullmatch(r"<@!?(\d+)>", role_query.strip()):
            await ctx.send(
                "That looks like a **user** mention. `-role` needs a **role** — use `@Role`, "
                "`<@&roleId>`, the role name, or the role ID."
            )
            return

        if not matches:
            await ctx.send(f"No role matching `{role_query}`.")
            return
        if len(matches) > 1 and not role_query.isdigit():
            listing = "\n".join(f"• {r.mention} (`{r.id}`)" for r in matches[:10])
            await ctx.send(f"Multiple roles match `{role_query}`:\n{listing}\nUse a role ID or a more specific name.")
            return

        role = matches[0]
        perms = await load_guild_permissions(ctx.guild.id)

        if not perm_keys:
            assigned = [k for k in permissions_for_role(role.id, perms) if k in allowed]
            if not assigned:
                await ctx.send(f"{role.mention} has no bot permissions configured on this server.")
                return
            lines = [f"**{PERMISSION_DEFINITIONS[k]['question']}**" for k in assigned]
            await ctx.send(f"Permissions for {role.mention}:\n" + "\n".join(f"• {line}" for line in lines))
            return

        perms = await add_role_to_permissions(ctx.guild.id, role.id, perm_keys)
        labels = [PERMISSION_DEFINITIONS[k]["label"] for k in perm_keys if k in PERMISSION_DEFINITIONS]
        await self._log_mod(
            ctx,
            "mod_role",
            ctx.author.id,
            {"role_id": role.id, "role_name": role.name, "permissions": perm_keys},
        )
        await ctx.send(f"Added {role.mention} to: **{', '.join(labels)}**.")

    async def setup_persistent_views(self):
        db = await get_db()
        appeals = await db.execute_fetchall(
            "SELECT id FROM mod_appeals WHERE status = 'pending' AND message_id IS NOT NULL"
        )
        for row in appeals:
            self.bot.add_view(_make_review_view(row["id"]))
        cases = await db.execute_fetchall(
            "SELECT id FROM mod_cases WHERE active = 1 AND appealable = 1"
        )
        for row in cases:
            self.bot.add_view(AppealOpenView(row["id"]))


async def setup(bot: commands.Bot):
    cog = Moderation(bot)
    await bot.add_cog(cog)
    await cog.setup_persistent_views()
