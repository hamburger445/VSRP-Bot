import discord

from utils.core import load_config


def get_theme_color() -> int:
    return load_config().get("info_panel", {}).get("color", 0x3498DB)


def build_embed(
    title: str,
    description: str | None = None,
    color: int | None = None,
    footer: str | None = None,
    timestamp: discord.Object | None = None,
) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=description or discord.Embed.Empty,
        color=color or get_theme_color(),
        timestamp=timestamp,
    )
    if footer:
        embed.set_footer(text=footer)
    return embed


def build_error_embed(title: str, description: str, footer: str | None = None) -> discord.Embed:
    return build_embed(title=title, description=description, color=0xE74C3C, footer=footer)


def build_success_embed(title: str, description: str, footer: str | None = None) -> discord.Embed:
    return build_embed(title=title, description=description, color=0x2ECC71, footer=footer)
