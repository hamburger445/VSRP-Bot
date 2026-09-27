"""Core utilities: config, guilds, interactions, datetime, command sync, bot state."""

import asyncio
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import discord
import yaml
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"
EXAMPLE_CONFIG_PATH = ROOT / "config.example.yaml"
ENV_PATH = ROOT / ".env"
_config_materialized = False

load_dotenv(ENV_PATH, override=True)

log = logging.getLogger("vsrp_bot.core")

try:
    from zoneinfo import ZoneInfo

    _DISPLAY_TZ = ZoneInfo("America/New_York")
except Exception:
    _DISPLAY_TZ = timezone(timedelta(hours=-5))


# --- config ---


def _load_example_config() -> dict[str, Any]:
    if not EXAMPLE_CONFIG_PATH.exists():
        return {}
    with open(EXAMPLE_CONFIG_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


def _skip_merge_key(key: Any) -> bool:
    return isinstance(key, str) and key.startswith("_")


def _deep_merge_missing(
    base: dict[Any, Any],
    defaults: dict[Any, Any],
) -> tuple[dict[Any, Any], bool]:
    """Fill missing keys from defaults. Existing values in base are never overwritten."""
    merged = dict(base)
    changed = False
    for key, default_value in defaults.items():
        if _skip_merge_key(key):
            continue
        if key not in merged:
            merged[key] = default_value
            changed = True
        elif isinstance(merged[key], dict) and isinstance(default_value, dict):
            nested, nested_changed = _deep_merge_missing(merged[key], default_value)
            if nested_changed:
                merged[key] = nested
                changed = True
    return merged, changed


def _ensure_config_file() -> None:
    if CONFIG_PATH.exists():
        return
    if EXAMPLE_CONFIG_PATH.exists():
        CONFIG_PATH.write_bytes(EXAMPLE_CONFIG_PATH.read_bytes())
        log.info("Created config.yaml from config.example.yaml")
        return
    raise FileNotFoundError(
        "config.yaml not found. Copy config.example.yaml to config.yaml with your server settings."
    )


def materialize_config_from_example() -> bool:
    """Write new keys from config.example.yaml into config.yaml (once per process if changed)."""
    global _config_materialized
    _ensure_config_file()
    with open(CONFIG_PATH, encoding="utf-8") as f:
        current = yaml.safe_load(f) or {}
    if not isinstance(current, dict):
        current = {}
    example = _load_example_config()
    merged, changed = _deep_merge_missing(current, example)
    if changed and not _config_materialized:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(merged, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        _config_materialized = True
        log.info("config.yaml updated with new keys from config.example.yaml")
        return True
    _config_materialized = True
    return False


def load_config() -> dict[str, Any]:
    _ensure_config_file()
    with open(CONFIG_PATH, encoding="utf-8") as f:
        current = yaml.safe_load(f) or {}
    if not isinstance(current, dict):
        current = {}
    example = _load_example_config()
    merged, _ = _deep_merge_missing(current, example)
    return merged


def _clean_env(value: str) -> str:
    return value.strip().strip('"').strip("'")


def get_token() -> str:
    token = _clean_env(os.getenv("DISCORD_TOKEN", ""))
    if not token or token == "your_bot_token_here":
        raise ValueError(
            "DISCORD_TOKEN is missing. Upload a .env file to your host or set "
            "DISCORD_TOKEN in your hosting panel's environment variables."
        )
    if token.count(".") != 2:
        raise ValueError(
            "DISCORD_TOKEN looks invalid. Copy the full Bot Token from "
            "Discord Developer Portal → Your App → Bot → Reset Token."
        )
    return token


def get_database_url() -> str:
    url = _clean_env(os.getenv("DATABASE_URL", ""))
    if not url:
        raise ValueError(
            "DATABASE_URL is missing. Add it to .env or your hosting panel environment variables."
        )
    return url


def get_erlc_server_key() -> str | None:
    key = _clean_env(os.getenv("ERLC_SERVER_KEY", ""))
    return key or None


def get_github_token() -> str | None:
    token = _clean_env(os.getenv("GITHUB_TOKEN", ""))
    return token or None


def get_github_repo() -> str:
    return load_config().get("github", {}).get("repo", "hamburger445/VSRP-Bot")


# --- guilds ---


def _servers_cfg() -> dict:
    return load_config().get("servers", {})


def main_guild_id() -> int:
    cfg = load_config()
    main = _servers_cfg().get("main", {})
    if main.get("id"):
        return int(main["id"])
    return int(cfg.get("guild", {}).get("id", 0))


def fd_guild_id() -> int:
    return int(_servers_cfg().get("fd", {}).get("id", 0))


def pd_guild_id() -> int:
    return int(_servers_cfg().get("pd", {}).get("id", 0))


def shift_guild_ids() -> list[int]:
    return [gid for gid in [fd_guild_id(), pd_guild_id()] if gid]


def all_guild_ids() -> list[int]:
    return [gid for gid in [main_guild_id()] + shift_guild_ids() if gid]


def is_main_guild(guild_id: int | None) -> bool:
    return guild_id == main_guild_id()


def is_shift_guild(guild_id: int | None) -> bool:
    return guild_id in shift_guild_ids()


def guild_objects(guild_ids: list[int]) -> list[discord.Object]:
    return [discord.Object(id=gid) for gid in guild_ids if gid]


def main_guild_objects() -> list[discord.Object]:
    return guild_objects([main_guild_id()])


def shift_guild_objects() -> list[discord.Object]:
    return guild_objects(shift_guild_ids())


def all_guild_objects() -> list[discord.Object]:
    return guild_objects(all_guild_ids())


def _move_command_to_guilds(
    bot: commands.Bot,
    command: app_commands.Command | app_commands.Group,
    guilds: list[discord.Object],
) -> None:
    if not guilds or command.parent is not None:
        return
    try:
        bot.tree.remove_command(command.name, guild=None)
    except Exception:
        pass
    for guild in guilds:
        try:
            bot.tree.add_command(command, guild=guild, override=True)
        except Exception as exc:
            log.warning("Could not register /%s to guild %s: %s", command.name, guild.id, exc)


def restrict_cog_guilds(
    bot: commands.Bot,
    cog: commands.Cog,
    guilds: list[discord.Object],
) -> None:
    for command in cog.__cog_app_commands__:
        _move_command_to_guilds(bot, command, guilds)


def server_label(guild_id: int | None) -> str:
    if is_main_guild(guild_id):
        return "Main RP"
    if guild_id == fd_guild_id():
        return "Fire Department"
    if guild_id == pd_guild_id():
        return "City PD"
    return "Unknown"


# --- command sync ---

REQUIRED_MAIN_COMMANDS = frozenset({"profile", "shop", "economy"})
REQUIRED_SHIFT_COMMANDS = frozenset({"shift"})


def _command_names(commands_list: list[app_commands.AppCommand]) -> list[str]:
    return sorted(cmd.name for cmd in commands_list)


async def sync_app_commands(
    tree: app_commands.CommandTree,
    guild_ids: list[int],
) -> dict[int, list[str]]:
    """Push guild slash commands to Discord. Returns registered command names per guild."""
    results: dict[int, list[str]] = {}

    if not guild_ids:
        synced = await tree.sync()
        names = _command_names(synced)
        log.info("Synced %d global commands: %s", len(synced), ", ".join(names))
        return {0: names}

    tree.clear_commands(guild=None)
    try:
        await tree.sync()
    except discord.HTTPException as exc:
        log.warning("Could not clear global commands (non-fatal): %s", exc)

    for guild_id in guild_ids:
        guild = discord.Object(id=int(guild_id))
        try:
            synced = await tree.sync(guild=guild)
        except discord.HTTPException as exc:
            log.error("Failed to sync commands to guild %s: %s", guild_id, exc)
            results[int(guild_id)] = []
            continue

        names = _command_names(synced)
        log.info("Synced %d command(s) to guild %s", len(synced), guild_id)
        if names:
            log.info("Guild %s updated commands: %s", guild_id, ", ".join(names))
        else:
            log.info("Guild %s commands unchanged (already up to date)", guild_id)

        registered = _command_names(tree.get_commands(guild=guild))
        results[int(guild_id)] = registered
        log.info("Guild %s registered commands: %s", guild_id, ", ".join(registered) or "(none)")

        if guild_id == guild_ids[0]:
            missing_main = REQUIRED_MAIN_COMMANDS - set(registered)
            if missing_main:
                log.error("Main server missing commands: %s", ", ".join(sorted(missing_main)))

        if guild_id in guild_ids[1:]:
            missing_shift = REQUIRED_SHIFT_COMMANDS - set(registered)
            if missing_shift:
                log.warning("Shift guild %s missing commands: %s", guild_id, ", ".join(sorted(missing_shift)))

        if guild_id != guild_ids[-1]:
            await asyncio.sleep(1)

    return results


def format_sync_summary(results: dict[int, list[str]]) -> str:
    if not results:
        return "No guilds synced."
    lines: list[str] = []
    for guild_id, names in sorted(results.items()):
        if guild_id == 0:
            lines.append(f"Global: **{len(names)}** command(s)")
        else:
            lines.append(f"Guild `{guild_id}`: **{len(names)}** command(s)")
    return "\n".join(lines)


# --- interactions ---


async def defer(interaction: discord.Interaction, *, ephemeral: bool = True) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=ephemeral)


async def reply(interaction: discord.Interaction, *args, ephemeral: bool = False, **kwargs) -> None:
    if ephemeral:
        kwargs["ephemeral"] = True

    if args and isinstance(args[0], discord.Embed):
        if "embed" not in kwargs:
            kwargs["embed"] = args[0]
        args = args[1:]
    elif "embed" not in kwargs and len(args) == 1 and isinstance(args[0], str):
        embed = discord.Embed(description=args[0], color=0x2B2D31)
        kwargs["embed"] = embed
        args = ()

    if interaction.response.is_done():
        await interaction.followup.send(*args, **kwargs)
    else:
        await interaction.response.send_message(*args, **kwargs)


async def run_db_command(interaction: discord.Interaction, coro, *, ephemeral: bool = True):
    from utils.database import DatabaseError, DatabaseTimeoutError

    await defer(interaction, ephemeral=ephemeral)
    try:
        return await coro
    except DatabaseTimeoutError:
        await interaction.followup.send(
            "The database took too long to respond. Please try again in a moment.",
            ephemeral=True,
        )
    except DatabaseError:
        await interaction.followup.send(
            "A database error occurred. Please try again.",
            ephemeral=True,
        )
    return None


# --- datetime ---


def _parse_dt(value: datetime | date | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_DISPLAY_TZ)


def format_date(value: datetime | date | str | None) -> str:
    dt = _parse_dt(value)
    return dt.strftime("%m/%d/%Y") if dt else "N/A"


def format_time(value: datetime | date | str | None) -> str:
    dt = _parse_dt(value)
    return dt.strftime("%I:%M %p") if dt else "N/A"


def format_datetime(value: datetime | date | str | None) -> str:
    dt = _parse_dt(value)
    return f"{dt.strftime('%m/%d/%Y')} {dt.strftime('%I:%M %p')}" if dt else "N/A"


# --- bot state ---


async def get_state(key: str) -> str | None:
    from utils.database import get_db

    db = await get_db()
    row = await db.execute_fetchone("SELECT value FROM bot_state WHERE key = ?", (key,))
    return row["value"] if row else None


async def set_state(key: str, value: str) -> None:
    from utils.database import get_db

    db = await get_db()
    await db.execute(
        """
        INSERT INTO bot_state (key, value) VALUES (?, ?)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """,
        (key, value),
    )
    await db.commit()
