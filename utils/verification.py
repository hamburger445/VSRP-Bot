from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone

from utils.database import get_db
from utils.core import load_config, reply
from utils.embeds import build_embed, build_error_embed, build_success_embed


def verification_channel_id() -> int:
    return int(load_config().get("verification", {}).get("channel_id", 1250612127891197953))


def verified_role_id() -> int:
    return int(load_config().get("verification", {}).get("verified_role_id", 1250612203850170390))


def pending_verify_role_id() -> int | None:
    pending_role = load_config().get("verification", {}).get("pending_role_id")
    return int(pending_role) if pending_role else None


async def mark_verified(user_id: int) -> None:
    db = await get_db()
    await db.execute(
        "INSERT INTO users (user_id, verified_at) VALUES (?, NOW()) ON CONFLICT (user_id) DO UPDATE SET verified_at = NOW()",
        (user_id,),
    )
    await db.commit()


async def is_verified_user(user: discord.User | discord.Member) -> bool:
    verified_role = None
    if isinstance(user, discord.Member):
        role_id = load_config().get("verification", {}).get("verified_role_id")
        if role_id:
            verified_role = discord.utils.get(user.roles, id=int(role_id))
    if verified_role is not None:
        return True

    db = await get_db()
    row = await db.execute_fetchone(
        "SELECT verified_at FROM users WHERE user_id = ?",
        (user.id,),
    )
    return bool(row and row["verified_at"])


def build_verification_embed() -> discord.Embed:
    return build_embed(
        title="Account Verification",
        description=(
            "Verification is required before you can access department information or apply for civilian positions.\n\n"
            "Verification helps prevent spam and alternate accounts.\n\n"
            "Run `/verify` in this channel to complete verification."
        ),
        footer="WCRP Verification System",
    )


def account_age_days(member: discord.Member) -> int:
    if not member.created_at:
        return 0
    now = datetime.now(timezone.utc)
    delta = now - member.created_at
    return delta.days


class Verify(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="verify", description="Verify your account to access departments and applications.")
    async def verify(self, interaction: discord.Interaction):
        channel_id = verification_channel_id()
        if interaction.channel_id != channel_id:
            await reply(
                interaction,
                build_error_embed(
                    "Wrong Channel",
                    "Please use `/verify` in the designated verification channel.",
                    footer="WCRP Verification System",
                ),
                ephemeral=True,
            )
            return

        member = interaction.user
        if not isinstance(member, discord.Member):
            await reply(
                interaction,
                build_error_embed(
                    "Verification Failed",
                    "This command must be used in a server member context.",
                    footer="WCRP Verification System",
                ),
                ephemeral=True,
            )
            return

        if await is_verified_user(member):
            await reply(
                interaction,
                build_embed(
                    title="Already Verified",
                    description="You are already verified and may continue to applications.",
                    footer="WCRP Verification System",
                ),
                ephemeral=True,
            )
            return

        age = account_age_days(member)
        if age < 14:
            await reply(
                interaction,
                build_error_embed(
                    "Account Too New",
                    "Your account must be at least 14 days old to verify. Please try again later.",
                    footer="WCRP Verification System",
                ),
                ephemeral=True,
            )
            return

        try:
            dm_embed = build_embed(
                title="Verification in Progress",
                description=(
                    "Thank you for beginning verification. You are being marked as verified for applications and department access.\n\n"
                    "If your DMs are closed, please enable them temporarily and try again."
                ),
                footer="WCRP Verification System",
            )
            await member.send(embed=dm_embed)
        except discord.Forbidden:
            await reply(
                interaction,
                build_error_embed(
                    "DMs Disabled",
                    "Please enable direct messages from server members temporarily so verification can continue.",
                    footer="WCRP Verification System",
                ),
                ephemeral=True,
            )
            return

        await mark_verified(member.id)

        success_embed = build_success_embed(
            title="Verified Successfully",
            description=(
                "Your account has been verified and the verified role has been applied.\n\n"
                "You can now access department information and civilian applications."
            ),
            footer="WCRP Verification System",
        )
        try:
            await member.send(embed=success_embed)
        except discord.HTTPException:
            pass

        if interaction.response.is_done():
            await interaction.followup.send(embed=build_embed(title="Verified", description="Verification complete.", footer="WCRP Verification System"), ephemeral=True)
        else:
            await interaction.response.send_message(embed=build_embed(title="Verified", description="Verification complete.", footer="WCRP Verification System"), ephemeral=True)

        try:
            await interaction.delete_original_response()
        except Exception:
            pass

    async def setup_persistent_views(self):
        pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Verify(bot))
