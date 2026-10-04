"""Idempotent Wytheville Police Department Discord structure setup."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import discord

log = logging.getLogger("vsrp_bot.wpd_setup")

WPD_ROLE_ORDER: list[str] = [
    "Chief of Police",
    "Deputy Chief",
    "Captain",
    "Lieutenant",
    "Sergeant",
    "Corporal",
    "Senior Police Officer",
    "Police Officer",
    "Municipal PD Academy",
    "Cadet",
    "Needs Training",
    "Needs Supervision",
    "Strike 1",
    "Strike 2",
    "Strike 3",
    "Under Investigation",
    "Administrative Leave",
    "Suspended",
    "Probation",
    "Previous Termination",
    "Blacklisted",
    "Leave of Absence",
    "Patrol Division",
    "Traffic Unit",
    "Criminal Investigations Division",
    "K-9 Unit",
    "SWAT / Special Operations",
    "Field Training Officer",
    "Internal Affairs",
    "Training Division",
    "Department Support",
    "Civilian",
    "Automated Systems",
]

COMMAND = (
    "Chief of Police",
    "Deputy Chief",
    "Captain",
    "Lieutenant",
)
SUPERVISION = COMMAND + ("Sergeant", "Corporal")
SWORN = SUPERVISION + ("Senior Police Officer", "Police Officer")
PATROL_ACCESS = SWORN + ("Municipal PD Academy", "Patrol Division")
RECRUITMENT_STAFF = COMMAND + ("Training Division",)
IA_ACCESS = ("Chief of Police", "Deputy Chief", "Internal Affairs")
TRAINING_VOICE = ("Training Division", "Field Training Officer", "Municipal PD Academy", "Cadet") + COMMAND

DENY = discord.PermissionOverwrite(view_channel=False)
VIEW = discord.PermissionOverwrite(view_channel=True, read_message_history=True)
TALK = discord.PermissionOverwrite(
    view_channel=True,
    read_message_history=True,
    send_messages=True,
    attach_files=True,
    embed_links=True,
    use_application_commands=True,
)
VOICE_USE = discord.PermissionOverwrite(view_channel=True, connect=True, speak=True)
VOICE_AFK = discord.PermissionOverwrite(view_channel=True, connect=True, speak=False)


def _guild_role_permissions(name: str) -> discord.Permissions:
    if name == "Chief of Police":
        return discord.Permissions(administrator=True)
    if name == "Deputy Chief":
        return discord.Permissions(
            manage_channels=True,
            manage_roles=True,
            manage_messages=True,
            view_audit_log=True,
            kick_members=True,
            ban_members=True,
            manage_nicknames=True,
            move_members=True,
            mute_members=True,
            deafen_members=True,
        )
    if name == "Captain":
        return discord.Permissions(
            manage_messages=True,
            view_audit_log=True,
            kick_members=True,
            manage_nicknames=True,
            move_members=True,
            mute_members=True,
            deafen_members=True,
        )
    if name == "Lieutenant":
        return discord.Permissions(
            manage_messages=True,
            view_audit_log=True,
            manage_nicknames=True,
            move_members=True,
            mute_members=True,
            deafen_members=True,
        )
    if name == "Sergeant":
        return discord.Permissions(
            manage_messages=True,
            manage_nicknames=True,
            move_members=True,
            mute_members=True,
            deafen_members=True,
        )
    if name == "Corporal":
        return discord.Permissions(
            manage_messages=True,
            manage_nicknames=True,
            move_members=True,
        )
    return discord.Permissions.none()


def _role_color(name: str) -> int:
    if name in COMMAND:
        return 0x1E3A8A
    if name in ("Senior Police Officer", "Police Officer"):
        return 0x2563EB
    if name in ("Cadet", "Municipal PD Academy"):
        return 0x60A5FA
    if name.startswith("Strike") or name in ("Suspended", "Blacklisted", "Under Investigation"):
        return 0xDC2626
    return 0x64748B


@dataclass
class SetupReport:
    roles_created: int = 0
    roles_reused: int = 0
    roles_updated: int = 0
    categories_created: int = 0
    categories_reused: int = 0
    channels_created: int = 0
    channels_reused: int = 0
    overwrites_set: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_embed(self) -> discord.Embed:
        color = 0x27AE60 if self.ok else 0xF39C12
        embed = discord.Embed(
            title="Wytheville Police Department Setup Complete",
            color=color,
        )
        embed.add_field(
            name="Roles",
            value=(
                f"Created: **{self.roles_created}** | Reused: **{self.roles_reused}** | "
                f"Updated: **{self.roles_updated}**"
            ),
            inline=False,
        )
        embed.add_field(
            name="Categories",
            value=f"Created: **{self.categories_created}** | Reused: **{self.categories_reused}**",
            inline=False,
        )
        embed.add_field(
            name="Channels",
            value=f"Created: **{self.channels_created}** | Reused: **{self.channels_reused}**",
            inline=False,
        )
        embed.add_field(
            name="Permissions",
            value=f"Channel overwrites configured: **{self.overwrites_set}**",
            inline=False,
        )
        if self.errors:
            body = "\n".join(f"• {e}"[:200] for e in self.errors[:12])
            if len(self.errors) > 12:
                body += f"\n• … and {len(self.errors) - 12} more (see logs)."
            embed.add_field(name="Issues", value=body, inline=False)
            embed.set_footer(text="Setup finished with errors — review issues above.")
        else:
            embed.set_footer(text="Safe to run again; existing WPD items were reused.")
        return embed


def _r(rmap: dict[str, discord.Role], name: str) -> discord.Role | None:
    return rmap.get(name)


def _add_roles(
    overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite],
    rmap: dict[str, discord.Role],
    names: tuple[str, ...],
    perm: discord.PermissionOverwrite,
) -> None:
    for name in names:
        role = _r(rmap, name)
        if role:
            overwrites[role] = perm


def _build_overwrites(
    guild: discord.Guild,
    rmap: dict[str, discord.Role],
    template: str,
) -> dict[discord.abc.Snowflake, discord.PermissionOverwrite]:
    ow: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {guild.default_role: DENY}

    if template == "info_view":
        ow[guild.default_role] = VIEW
    elif template == "info_staff":
        ow[guild.default_role] = VIEW
        _add_roles(ow, rmap, COMMAND, TALK)
    elif template == "community_general":
        ow[guild.default_role] = TALK
        _add_roles(ow, rmap, ("Department Support", "Civilian"), TALK)
    elif template == "recruit_info":
        ow[guild.default_role] = VIEW
        _add_roles(ow, rmap, RECRUITMENT_STAFF + ("Cadet",), TALK)
    elif template == "recruit_applications":
        _add_roles(ow, rmap, ("Cadet", "Civilian"), TALK)
        _add_roles(ow, rmap, RECRUITMENT_STAFF, TALK)
    elif template == "recruit_status":
        _add_roles(ow, rmap, ("Cadet", "Civilian"), VIEW)
        _add_roles(ow, rmap, RECRUITMENT_STAFF, TALK)
    elif template == "recruit_chat":
        _add_roles(ow, rmap, RECRUITMENT_STAFF, TALK)
    elif template == "patrol":
        _add_roles(ow, rmap, PATROL_ACCESS, TALK)
        _add_roles(ow, rmap, COMMAND, TALK)
    elif template == "unit_traffic":
        _add_roles(ow, rmap, ("Traffic Unit",) + COMMAND, TALK)
    elif template == "unit_cid":
        _add_roles(ow, rmap, ("Criminal Investigations Division",) + COMMAND, TALK)
    elif template == "unit_k9":
        _add_roles(ow, rmap, ("K-9 Unit",) + COMMAND, TALK)
    elif template == "unit_swat":
        _add_roles(ow, rmap, ("SWAT / Special Operations",) + COMMAND, TALK)
    elif template == "supervision":
        _add_roles(ow, rmap, SUPERVISION, TALK)
        _add_roles(ow, rmap, ("Internal Affairs", "Training Division"), TALK)
    elif template == "ia":
        _add_roles(ow, rmap, IA_ACCESS, TALK)
    elif template == "command":
        _add_roles(ow, rmap, COMMAND, TALK)
    elif template == "personnel":
        _add_roles(ow, rmap, COMMAND + ("Internal Affairs",), TALK)
    elif template == "voice_lobby":
        _add_roles(ow, rmap, SWORN + ("Cadet", "Municipal PD Academy", "Department Support"), VOICE_USE)
    elif template == "voice_training":
        _add_roles(ow, rmap, TRAINING_VOICE, VOICE_USE)
    elif template == "voice_command":
        _add_roles(ow, rmap, COMMAND, VOICE_USE)
    elif template == "voice_afk":
        ow[guild.default_role] = VOICE_AFK
    else:
        ow[guild.default_role] = VIEW

    return ow


WPD_CATEGORIES: list[tuple[str, list[tuple[str, str, str]]]] = [
    (
        "INFORMATION",
        [
            ("welcome", "text", "info_staff"),
            ("rules", "text", "info_staff"),
            ("announcements", "text", "info_staff"),
            ("department-information", "text", "info_staff"),
            ("department-directory", "text", "info_staff"),
            ("faq", "text", "info_view"),
        ],
    ),
    ("COMMUNITY", [("general", "text", "community_general")]),
    (
        "RECRUITMENT",
        [
            ("application-information", "text", "recruit_info"),
            ("applications", "text", "recruit_applications"),
            ("application-status", "text", "recruit_status"),
            ("recruitment-chat", "text", "recruit_chat"),
        ],
    ),
    (
        "PATROL DIVISION",
        [
            ("patrol-information", "text", "patrol"),
            ("patrol-briefings", "text", "patrol"),
            ("patrol-chat", "text", "patrol"),
        ],
    ),
    (
        "SPECIAL OPERATIONS",
        [
            ("traffic-unit", "text", "unit_traffic"),
            ("criminal-investigations", "text", "unit_cid"),
            ("k9-unit", "text", "unit_k9"),
            ("swat-special-operations", "text", "unit_swat"),
        ],
    ),
    (
        "SUPERVISION",
        [
            ("supervisor-chat", "text", "supervision"),
            ("officer-evaluations", "text", "supervision"),
            ("disciplinary-actions", "text", "supervision"),
            ("leave-requests", "text", "supervision"),
            ("promotion-recommendations", "text", "supervision"),
        ],
    ),
    (
        "INTERNAL AFFAIRS",
        [
            ("ia-information", "text", "ia"),
            ("complaints", "text", "ia"),
            ("ia-cases", "text", "ia"),
            ("investigations", "text", "ia"),
        ],
    ),
    (
        "COMMAND",
        [
            ("command-chat", "text", "command"),
            ("command-decisions", "text", "command"),
            ("department-management", "text", "command"),
            ("personnel-discussions", "text", "personnel"),
        ],
    ),
    (
        "VOICE",
        [
            ("Lobby", "voice", "voice_lobby"),
            ("Training", "voice", "voice_training"),
            ("Command", "voice", "voice_command"),
            ("AFK", "voice", "voice_afk"),
        ],
    ),
]


async def _get_or_create_role(
    guild: discord.Guild,
    name: str,
    report: SetupReport,
) -> discord.Role | None:
    perms = _guild_role_permissions(name)
    existing = discord.utils.get(guild.roles, name=name)
    if existing:
        report.roles_reused += 1
        try:
            if existing.permissions != perms or existing.colour.value != _role_color(name):
                await existing.edit(
                    permissions=perms,
                    colour=discord.Colour(_role_color(name)),
                    hoist=name in COMMAND or name in ("Sergeant", "Corporal"),
                    mentionable=name in COMMAND,
                    reason="WPD setup",
                )
                report.roles_updated += 1
        except discord.HTTPException as exc:
            report.errors.append(f"Role update @{name}: {exc}")
        return existing
    try:
        role = await guild.create_role(
            name=name,
            permissions=perms,
            colour=discord.Colour(_role_color(name)),
            hoist=name in COMMAND or name in ("Sergeant", "Corporal"),
            mentionable=name in COMMAND,
            reason="WPD setup",
        )
        report.roles_created += 1
        await asyncio.sleep(0.2)
        return role
    except discord.HTTPException as exc:
        report.errors.append(f"Role create @{name}: {exc}")
        return None


async def _reorder_roles(guild: discord.Guild, rmap: dict[str, discord.Role], report: SetupReport) -> None:
    me = guild.me
    if not me or not me.top_role:
        report.errors.append("Cannot reorder roles: bot member unavailable.")
        return
    pos = me.top_role.position - 1
    for name in WPD_ROLE_ORDER:
        role = rmap.get(name)
        if not role or role >= me.top_role:
            continue
        try:
            await role.edit(position=pos, reason="WPD setup hierarchy")
            pos -= 1
            await asyncio.sleep(0.25)
        except discord.HTTPException as exc:
            report.errors.append(f"Role position @{name}: {exc}")


async def _get_or_create_category(
    guild: discord.Guild,
    name: str,
    position: int,
    report: SetupReport,
) -> discord.CategoryChannel | None:
    existing = discord.utils.get(guild.categories, name=name)
    if existing:
        report.categories_reused += 1
        if existing.position != position:
            try:
                await existing.edit(position=position, reason="WPD setup")
            except discord.HTTPException as exc:
                report.errors.append(f"Category position {name}: {exc}")
        return existing
    try:
        cat = await guild.create_category(name, position=position, reason="WPD setup")
        report.categories_created += 1
        await asyncio.sleep(0.2)
        return cat
    except discord.HTTPException as exc:
        report.errors.append(f"Category create {name}: {exc}")
        return None


async def _apply_overwrites(
    channel: discord.abc.GuildChannel,
    guild: discord.Guild,
    rmap: dict[str, discord.Role],
    template: str,
    report: SetupReport,
) -> None:
    try:
        overwrites = _build_overwrites(guild, rmap, template)
        await channel.edit(overwrites=overwrites, reason="WPD setup permissions")
        report.overwrites_set += 1
        await asyncio.sleep(0.15)
    except discord.HTTPException as exc:
        report.errors.append(f"Overwrites #{channel.name}: {exc}")


async def run_wpd_setup(guild: discord.Guild) -> SetupReport:
    report = SetupReport()
    rmap: dict[str, discord.Role] = {}

    for name in WPD_ROLE_ORDER:
        role = await _get_or_create_role(guild, name, report)
        if role:
            rmap[name] = role

    await _reorder_roles(guild, rmap, report)

    for cat_index, (cat_name, channels) in enumerate(WPD_CATEGORIES):
        category = await _get_or_create_category(guild, cat_name, cat_index, report)
        if not category:
            continue

        for ch_index, (ch_name, ch_type, template) in enumerate(channels):
            existing = discord.utils.get(category.channels, name=ch_name)
            if existing:
                report.channels_reused += 1
                channel = existing
            else:
                try:
                    if ch_type == "voice":
                        channel = await guild.create_voice_channel(
                            ch_name,
                            category=category,
                            position=ch_index,
                            reason="WPD setup",
                        )
                    else:
                        channel = await guild.create_text_channel(
                            ch_name,
                            category=category,
                            position=ch_index,
                            reason="WPD setup",
                        )
                    report.channels_created += 1
                    await asyncio.sleep(0.2)
                except discord.HTTPException as exc:
                    report.errors.append(f"Channel create {ch_name}: {exc}")
                    continue

            await _apply_overwrites(channel, guild, rmap, template, report)

    bot_role = _r(rmap, "Automated Systems")
    me = guild.me
    if bot_role and me and bot_role not in me.roles:
        try:
            await me.add_roles(bot_role, reason="WPD setup")
        except discord.HTTPException as exc:
            report.errors.append(f"Could not assign Automated Systems to bot: {exc}")

    return report
