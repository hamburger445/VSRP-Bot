import discord
from discord import app_commands
from discord.ext import commands

from utils.core import load_config
from utils.core import is_main_guild, is_shift_guild
from utils.core import reply
from utils.permissions import is_leo, is_owner, is_staff


def _prefix() -> str:
    return load_config().get("bot", {}).get("prefix", "-")


def _help_sections() -> dict[str, dict]:
    p = _prefix()
    return {
        "general": {
            "label": "General",
            "description": "Commands for all members",
            "visible": lambda m: True,
            "commands": [
                ("/help", "Open this command list"),
            ],
        },
        "main_rp": {
            "label": "Main RP Server",
            "description": "Economy, profiles, sessions (main server only)",
            "visible": lambda m: is_main_guild(m.guild.id),
            "commands": [
                ("/profile [user]", "View a profile dashboard with registrations, tickets, and more"),
                ("/session-status", "Check if a roleplay session is active"),
                ("/startup / /over", "Session management (staff)"),
            ],
        },
        "shifts": {
            "label": "Shift Monitor",
            "description": "FD and PD shift tracking",
            "visible": lambda m: is_shift_guild(m.guild.id),
            "commands": [
                ("/shift manage", "Start, break, and stop your shift (buttons)"),
                ("/shift admin @user", "Staff panel: shift controls + admin actions dropdown"),
            ],
        },
        "economy": {
            "label": "Economy & Games",
            "description": "Money, shop, and games (main server only)",
            "visible": lambda m: is_main_guild(m.guild.id),
            "commands": [
                ("/economy balance", "View your wallet and bank balance"),
                ("/economy daily", "Claim your daily reward"),
                ("/economy work", "Work a shift to earn money"),
                ("/economy pay @user amount", "Pay another member"),
                ("/economy leaderboard", "View the richest members"),
                ("/shop", "Browse and buy from the shop (buttons)"),
                ("/blackjack amount", "Play a hand of blackjack"),
            ],
        },
        "registration": {
            "label": "Registration & Tickets",
            "description": "Vehicles, trailers, and citations (main server only)",
            "visible": lambda m: is_main_guild(m.guild.id),
            "commands": [
                ("/registervehicle", "Register a vehicle to your profile"),
                ("/registertrailer", "Register a trailer to your profile"),
                ("/payticket ticket_id", "Pay a citation ticket by ID"),
            ],
        },
        "leo": {
            "label": "Law Enforcement",
            "description": "Police and LEO tools (main server only)",
            "visible": lambda m: is_main_guild(m.guild.id) and is_leo(m),
            "commands": [
                ("/citation @user violation fine", "Issue a citation ticket"),
                ("/warrant @user reason", "Issue a warrant"),
                ("/removewarrant warrant_id", "Clear a warrant by ID"),
                ("/warrant-check @user", "View active warrants for a member"),
                ("/pager send department message", "Page a department"),
                ("/leo-panel", "Open the law enforcement panel"),
            ],
        },
        "setup": {
            "label": "Server Setup",
            "description": "Configure this server for the bot",
            "visible": lambda m: is_staff(m),
            "commands": [
                ("/setup-permissions", "Configure permissions for this server (main vs FD/PD options differ)"),
                (f"{p}role mod warn moderate", "Assign bot permissions to a role (partial names work)"),
            ],
        },
        "staff": {
            "label": "Staff",
            "description": "Session, panels, and admin tools",
            "visible": lambda m: is_staff(m) and is_main_guild(m.guild.id),
            "commands": [
                ("/startup reactions", "Start a session vote with check reactions"),
                ("/over", "End the session and purge the startup channel"),
                ("/ticket-panel", "Post the support ticket panel"),
                ("/info-panel", "Post the server information panel"),
                ("/post-rules", "Post server rules to the rules channel"),
                ("/strike @user", "Issue a strike (modal, creates case + appeal button)"),
                ("/modban @user", "Record a ban (modal, creates case + appeal button)"),
                ("/dept-ping-panel", "Post the department pager panel"),
                ("/reactionrole create", "Create a reaction role panel"),
                ("/reactionrole add", "Add an emoji-to-role mapping"),
                ("/reactionrole remove", "Remove an emoji mapping"),
                ("/economyadmin add @user amount", "Add money to a wallet"),
                ("/economyadmin remove @user amount", "Remove money from a wallet"),
                ("/economyadmin freeze @user", "Freeze an economy account"),
                ("/economyadmin unfreeze @user", "Unfreeze an economy account"),
            ],
        },
        "moderation": {
            "label": "Moderation",
            "description": f"Prefix commands ({p}) — all servers",
            "visible": lambda m: is_staff(m),
            "commands": [
                (f"{p}modlogs @user", "View a member's moderation cases"),
                (f"{p}case <#>", "View a moderation case (includes delete button)"),
                (f"{p}case delete <#>", "Remove a case from a member's record"),
                (f"{p}reason <#> text", "Change a case reason"),
                (f"{p}ban @user reason", "Ban a member (admin only)"),
                (f"{p}kick @user reason", "Kick a member"),
                (f"{p}mute @user duration reason", "Timeout a member (e.g. 1h, 30m)"),
                (f"{p}unmute @user", "Remove a member timeout"),
                (f"{p}unban user_id reason", "Unban a user by ID"),
                (f"{p}warn @user reason", "Warn a member"),
                (f"{p}purge number", "Delete messages (any amount, batched)"),
            ],
        },
        "owner": {
            "label": "Owner",
            "description": "Owner-only management",
            "visible": lambda m: is_owner(m),
            "commands": [
                ("/owner-panel @user", "Open the owner management panel"),
                ("/say message [channel]", "Make the bot say something"),
            ],
        },
    }


