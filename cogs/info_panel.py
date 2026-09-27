import asyncio
import discord
from discord import app_commands
from discord.ext import commands

from utils.core import load_config, get_state, set_state
from utils.database import DatabaseError, DatabaseTimeoutError
from utils.helpers import get_emoji
from utils.permissions import is_staff
from utils.verification import is_verified_user


class InfoSectionButton(discord.ui.Button):
    def __init__(self, section_id: str, section: dict, row: int | None = None):
        url = (section.get("url") or "").strip()
        if url:
            super().__init__(
                label=section.get("button_label", section_id.title()),
                style=discord.ButtonStyle.link,
                url=url,
                row=row,
            )
            self.section_id = section_id
            self.section = section
            return
        kwargs = {
            "label": section.get("button_label", section_id.title()),
            "style": discord.ButtonStyle.secondary,
            "custom_id": f"info_panel_{section_id}",
            "row": row,
        }
        emoji = get_emoji(f"info_{section_id}") or section.get("button_emoji")
        if emoji:
            kwargs["emoji"] = emoji
        super().__init__(**kwargs)
        self.section_id = section_id
        self.section = section

    async def callback(self, interaction: discord.Interaction):
        if self.section_id in {"departments", "applications"}:
            member = interaction.user
            if not isinstance(member, discord.Member) or not await is_verified_user(member):
                embed = discord.Embed(
                    title="Verification Required",
                    description=(
                        "You must verify your Discord account before you can access department information or civilian applications.\n\n"
                        "Please complete verification in <#1250612127891197953>.\n\n"
                        "Once verified, you will automatically gain access to department information and applications."
                    ),
                    color=0xE74C3C,
                )
                embed.set_footer(text="WCRP Verification System")
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

        embed = discord.Embed(
            title=self.section.get("title", self.section_id.title()),
            description=self.section.get("content", "No content configured."),
            color=load_config().get("info_panel", {}).get("color", 0x3498DB),
        )
        url = self.section.get("url")
        if url:
            embed.add_field(name="Link", value=url, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class InfoPanelView(discord.ui.View):
    def __init__(self, sections: dict):
        super().__init__(timeout=None)
        for idx, (section_id, section) in enumerate(sections.items()):
            row = 0 if idx < 4 else 1
            self.add_item(InfoSectionButton(section_id, section, row=row))


class InfoPanel(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _ensure_posted(self):
        await self.bot.wait_until_ready()
        try:
            await asyncio.sleep(1)
            await self.post_panels()
        except Exception:
            return

    def _panel_config(self) -> dict:
        return load_config().get("info_panel", {})

    # Panel posting commands removed — panels are auto-posted and sticky on startup.

    async def setup_persistent_views(self):
        sections = self._panel_config().get("sections", {})
        link_only = {k: v for k, v in sections.items() if not v.get("url")}
        if link_only:
            self.bot.add_view(InfoPanelView(link_only))

    async def post_panels(self):
        cfg = self._panel_config()
        sections = cfg.get("sections", {})
        if sections:
            channel_id = load_config().get("channels", {}).get("info_panel")
            guild_id = load_config().get("guild", {}).get("id")
            if channel_id and guild_id:
                guild = self.bot.get_guild(int(guild_id))
                if guild:
                    channel = guild.get_channel(int(channel_id))
                    if isinstance(channel, discord.TextChannel):
                        embed = discord.Embed(
                            title=cfg.get("title", "Server Information"),
                            description=cfg.get("description", "Select a topic below."),
                            color=cfg.get("color", 0x3498DB),
                        )
                        footer_text = cfg.get("footer", "Thank you for being a member of our community.")
                        embed.set_footer(text=footer_text)
                        view = InfoPanelView(sections)
                        state_key = f"info_panel_message:{channel_id}"
                        try:
                            # try to fetch saved message id
                            msg_id = await get_state(state_key)
                            if msg_id:
                                try:
                                    msg = await channel.fetch_message(int(msg_id))
                                    try:
                                        await msg.edit(embed=embed, view=view)
                                        return
                                    except discord.NotFound:
                                        pass
                                except (discord.NotFound, discord.HTTPException, ValueError):
                                    pass
                            # scan recent messages for same embed title
                            async for message in channel.history(limit=50):
                                if message.author.id != self.bot.user.id or not message.embeds:
                                    continue
                                title = message.embeds[0].title or ""
                                if title == cfg.get("title", "Server Information"):
                                    try:
                                        await message.edit(embed=embed, view=view)
                                        await set_state(state_key, str(message.id))
                                        return
                                    except Exception:
                                        break
                            # send new
                            msg = await channel.send(embed=embed, view=view)
                            await set_state(state_key, str(msg.id))
                        except (DatabaseError, DatabaseTimeoutError):
                            # best-effort: send new message
                            try:
                                await channel.send(embed=embed, view=view)
                            except Exception:
                                pass

        # Post rules panel if configured
        rules_cfg = load_config().get("rules", {})
        rules_channel_id = load_config().get("channels", {}).get("rules")
        guild_id = load_config().get("guild", {}).get("id")
        if rules_cfg and rules_channel_id and guild_id:
            guild = self.bot.get_guild(int(guild_id))
            if guild:
                target = guild.get_channel(int(rules_channel_id))
                if isinstance(target, discord.TextChannel):
                    embed = discord.Embed(
                        title=rules_cfg.get("title", "Server Rules"),
                        description=rules_cfg.get("content", "Rules not configured yet."),
                        color=rules_cfg.get("color", 0xE74C3C),
                    )
                    state_key = f"rules_panel_message:{rules_channel_id}"
                    try:
                        msg_id = await get_state(state_key)
                        if msg_id:
                            try:
                                msg = await target.fetch_message(int(msg_id))
                                try:
                                    await msg.edit(embed=embed)
                                    return
                                except discord.NotFound:
                                    pass
                            except (discord.NotFound, discord.HTTPException, ValueError):
                                pass
                        async for message in target.history(limit=50):
                            if message.author.id != self.bot.user.id or not message.embeds:
                                continue
                            title = message.embeds[0].title or ""
                            if title == rules_cfg.get("title", "Server Rules"):
                                try:
                                    await message.edit(embed=embed)
                                    await set_state(state_key, str(message.id))
                                    return
                                except Exception:
                                    break
                        msg = await target.send(embed=embed)
                        await set_state(state_key, str(msg.id))
                    except (DatabaseError, DatabaseTimeoutError):
                        try:
                            await target.send(embed=embed)
                        except Exception:
                            pass

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        # If an info or rules panel was deleted, repost it
        try:
            channel_id = payload.channel_id
            state_key_info = f"info_panel_message:{channel_id}"
            state_key_rules = f"rules_panel_message:{channel_id}"
            info_id = await get_state(state_key_info)
            rules_id = await get_state(state_key_rules)
            if str(payload.message_id) == str(info_id) or str(payload.message_id) == str(rules_id):
                await self.post_panels()
        except Exception:
            return


async def setup(bot: commands.Bot):
    cog = InfoPanel(bot)
    await bot.add_cog(cog)
    await cog.setup_persistent_views()
    # schedule auto-post of configured panels after bot is ready
    bot.loop.create_task(cog._ensure_posted())
