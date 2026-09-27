import discord
from discord import app_commands
from discord.ext import commands

from utils.helpers import log_action
from utils.economy import (
    AccountFrozen,
    CooldownActive,
    EconomyError,
    InsufficientFunds,
    claim_daily,
    claim_work,
    get_account,
    get_leaderboard,
    transfer,
)
from utils.core import defer, reply


class Economy(commands.Cog):
    economy = app_commands.Group(name="economy", description="Economy commands")

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @economy.command(name="balance", description="View your account balance")
    async def balance(self, interaction: discord.Interaction):
        await defer(interaction, ephemeral=True)
        try:
            account = await get_account(interaction.user.id)
        except Exception:
            await reply(interaction, "Database error. Please try again.", ephemeral=True)
            return
        net = account["wallet"] + account["bank"]
        embed = discord.Embed(title="Account Balance", color=0x2B2D31)
        embed.add_field(name="Wallet", value=f"${account['wallet']:,}", inline=True)
        embed.add_field(name="Bank", value=f"${account['bank']:,}", inline=True)
        embed.add_field(name="Net Worth", value=f"${net:,}", inline=True)
        embed.add_field(name="Status", value="Frozen" if account.get("frozen") else "Active", inline=True)
        await reply(interaction, embed=embed, ephemeral=True)

    @economy.command(name="daily", description="Claim your daily reward")
    async def daily(self, interaction: discord.Interaction):
        await defer(interaction, ephemeral=True)
        try:
            amount = await claim_daily(interaction.user.id)
        except CooldownActive as exc:
            await reply(interaction, str(exc), ephemeral=True)
            return
        except AccountFrozen:
            await reply(interaction, "Your account is frozen.", ephemeral=True)
            return
        except Exception:
            await reply(interaction, "Database error. Please try again.", ephemeral=True)
            return
        await log_action(
            self.bot, "economy", interaction.user.id, details={"action": "daily", "amount": amount}
        )
        await reply(interaction, f"Daily reward claimed. ${amount:,} added to your wallet.", ephemeral=True)

    @economy.command(name="work", description="Work a shift to earn money")
    async def work(self, interaction: discord.Interaction):
        await defer(interaction, ephemeral=True)
        try:
            amount, job = await claim_work(interaction.user.id)
        except CooldownActive as exc:
            await reply(interaction, str(exc), ephemeral=True)
            return
        except AccountFrozen:
            await reply(interaction, "Your account is frozen.", ephemeral=True)
            return
        except Exception:
            await reply(interaction, "Database error. Please try again.", ephemeral=True)
            return
        await log_action(
            self.bot, "economy", interaction.user.id, details={"action": "work", "amount": amount, "job": job}
        )
        await reply(interaction, f"You worked as a {job} and earned ${amount:,}.", ephemeral=True)

    @economy.command(name="pay", description="Transfer money to another user")
    @app_commands.describe(member="User to pay", amount="Amount to transfer")
    async def pay(self, interaction: discord.Interaction, member: discord.Member, amount: int):
        await defer(interaction, ephemeral=True)
        try:
            await transfer(interaction.user.id, member.id, amount)
        except InsufficientFunds:
            await reply(interaction, "Insufficient wallet balance.", ephemeral=True)
            return
        except AccountFrozen:
            await reply(interaction, "One or both accounts are frozen.", ephemeral=True)
            return
        except EconomyError as exc:
            await reply(interaction, str(exc), ephemeral=True)
            return
        except Exception:
            await reply(interaction, "Database error. Please try again.", ephemeral=True)
            return
        await log_action(
            self.bot,
            "economy",
            interaction.user.id,
            target_id=member.id,
            details={"action": "pay", "amount": amount},
        )
        await reply(interaction, f"Paid ${amount:,} to {member.mention}.", ephemeral=True)

    @economy.command(name="leaderboard", description="View the richest users")
    async def leaderboard(self, interaction: discord.Interaction):
        await defer(interaction, ephemeral=True)
        try:
            rows = await get_leaderboard(10)
        except Exception:
            await reply(interaction, "Database error. Please try again.", ephemeral=True)
            return
        if not rows:
            await reply(interaction, "No economy data yet.", ephemeral=True)
            return
        embed = discord.Embed(title="Economy Leaderboard", color=0x2B2D31)
        for i, row in enumerate(rows, 1):
            embed.add_field(
                name=f"#{i} | <@{row['user_id']}>",
                value=f"Net Worth: ${row['net_worth']:,}",
                inline=False,
            )
        await reply(interaction, embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Economy(bot))