def _visible_section_ids(member: discord.Member) -> list[str]:
    sections = _help_sections()
    return [key for key, section in sections.items() if section["visible"](member)]


def _section_embed(section_id: str) -> discord.Embed:
    section = _help_sections()[section_id]
    lines = [f"**{cmd}**\n{desc}" for cmd, desc in section["commands"]]
    embed = discord.Embed(
        title=f"Help | {section['label']}",
        description=section["description"],
        color=0x2B2D31,
    )
    body = "\n\n".join(lines)
    if len(body) <= 4096:
        embed.description = f"{section['description']}\n\n{body}"
    else:
        embed.add_field(name="Commands", value=body[:1024], inline=False)
    return embed


class HelpCategorySelect(discord.ui.Select):
    def __init__(self, member: discord.Member):
        sections = _help_sections()
        options = [
            discord.SelectOption(
                label=sections[sid]["label"],
                value=sid,
                description=sections[sid]["description"][:100],
            )
            for sid in _visible_section_ids(member)
        ]
        super().__init__(placeholder="Choose a command category...", options=options)

    async def callback(self, interaction: discord.Interaction):
        embed = _section_embed(self.values[0])
        await interaction.response.edit_message(embed=embed, view=self.view)


class HelpView(discord.ui.View):
    def __init__(self, member: discord.Member):
        super().__init__(timeout=180)
        self.add_item(HelpCategorySelect(member))


class Help(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="help", description="View available commands for your rank")
    async def help_cmd(self, interaction: discord.Interaction):
        if not isinstance(interaction.user, discord.Member):
            await reply(interaction, "This command can only be used in a server.", ephemeral=True)
            return

        visible = _visible_section_ids(interaction.user)
        if not visible:
            await reply(interaction, "No help categories available.", ephemeral=True)
            return

        rank_parts = ["Member"]
        if is_leo(interaction.user):
            rank_parts.append("Law Enforcement")
        if is_staff(interaction.user):
            rank_parts.append("Staff")
        if is_owner(interaction.user):
            rank_parts.append("Owner")
        rank_parts = list(dict.fromkeys(rank_parts))

        embed = discord.Embed(
            title="VSRP Bot Help",
            description=(
                f"Your access level: **{' + '.join(rank_parts)}**\n\n"
                "Select a category below to view commands available to you."
            ),
            color=0x3498DB,
        )
        view = HelpView(interaction.user)
        await reply(interaction, embed=embed, view=view, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Help(bot))
