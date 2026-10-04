"""Destructive full rebuild of the Wytheville Police Department Discord server (/pd setup)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Literal

import discord

log = logging.getLogger("vsrp_bot.wpd_setup")

ChannelKind = Literal["text", "voice"]

# --- Role catalog (canonical WPD) ---

WPD_ROLE_ORDER: list[str] = [
    "Chief of Police",
    "Deputy Chief",
    "Captain",
    "Lieutenant",
    "Sergeant",
    "Corporal",
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
SWORN = SUPERVISION + ("Police Officer",)
PATROL_ACCESS = SWORN + ("Municipal PD Academy", "Patrol Division")
RECRUITMENT_STAFF = COMMAND + ("Training Division",)
IA_ACCESS = ("Chief of Police", "Deputy Chief", "Internal Affairs")
TRAINING_VOICE = ("Training Division", "Field Training Officer", "Municipal PD Academy", "Cadet") + COMMAND
ACADEMY_VIEWERS = ("Cadet", "Civilian", "Department Support", "Municipal PD Academy")
INFO_STAFF = COMMAND + ("Training Division",)
SUPERVISION_PERSONNEL = SUPERVISION + ("Training Division", "Field Training Officer")

INFO_VIEWERS = (
    "Civilian",
    "Cadet",
    "Department Support",
    "Municipal PD Academy",
) + SWORN
DEPT_COMMUNITY = INFO_VIEWERS

WPD_CATEGORIES: list[tuple[str, list[tuple[str, ChannelKind, str]]]] = [
    (
        "INFORMATION",
        [
            ("welcome", "text", "info_readonly"),
            ("rules", "text", "info_readonly"),
            ("announcements", "text", "info_readonly"),
            ("department-information", "text", "info_readonly"),
            ("department-directory", "text", "info_readonly"),
            ("faq", "text", "info_readonly"),
        ],
    ),
    ("COMMUNITY", [("general", "text", "community_general")]),
    (
        "RECRUITMENT",
        [
            ("application-information", "text", "recruit_info_readonly"),
            ("applications", "text", "applications_readonly"),
            ("application-status", "text", "application_status_readonly"),
            ("recruitment-chat", "text", "recruit_chat_comm"),
        ],
    ),
    (
        "PATROL DIVISION",
        [
            ("patrol-information", "text", "patrol_info_readonly"),
            ("patrol-briefings", "text", "patrol_briefings_readonly"),
            ("patrol-chat", "text", "patrol_chat_comm"),
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
            ("supervisor-chat", "text", "supervision_chat_comm"),
            ("officer-evaluations", "text", "supervision_personnel_admin"),
            ("disciplinary-actions", "text", "supervision_personnel_admin"),
            ("leave-requests", "text", "supervision_personnel_admin"),
            ("promotion-recommendations", "text", "supervision_personnel_admin"),
        ],
    ),
    (
        "INTERNAL AFFAIRS",
        [
            ("ia-information", "text", "ia_info_readonly"),
            ("complaints", "text", "ia_comm"),
            ("ia-cases", "text", "ia_comm"),
            ("investigations", "text", "ia_comm"),
        ],
    ),
    (
        "COMMAND",
        [
            ("command-chat", "text", "command_chat_comm"),
            ("command-decisions", "text", "command_admin_comm"),
            ("department-management", "text", "command_admin_comm"),
            ("personnel-discussions", "text", "personnel_comm"),
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

# --- Explicit channel permission presets ---


def _deny_all() -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(view_channel=False)


def _text_readonly_member() -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(
        view_channel=True,
        read_message_history=True,
        add_reactions=True,
        use_external_emojis=True,
        use_external_stickers=True,
        use_application_commands=True,
        send_messages=False,
        send_messages_in_threads=False,
        create_public_threads=False,
        create_private_threads=False,
        embed_links=False,
        attach_files=False,
        mention_everyone=False,
        manage_messages=False,
        manage_threads=False,
        send_tts_messages=False,
        create_instant_invite=False,
        manage_webhooks=False,
    )


def _text_staff_maintain() -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(
        view_channel=True,
        read_message_history=True,
        send_messages=True,
        send_messages_in_threads=True,
        create_public_threads=True,
        create_private_threads=True,
        embed_links=True,
        attach_files=True,
        add_reactions=True,
        use_external_emojis=True,
        use_external_stickers=True,
        use_application_commands=True,
        manage_messages=True,
        manage_threads=True,
        mention_everyone=False,
        send_tts_messages=False,
        create_instant_invite=False,
        manage_webhooks=False,
        manage_channels=False,
        manage_permissions=False,
    )


def _text_comm_member() -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(
        view_channel=True,
        read_message_history=True,
        send_messages=True,
        send_messages_in_threads=True,
        create_public_threads=True,
        create_private_threads=False,
        embed_links=True,
        attach_files=True,
        add_reactions=True,
        use_external_emojis=True,
        use_external_stickers=True,
        use_application_commands=True,
        mention_everyone=False,
        manage_messages=False,
        manage_threads=False,
        send_tts_messages=False,
        create_instant_invite=False,
        manage_webhooks=False,
        manage_channels=False,
        manage_permissions=False,
    )


def _text_comm_supervisor() -> discord.PermissionOverwrite:
    ow = _text_comm_member()
    ow.update(manage_messages=True, manage_threads=True, create_private_threads=True)
    return ow


def _text_comm_command() -> discord.PermissionOverwrite:
    return _text_comm_supervisor()


def _text_bot_service() -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(
        view_channel=True,
        read_message_history=True,
        send_messages=True,
        embed_links=True,
        attach_files=True,
        manage_messages=True,
        use_application_commands=True,
        mention_everyone=False,
        manage_webhooks=False,
        manage_channels=False,
        manage_permissions=False,
    )


def _voice_member() -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(
        view_channel=True,
        connect=True,
        speak=True,
        stream=True,
        use_voice_activation=True,
        use_soundboard=True,
        use_external_sounds=True,
        priority_speaker=False,
        mute_members=False,
        deafen_members=False,
        move_members=False,
    )


def _voice_training() -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(
        view_channel=True,
        connect=True,
        speak=True,
        stream=True,
        use_voice_activation=True,
        use_soundboard=False,
        use_external_sounds=False,
        priority_speaker=False,
        mute_members=False,
        deafen_members=False,
        move_members=False,
    )


def _voice_command() -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(
        view_channel=True,
        connect=True,
        speak=True,
        stream=True,
        use_voice_activation=True,
        use_soundboard=True,
        use_external_sounds=True,
        priority_speaker=True,
        mute_members=True,
        deafen_members=True,
        move_members=True,
    )


def _voice_afk() -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(
        view_channel=True,
        connect=True,
        speak=False,
        stream=False,
        use_voice_activation=True,
        use_soundboard=False,
        use_external_sounds=False,
        priority_speaker=False,
        mute_members=False,
        deafen_members=False,
        move_members=False,
    )


DENY = _deny_all()


# --- Guild (server) role permissions ---


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
    if name == "Automated Systems":
        return discord.Permissions(
            view_channel=True,
            send_messages=True,
            embed_links=True,
            attach_files=True,
            read_message_history=True,
            use_application_commands=True,
            manage_messages=True,
            connect=True,
        )
    return discord.Permissions.none()


def _role_color(name: str) -> int:
    if name in COMMAND:
        return 0x1E3A8A
    if name == "Police Officer":
        return 0x2563EB
    if name in ("Cadet", "Municipal PD Academy"):
        return 0x60A5FA
    if name.startswith("Strike") or name in ("Suspended", "Blacklisted", "Under Investigation"):
        return 0xDC2626
    return 0x64748B


@dataclass
class SetupReport:
    channels_deleted: int = 0
    categories_deleted: int = 0
    roles_deleted: int = 0
    roles_skipped: int = 0
    roles_created: int = 0
    categories_created: int = 0
    channels_created: int = 0
    overwrites_set: int = 0
    afk_configured: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_embed(self) -> discord.Embed:
        color = 0x27AE60 if self.ok else 0xF39C12
        embed = discord.Embed(
            title="Wytheville Police Department Rebuild Complete",
            description=(
                "The PD server was wiped and rebuilt from the WPD template. "
                "@everyone and roles above the bot were preserved where required."
            ),
            color=color,
        )
        embed.add_field(
            name="Removed",
            value=(
                f"Channels **{self.channels_deleted}** · Categories **{self.categories_deleted}** · "
                f"Roles **{self.roles_deleted}** · Roles not removed **{self.roles_skipped}**"
            ),
            inline=False,
        )
        embed.add_field(
            name="Created",
            value=(
                f"Roles **{self.roles_created}** · Categories **{self.categories_created}** · "
                f"Channels **{self.channels_created}**"
            ),
            inline=False,
        )
        embed.add_field(
            name="Permissions",
            value=(
                f"Channel overwrites applied: **{self.overwrites_set}** · "
                f"AFK channel: **{'yes' if self.afk_configured else 'no'}**"
            ),
            inline=False,
        )
        if self.errors:
            body = "\n".join(f"• {e}"[:200] for e in self.errors[:12])
            if len(self.errors) > 12:
                body += f"\n• … and {len(self.errors) - 12} more (see logs)."
            embed.add_field(name="Issues", value=body, inline=False)
            embed.set_footer(text="Setup finished with errors — review issues above.")
        else:
            embed.set_footer(text="Running /pd setup again will wipe and rebuild the PD server.")
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


def _readonly_plus_staff(
    ow: dict[discord.abc.Snowflake, discord.PermissionOverwrite],
    rmap: dict[str, discord.Role],
    *,
    viewers: tuple[str, ...],
    staff: tuple[str, ...],
) -> None:
    _add_roles(ow, rmap, viewers, _text_readonly_member())
    _add_roles(ow, rmap, staff, _text_staff_maintain())


def _add_bot_post(
    ow: dict[discord.abc.Snowflake, discord.PermissionOverwrite],
    rmap: dict[str, discord.Role],
) -> None:
    bot = _r(rmap, "Automated Systems")
    if bot:
        ow[bot] = _text_bot_service()


def _build_overwrites(
    guild: discord.Guild,
    rmap: dict[str, discord.Role],
    template: str,
) -> dict[discord.abc.Snowflake, discord.PermissionOverwrite]:
    ow: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {guild.default_role: DENY}

    if template == "info_readonly":
        _readonly_plus_staff(ow, rmap, viewers=INFO_VIEWERS, staff=INFO_STAFF)
    elif template == "recruit_info_readonly":
        _readonly_plus_staff(ow, rmap, viewers=ACADEMY_VIEWERS, staff=RECRUITMENT_STAFF)
    elif template == "applications_readonly":
        _readonly_plus_staff(ow, rmap, viewers=("Cadet", "Civilian"), staff=RECRUITMENT_STAFF)
        _add_bot_post(ow, rmap)
    elif template == "application_status_readonly":
        _readonly_plus_staff(ow, rmap, viewers=("Cadet", "Civilian"), staff=RECRUITMENT_STAFF)
        _add_bot_post(ow, rmap)
    elif template == "patrol_info_readonly":
        _readonly_plus_staff(ow, rmap, viewers=PATROL_ACCESS, staff=SUPERVISION)
    elif template == "patrol_briefings_readonly":
        _readonly_plus_staff(ow, rmap, viewers=PATROL_ACCESS, staff=SUPERVISION)
    elif template == "ia_info_readonly":
        _readonly_plus_staff(ow, rmap, viewers=IA_ACCESS, staff=("Chief of Police", "Deputy Chief"))
    elif template == "community_general":
        _add_roles(ow, rmap, DEPT_COMMUNITY, _text_comm_member())
    elif template == "recruit_chat_comm":
        _add_roles(ow, rmap, RECRUITMENT_STAFF + ("Cadet", "Municipal PD Academy"), _text_comm_member())
    elif template == "patrol_chat_comm":
        _add_roles(ow, rmap, PATROL_ACCESS, _text_comm_member())
        _add_roles(ow, rmap, SUPERVISION, _text_comm_supervisor())
    elif template == "unit_traffic":
        _add_roles(ow, rmap, ("Traffic Unit",), _text_comm_member())
        _add_roles(ow, rmap, COMMAND, _text_comm_command())
    elif template == "unit_cid":
        _add_roles(ow, rmap, ("Criminal Investigations Division",), _text_comm_member())
        _add_roles(ow, rmap, COMMAND, _text_comm_command())
    elif template == "unit_k9":
        _add_roles(ow, rmap, ("K-9 Unit",), _text_comm_member())
        _add_roles(ow, rmap, COMMAND, _text_comm_command())
    elif template == "unit_swat":
        _add_roles(ow, rmap, ("SWAT / Special Operations",), _text_comm_member())
        _add_roles(ow, rmap, COMMAND, _text_comm_command())
    elif template == "supervision_chat_comm":
        _add_roles(ow, rmap, SUPERVISION + ("Training Division",), _text_comm_supervisor())
    elif template == "supervision_personnel_admin":
        _add_roles(ow, rmap, SUPERVISION_PERSONNEL, _text_comm_supervisor())
    elif template == "ia_comm":
        _add_roles(ow, rmap, IA_ACCESS, _text_comm_member())
    elif template == "command_chat_comm":
        _add_roles(ow, rmap, COMMAND, _text_comm_member())
    elif template == "command_admin_comm":
        _add_roles(ow, rmap, COMMAND, _text_comm_supervisor())
    elif template == "personnel_comm":
        _add_roles(ow, rmap, COMMAND + ("Internal Affairs",), _text_comm_supervisor())
    elif template == "voice_lobby":
        _add_roles(
            ow,
            rmap,
            SWORN + ("Cadet", "Municipal PD Academy", "Department Support"),
            _voice_member(),
        )
    elif template == "voice_training":
        _add_roles(ow, rmap, TRAINING_VOICE, _voice_training())
    elif template == "voice_command":
        _add_roles(ow, rmap, COMMAND, _voice_command())
    elif template == "voice_afk":
        ow[guild.default_role] = _voice_afk()
    else:
        ow[guild.default_role] = _text_readonly_member()

    return ow


def _bot_ceiling(guild: discord.Guild) -> discord.Role | None:
    me = guild.me
    if not me or not me.top_role:
        return None
    return me.top_role


def _role_may_delete(guild: discord.Guild, role: discord.Role, ceiling: discord.Role | None) -> bool:
    if role.is_default():
        return False
    if role.managed:
        return False
    if ceiling and role >= ceiling:
        return False
    return True


async def _wipe_channels_and_categories(guild: discord.Guild, report: SetupReport) -> None:
    non_categories = [
        ch for ch in guild.channels if not isinstance(ch, discord.CategoryChannel)
    ]
    for channel in sorted(non_categories, key=lambda c: c.position, reverse=True):
        try:
            await channel.delete(reason="WPD setup: full rebuild")
            report.channels_deleted += 1
            await asyncio.sleep(0.12)
        except discord.Forbidden:
            report.errors.append(f"No permission to delete channel #{channel.name}.")
        except discord.HTTPException as exc:
            report.errors.append(f"Delete channel #{channel.name}: {exc}")

    for category in sorted(guild.categories, key=lambda c: c.position, reverse=True):
        try:
            await category.delete(reason="WPD setup: full rebuild")
            report.categories_deleted += 1
            await asyncio.sleep(0.12)
        except discord.Forbidden:
            report.errors.append(f"No permission to delete category {category.name}.")
        except discord.HTTPException as exc:
            report.errors.append(f"Delete category {category.name}: {exc}")


async def _wipe_custom_roles(guild: discord.Guild, report: SetupReport) -> None:
    ceiling = _bot_ceiling(guild)
    if not ceiling:
        report.errors.append("Cannot delete roles: bot member or top role unavailable.")
        return

    for role in sorted(guild.roles, key=lambda r: r.position, reverse=True):
        if not _role_may_delete(guild, role, ceiling):
            if not role.is_default() and not role.managed:
                report.roles_skipped += 1
            continue
        try:
            await role.delete(reason="WPD setup: full rebuild")
            report.roles_deleted += 1
            await asyncio.sleep(0.15)
        except discord.Forbidden:
            report.roles_skipped += 1
            report.errors.append(f"Could not delete role @{role.name} (forbidden).")
        except discord.HTTPException as exc:
            report.roles_skipped += 1
            report.errors.append(f"Delete role @{role.name}: {exc}")


async def _create_wpd_role(
    guild: discord.Guild,
    name: str,
    report: SetupReport,
) -> discord.Role | None:
    perms = _guild_role_permissions(name)
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


async def _create_wpd_category(
    guild: discord.Guild,
    name: str,
    position: int,
    report: SetupReport,
) -> discord.CategoryChannel | None:
    try:
        category = await guild.create_category(
            name,
            position=position,
            overwrites={guild.default_role: DENY},
            reason="WPD setup",
        )
        report.categories_created += 1
        await asyncio.sleep(0.2)
        return category
    except discord.HTTPException as exc:
        report.errors.append(f"Category create {name}: {exc}")
        return None


async def _create_wpd_channel(
    guild: discord.Guild,
    category: discord.CategoryChannel,
    ch_name: str,
    kind: ChannelKind,
    position: int,
    rmap: dict[str, discord.Role],
    template: str,
    report: SetupReport,
) -> discord.abc.GuildChannel | None:
    overwrites = _build_overwrites(guild, rmap, template)
    try:
        if kind == "voice":
            channel = await guild.create_voice_channel(
                ch_name,
                category=category,
                position=position,
                overwrites=overwrites,
                reason="WPD setup",
            )
        else:
            channel = await guild.create_text_channel(
                ch_name,
                category=category,
                position=position,
                overwrites=overwrites,
                reason="WPD setup",
            )
        report.channels_created += 1
        report.overwrites_set += 1
        await asyncio.sleep(0.2)
        return channel
    except discord.HTTPException as exc:
        report.errors.append(f"Channel create {ch_name}: {exc}")
        return None


async def _configure_afk_channel(guild: discord.Guild, report: SetupReport) -> None:
    afk = discord.utils.get(guild.voice_channels, name="AFK")
    if not afk:
        report.errors.append("AFK voice channel was not found after rebuild.")
        return
    try:
        await guild.edit(afk_channel=afk, afk_timeout=300, reason="WPD setup")
        report.afk_configured = True
    except discord.HTTPException as exc:
        report.errors.append(f"Could not set server AFK channel: {exc}")


async def run_wpd_setup(guild: discord.Guild) -> SetupReport:
    """Wipe PD channels/categories/roles (within bot limits) and rebuild WPD from scratch."""
    report = SetupReport()

    await _wipe_channels_and_categories(guild, report)
    await _wipe_custom_roles(guild, report)

    rmap: dict[str, discord.Role] = {}
    for name in WPD_ROLE_ORDER:
        role = await _create_wpd_role(guild, name, report)
        if role:
            rmap[name] = role

    await _reorder_roles(guild, rmap, report)

    for cat_index, (cat_name, channels) in enumerate(WPD_CATEGORIES):
        category = await _create_wpd_category(guild, cat_name, cat_index, report)
        if not category:
            continue

        for ch_index, (ch_name, ch_kind, template) in enumerate(channels):
            await _create_wpd_channel(
                guild,
                category,
                ch_name,
                ch_kind,
                ch_index,
                rmap,
                template,
                report,
            )

    await _configure_afk_channel(guild, report)

    bot_role = _r(rmap, "Automated Systems")
    me = guild.me
    if bot_role and me and bot_role not in me.roles:
        try:
            await me.add_roles(bot_role, reason="WPD setup")
        except discord.HTTPException as exc:
            report.errors.append(f"Could not assign Automated Systems to bot: {exc}")

    return report
