"""CAD Discord integration: /cad commands and outbox delivery."""

from __future__ import annotations

import asyncio
import json
import logging

import discord
from discord import app_commands
from discord.ext import commands

from utils.core import load_config, reply
from utils.database import get_db
from utils.helpers import (
    department_display_name,
    log_action,
    notify_citation_issued,
    notify_ticket_paid,
    notify_warrant_cleared,
    notify_warrant_issued,
)
from utils.leo_forms import build_citation_embed, build_warrant_embed
from utils.permissions import is_leo, is_staff
from utils.tickets import enforce_ticket_thresholds

log = logging.getLogger("vsrp_bot.cad")


def _cad_url() -> str:
    cfg = load_config().get("cad") or {}
    import os

    return (os.getenv("CAD_PUBLIC_URL") or cfg.get("public_url") or "https://cad.example.invalid").rstrip("/")


def _dept_roles(guild: discord.Guild, department: str) -> list[discord.Role]:
    dept_cfg = load_config().get("roles", {}).get("departments", {})
    val = dept_cfg.get(department)
    role_ids = [val] if isinstance(val, int) else (val or [])
    return [r for rid in role_ids if (r := guild.get_role(int(rid)))]


class Cad(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._task: asyncio.Task | None = None

    async def cog_load(self):
        self._task = asyncio.get_running_loop().create_task(self._outbox_loop())

    async def cog_unload(self):
        if self._task:
            self._task.cancel()

    async def _outbox_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                await self._drain_outbox()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("CAD outbox drain failed")
            await asyncio.sleep(2)

    async def _drain_outbox(self):
        db = await get_db()
        pool = db._pool
        async with pool.acquire() as conn:
            exists = await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_name = 'cad_outbox'
                )
                """
            )
            if not exists:
                return
            rows = await conn.fetch(
                """
                UPDATE cad_outbox o
                SET locked_at = NOW(), attempts = o.attempts + 1
                FROM (
                    SELECT id FROM cad_outbox
                    WHERE delivered_at IS NULL AND available_at <= NOW()
                      AND (locked_at IS NULL OR locked_at < NOW() - INTERVAL '2 minutes')
                    ORDER BY id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 10
                ) s
                WHERE o.id = s.id
                RETURNING o.*
                """
            )
            for row in rows:
                try:
                    await self._deliver(dict(row))
                    await conn.execute(
                        "UPDATE cad_outbox SET delivered_at = NOW(), last_error = NULL WHERE id = $1",
                        row["id"],
                    )
                except Exception as exc:
                    log.warning("CAD outbox %s failed: %s", row["id"], exc)
                    await conn.execute(
                        "UPDATE cad_outbox SET locked_at = NULL, last_error = $2 WHERE id = $1",
                        row["id"],
                        str(exc)[:500],
                    )

    async def _deliver(self, row: dict):
        payload = json.loads(row["payload_json"] or "{}")
        kind = row["kind"]
        guild_id = load_config().get("servers", {}).get("main", {}).get("id") or load_config().get("guild", {}).get("id")
        guild = self.bot.get_guild(int(guild_id)) if guild_id else None

        if kind == "dm_code":
            user_id = int(payload["user_id"])
            code = payload["code"]
            user = await self.bot.fetch_user(user_id)
            await user.send(
                embed=discord.Embed(
                    title="VSRP CAD verification code",
                    description=f"Your one-time CAD code is **{code}**.\nIt expires in 10 minutes. Do not share it.",
                    color=0x3498DB,
                )
            )
            return

        if kind == "citation_issued":
            await notify_citation_issued(
                self.bot,
                int(payload["user_id"]),
                ticket_id=int(payload.get("ticket_id") or 0),
                violation=payload.get("violation") or "Citation",
                fine=int(payload.get("fine") or 0),
                issued_by=payload.get("issued_by_name") or "CAD",
                guild=guild,
            )
            if guild:
                await enforce_ticket_thresholds(self.bot, guild, int(payload["user_id"]))
            await log_action(
                self.bot,
                "citation_ticket",
                int(payload.get("issued_by") or 0),
                target_id=int(payload["user_id"]),
                details={"case_number": payload.get("case_number"), "via": "cad"},
                channel_key="tickets",
            )
            channel_id = load_config().get("channels", {}).get("citations")
            if channel_id and guild:
                ch = guild.get_channel(int(channel_id))
                if ch and payload.get("form"):
                    member = guild.get_member(int(payload["user_id"]))
                    embed = build_citation_embed(
                        payload["form"],
                        case_no=payload.get("case_number"),
                        subject=member,
                        fine=int(payload.get("fine") or 0),
                        footer="Issued via CAD",
                    )
                    await ch.send(embed=embed)
            return

        if kind == "citation_paid":
            await notify_ticket_paid(
                self.bot,
                int(payload["user_id"]),
                ticket_id=int(payload.get("ticket_id") or 0),
                fine=0,
                guild=guild,
            )
            return

        if kind == "warrant_issued":
            await notify_warrant_issued(
                self.bot,
                int(payload["user_id"]),
                reason=payload.get("reason") or "Warrant",
                issued_by=payload.get("issued_by_name") or "CAD",
                guild=guild,
            )
            channel_id = load_config().get("channels", {}).get("warrants")
            if channel_id and guild and payload.get("form"):
                ch = guild.get_channel(int(channel_id))
                member = guild.get_member(int(payload["user_id"]))
                if ch:
                    embed = build_warrant_embed(
                        payload["form"],
                        case_no=payload.get("case_number"),
                        subject=member,
                        footer="Issued via CAD",
                    )
                    await ch.send(embed=embed)
            return

        if kind in ("warrant_served", "warrant_cleared"):
            await notify_warrant_cleared(
                self.bot,
                int(payload["user_id"]),
                warrant_id=int(payload.get("warrant_id") or 0),
                reason=kind.replace("_", " "),
                cleared_by="CAD",
                guild=guild,
            )
            return

        if kind == "pager":
            if not guild:
                return
            roles = _dept_roles(guild, payload.get("department") or "")
            channel_id = load_config().get("channels", {}).get("department_pings")
            channel = guild.get_channel(int(channel_id)) if channel_id else None
            if not channel:
                return
            mentions = " ".join(r.mention for r in roles)
            name = department_display_name(payload.get("department") or "")
            embed = discord.Embed(
                title=f"Pager | {name}",
                description=payload.get("message") or "",
                color=0xE74C3C,
            )
            embed.set_footer(text=f"Paged via CAD by {payload.get('paged_by') or 'dispatcher'}")
            await channel.send(content=mentions, embed=embed, allowed_mentions=discord.AllowedMentions(roles=True))

    @app_commands.command(name="cad", description="Get the CAD website link")
    async def cad_open(self, interaction: discord.Interaction):
        url = _cad_url()
        await reply(interaction, f"Open CAD: {url}", ephemeral=True)

    @app_commands.command(name="cad-status", description="Show your CAD unit status")
    async def cad_status(self, interaction: discord.Interaction):
        db = await get_db()
        row = await db.execute_fetchone(
            "SELECT callsign, department, status FROM cad_units WHERE discord_user_id = ? ORDER BY updated_at DESC LIMIT 1",
            (interaction.user.id,),
        )
        if not row:
            await reply(interaction, "You do not have a CAD unit assigned.", ephemeral=True)
            return
        await reply(
            interaction,
            f"**{row['callsign']}** ({row['department']}) — `{row['status']}`",
            ephemeral=True,
        )

    @app_commands.command(name="cad-panic", description="Mark your CAD unit as panic / emergency")
    async def cad_panic(self, interaction: discord.Interaction):
        if not is_leo(interaction.user) and not is_staff(interaction.user):
            await reply(interaction, "Authorized units only.", ephemeral=True)
            return
        db = await get_db()
        row = await db.execute_fetchone(
            "SELECT id, callsign FROM cad_units WHERE discord_user_id = ? ORDER BY updated_at DESC LIMIT 1",
            (interaction.user.id,),
        )
        if not row:
            await reply(interaction, "No CAD unit linked to your Discord account.", ephemeral=True)
            return
        await db.execute(
            "UPDATE cad_units SET status = 'panic', updated_at = NOW() WHERE id = ?",
            (row["id"],),
        )
        pool = db._pool
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO cad_call_notes (call_id, author_id, body) SELECT id, $1, $2 FROM cad_calls WHERE status NOT IN ('closed','cancelled') ORDER BY opened_at DESC LIMIT 1",
                interaction.user.id,
                f"PANIC from {row['callsign']}",
            )
        channel_id = load_config().get("channels", {}).get("cad_panic") or load_config().get("channels", {}).get(
            "department_pings"
        )
        if channel_id and interaction.guild:
            ch = interaction.guild.get_channel(int(channel_id))
            if ch:
                await ch.send(
                    f"🚨 **PANIC** — {interaction.user.mention} unit **{row['callsign']}**",
                )
        await reply(interaction, f"Panic status set on **{row['callsign']}**.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Cad(bot))
