import asyncio
import discord
from discord import app_commands
from discord.ext import commands

from utils.core import load_config, get_state, set_state
from utils.database import DatabaseError, DatabaseTimeoutError
from utils.helpers import department_display_name, get_emoji
from utils.permissions import is_staff


def _dept_roles(guild: discord.Guild, department: str) -> list[discord.Role]:
    dept_cfg = load_config().get("roles", {}).get("departments", {})
    role_ids = dept_cfg.get(department, [])
    if isinstance(role_ids, int):
        role_ids = [role_ids]
    roles = []
    for rid in role_ids:
        role = guild.get_role(rid)
        if role:
            roles.append(role)
    return roles


class DepartmentPingView(discord.ui.View):
    def __init__(self, events: dict):
        super().__init__(timeout=None)
        for event_id, event in events.items():
            self.add_item(DepartmentPingButton(event_id, event))


class DepartmentPingButton(discord.ui.Button):
    def __init__(self, event_id: str, event: dict):
        kwargs = {
            "label": event.get("label", event_id.replace("_", " ").title()),
            "style": discord.ButtonStyle.danger,
            "custom_id": f"dept_ping_{event_id}",
        }
        emoji = get_emoji(f"dept_{event_id}") or event.get("emoji")
        if emoji:
            kwargs["emoji"] = emoji
        super().__init__(**kwargs)
        self.event_id = event_id
        self.event = event

    async def callback(self, interaction: discord.Interaction):
        if not is_staff(interaction.user):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return

        departments = self.event.get("departments") or [self.event.get("department")]
        if not departments or not departments[0]:
            await interaction.response.send_message("Department not configured.", ephemeral=True)
            return

        roles = []
        for dept in departments:
            roles.extend(_dept_roles(interaction.guild, dept))
        if not roles:
            await interaction.response.send_message("Department roles not found.", ephemeral=True)
            return

        message = self.event.get("message", f"{self.event.get('label', self.event_id)}. Respond immediately.")
        channel_id = load_config().get("channels", {}).get("department_pings")
        channel = interaction.guild.get_channel(channel_id) if channel_id else interaction.channel
        if channel is None:
            channel = interaction.channel

        mentions = " ".join(r.mention for r in roles)
        embed = discord.Embed(
            title=self.event.get("label", "Department Alert"),
            description=message,
            color=0xE74C3C,
        )
        embed.set_footer(text=f"Triggered by {interaction.user.display_name}")

        await channel.send(content=mentions, embed=embed, allowed_mentions=discord.AllowedMentions(roles=True))
        await interaction.response.send_message(
            f"Department alert sent for {self.event.get('label')}.",
            ephemeral=True,
        )


class DepartmentPings(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _ensure_posted(self):
        await self.bot.wait_until_ready()
        try:
            await asyncio.sleep(1)
            await self.post_panel()
        except Exception:
            return

    def _events(self) -> dict:
        return load_config().get("department_pings", {}).get("events", {})

    # dept-ping-panel command removed — department pager panel is auto-posted on startup.

    async def setup_persistent_views(self):
        events = self._events()
        if events:
            self.bot.add_view(DepartmentPingView(events))

    async def post_panel(self):
        events = self._events()
        if not events:
            return
        channel_id = load_config().get("channels", {}).get("department_pings")
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
            title="Department Pager",
            description="Staff: click a button below to page the corresponding department.",
            color=0xE74C3C,
        )
        for event_id, event in events.items():
            depts = event.get("departments") or [event.get("department", "unknown")]
            dept_labels = ", ".join(department_display_name(dept) for dept in depts)
            embed.add_field(
                name=event.get("label", event_id),
                value=f"Pages: {dept_labels}",
                inline=True,
            )
        view = DepartmentPingView(events)
        state_key = f"dept_ping_panel:{channel_id}"
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
                if title == "Department Pager":
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
            state_key = f"dept_ping_panel:{channel_id}"
            msg_id = await get_state(state_key)
            if msg_id and str(payload.message_id) == str(msg_id):
                await self.post_panel()
        except Exception:
            return


async def setup(bot: commands.Bot):
    cog = DepartmentPings(bot)
    await bot.add_cog(cog)
    await cog.setup_persistent_views()
    bot.loop.create_task(cog._ensure_posted())
