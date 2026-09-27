import discord
from discord import app_commands
from discord.ext import commands

from utils.helpers import log_action
from utils.database import get_db
from utils.core import format_datetime
from utils.economy import get_transactions, set_balances
from utils.core import defer, reply
from utils.permissions import is_owner
from utils.helpers import (
    notify_account_frozen,
    notify_account_unfrozen,
    notify_balance_updated,
)
from utils.users import set_registration_suspended


class OwnerPanelView(discord.ui.View):
    def __init__(self, target: discord.Member):
        super().__init__(timeout=300)
        self.target = target

    async def _check(self, interaction: discord.Interaction) -> bool:
        if not is_owner(interaction.user):
            await reply(interaction, "Owner only.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Set Wallet", style=discord.ButtonStyle.primary)
    async def set_wallet(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check(interaction):
            return
        await interaction.response.send_modal(SetBalanceModal(self.target, "wallet"))

    @discord.ui.button(label="Set Bank", style=discord.ButtonStyle.primary)
    async def set_bank(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check(interaction):
            return
        await interaction.response.send_modal(SetBalanceModal(self.target, "bank"))

    @discord.ui.button(label="Freeze Account", style=discord.ButtonStyle.danger)
    async def freeze(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check(interaction):
            return
        await defer(interaction, ephemeral=True)
        await set_balances(self.target.id, frozen=True)
        await log_action(
            interaction.client,
            "owner",
            interaction.user.id,
            target_id=self.target.id,
            details={"action": "freeze"},
        )
        await notify_account_frozen(
            interaction.client,
            self.target.id,
            by_staff=interaction.user.display_name,
            guild=interaction.guild,
        )
        await reply(interaction, f"Froze {self.target.mention}'s account.", ephemeral=True)

    @discord.ui.button(label="Unfreeze Account", style=discord.ButtonStyle.success)
    async def unfreeze(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check(interaction):
            return
        await defer(interaction, ephemeral=True)
        await set_balances(self.target.id, frozen=False)
        await log_action(
            interaction.client,
            "owner",
            interaction.user.id,
            target_id=self.target.id,
            details={"action": "unfreeze"},
        )
        await notify_account_unfrozen(
            interaction.client,
            self.target.id,
            by_staff=interaction.user.display_name,
            guild=interaction.guild,
        )
        await reply(interaction, f"Unfroze {self.target.mention}'s account.", ephemeral=True)

    @discord.ui.button(label="Suspend Registration", style=discord.ButtonStyle.secondary)
    async def suspend_reg(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check(interaction):
            return
        await defer(interaction, ephemeral=True)
        await set_registration_suspended(
            self.target.id,
            True,
            notify_client=interaction.client,
            guild=interaction.guild,
            reason="Registration suspended by server ownership.",
        )
        await log_action(
            interaction.client,
            "owner",
            interaction.user.id,
            target_id=self.target.id,
            details={"action": "suspend_registration"},
        )
        await reply(interaction, f"Suspended registration for {self.target.mention}.", ephemeral=True)

    @discord.ui.button(label="Restore Registration", style=discord.ButtonStyle.secondary)
    async def restore_reg(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check(interaction):
            return
        await defer(interaction, ephemeral=True)
        await set_registration_suspended(
            self.target.id,
            False,
            notify_client=interaction.client,
            guild=interaction.guild,
            reason="Registration restored by server ownership.",
        )
        await log_action(
            interaction.client,
            "owner",
            interaction.user.id,
            target_id=self.target.id,
            details={"action": "restore_registration"},
        )
        await reply(interaction, f"Restored registration for {self.target.mention}.", ephemeral=True)

    @discord.ui.button(label="Transaction History", style=discord.ButtonStyle.secondary)
    async def tx_history(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check(interaction):
            return
        await defer(interaction, ephemeral=True)
        txs = await get_transactions(self.target.id, 15)
        if not txs:
            await reply(interaction, "No transactions.", ephemeral=True)
            return
        embed = discord.Embed(title=f"Transactions | {self.target.display_name}", color=0x2B2D31)
        for tx in txs:
            embed.add_field(
                name=f"{tx['tx_type']} | ${tx['amount']:+,}",
                value=f"{tx['description']}\nBalance: ${tx['balance_after']:,} | {format_datetime(tx['created_at'])}",
                inline=False,
            )
        await reply(interaction, embed=embed, ephemeral=True)


class SetBalanceModal(discord.ui.Modal):
    def __init__(self, target: discord.Member, field: str):
        super().__init__(title=f"Set {field.title()} Balance")
        self.target = target
        self.field = field
        self.amount_input = discord.ui.TextInput(label="Amount", placeholder="Enter exact amount")
        self.add_item(self.amount_input)

    async def on_submit(self, interaction: discord.Interaction):
        if not is_owner(interaction.user):
            await reply(interaction, "Owner only.", ephemeral=True)
            return
        try:
            amount = int(self.amount_input.value)
        except ValueError:
            await reply(interaction, "Invalid amount.", ephemeral=True)
            return
        kwargs = {self.field: amount}
        await set_balances(self.target.id, **kwargs)
        await log_action(
            interaction.client,
            "owner",
            interaction.user.id,
            target_id=self.target.id,
            details={"action": f"set_{self.field}", "amount": amount},
        )
        await notify_balance_updated(
            interaction.client,
            self.target.id,
            field=self.field,
            amount=amount,
            by_staff=interaction.user.display_name,
            guild=interaction.guild,
        )
        await reply(
            interaction,
            f"Set {self.target.mention}'s {self.field} to ${amount:,}.",
            ephemeral=True,
        )


class OwnerPanel(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="owner-panel", description="Owner management panel for a user")
    @app_commands.describe(member="Member to manage")
    async def owner_panel(self, interaction: discord.Interaction, member: discord.Member):
        if not is_owner(interaction.user):
            await reply(interaction, "Owner only.", ephemeral=True)
            return
        await defer(interaction, ephemeral=True)
        db = await get_db()
        vehicles = await db.execute_fetchone("SELECT COUNT(*) AS cnt FROM vehicles WHERE user_id = ?", (member.id,))
        tickets = await db.execute_fetchone(
            "SELECT COUNT(*) AS cnt FROM citation_tickets WHERE user_id = ? AND payment_status = 'unpaid'",
            (member.id,),
        )
        warrants = await db.execute_fetchone(
            "SELECT COUNT(*) AS cnt FROM warrants WHERE user_id = ? AND active = 1", (member.id,)
        )
        embed = discord.Embed(title=f"Owner Panel | {member.display_name}", color=0x2B2D31)
        embed.add_field(name="Vehicles", value=str(vehicles["cnt"]), inline=True)
        embed.add_field(name="Unpaid Tickets", value=str(tickets["cnt"]), inline=True)
        embed.add_field(name="Active Warrants", value=str(warrants["cnt"]), inline=True)
        embed.description = "Manage economy, registrations, and view records."
        view = OwnerPanelView(member)
        await reply(interaction, embed=embed, view=view, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(OwnerPanel(bot))
