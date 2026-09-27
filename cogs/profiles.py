import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from utils.database import DatabaseError, DatabaseTimeoutError, get_db
from utils.core import format_date, format_datetime
from utils.economy import get_account, get_transactions
from utils.core import defer, reply
from utils.users import ensure_user, insurance_is_valid, license_is_valid


class VehicleSelect(discord.ui.Select):
    def __init__(self, vehicles: list[dict], target_id: int):
        options = [
            discord.SelectOption(label=v["plate"], value=str(v["id"]), description=f"{v['year']} {v['make']} {v['model']}"[:100])
            for v in vehicles[:25]
        ]
        super().__init__(placeholder="Select a vehicle...", options=options, custom_id=f"profile_vehicle_{target_id}")
        self.vehicles = {str(v["id"]): v for v in vehicles}
        self.target_id = target_id

    async def callback(self, interaction: discord.Interaction):
        vehicle = self.vehicles[self.values[0]]
        owner = interaction.guild.get_member(self.target_id)
        embed = discord.Embed(title="Vehicle Registration", color=0x2B2D31)
        embed.add_field(name="Make", value=vehicle["make"], inline=True)
        embed.add_field(name="Model", value=vehicle["model"], inline=True)
        embed.add_field(name="Year", value=str(vehicle["year"]), inline=True)
        embed.add_field(name="Color", value=vehicle["color"], inline=True)
        embed.add_field(name="License Plate", value=vehicle["plate"], inline=True)
        embed.add_field(name="Registration Date", value=format_datetime(vehicle["created_at"]), inline=False)
        status = vehicle.get("status", "active").title()
        if vehicle.get("expires_at"):
            status += f"\nExpires: {format_date(vehicle['expires_at'])}"
        embed.add_field(name="Status", value=status, inline=True)
        embed.add_field(name="Owner", value=owner.mention if owner else f"ID {self.target_id}", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class TrailerSelect(discord.ui.Select):
    def __init__(self, trailers: list[dict], target_id: int):
        options = [
            discord.SelectOption(label=t["plate"], value=str(t["id"]), description=t["trailer_type"][:100])
            for t in trailers[:25]
        ]
        super().__init__(placeholder="Select a trailer...", options=options, custom_id=f"profile_trailer_{target_id}")
        self.trailers = {str(t["id"]): t for t in trailers}
        self.target_id = target_id

    async def callback(self, interaction: discord.Interaction):
        trailer = self.trailers[self.values[0]]
        owner = interaction.guild.get_member(self.target_id)
        embed = discord.Embed(title="Trailer Registration", color=0x2B2D31)
        embed.add_field(name="Trailer Type", value=trailer["trailer_type"], inline=True)
        embed.add_field(name="Plate Number", value=trailer["plate"], inline=True)
        embed.add_field(name="Registration Date", value=format_datetime(trailer["created_at"]), inline=False)
        embed.add_field(name="Status", value=trailer.get("status", "active").title(), inline=True)
        embed.add_field(name="Owner", value=owner.mention if owner else f"ID {self.target_id}", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class ProfileView(discord.ui.View):
    def __init__(self, target: discord.Member):
        super().__init__(timeout=300)
        self.target = target
        self._add_buttons()

    def _add_buttons(self) -> None:
        uid = self.target.id
        specs = [
            ("Registrations", discord.ButtonStyle.primary, f"prof_reg_{uid}", self.registrations),
            ("Tickets", discord.ButtonStyle.secondary, f"prof_tix_{uid}", self.tickets),
            ("Trailers", discord.ButtonStyle.secondary, f"prof_trl_{uid}", self.trailers),
            ("Warrants", discord.ButtonStyle.secondary, f"prof_war_{uid}", self.warrants),
            ("Economy", discord.ButtonStyle.success, f"prof_eco_{uid}", self.economy),
        ]
        for label, style, cid, callback in specs:
            btn = discord.ui.Button(label=label, style=style, custom_id=cid)
            btn.callback = callback
            self.add_item(btn)

    async def _db_error(self, interaction: discord.Interaction, exc: Exception) -> None:
        if isinstance(exc, DatabaseTimeoutError):
            await reply(interaction, "The database took too long. Please try again.", ephemeral=True)
        else:
            await reply(interaction, "A database error occurred. Please try again.", ephemeral=True)

    async def registrations(self, interaction: discord.Interaction):
        await defer(interaction, ephemeral=True)
        try:
            db = await get_db()
            vehicles = await db.execute_fetchall(
                "SELECT * FROM vehicles WHERE user_id = ? ORDER BY id",
                (self.target.id,),
            )
        except (DatabaseTimeoutError, DatabaseError) as exc:
            await self._db_error(interaction, exc)
            return
        if not vehicles:
            await reply(interaction, "No registered vehicles.", ephemeral=True)
            return
        view = discord.ui.View(timeout=120)
        view.add_item(VehicleSelect(vehicles, self.target.id))
        await reply(interaction, "Select a vehicle to view details:", view=view, ephemeral=True)

    async def tickets(self, interaction: discord.Interaction):
        await defer(interaction, ephemeral=True)
        try:
            db = await get_db()
            rows = await db.execute_fetchall(
                """
                SELECT id, violation, fine, issued_by, payment_status, created_at
                FROM citation_tickets WHERE user_id = ? ORDER BY id DESC
                """,
                (self.target.id,),
            )
        except (DatabaseTimeoutError, DatabaseError) as exc:
            await self._db_error(interaction, exc)
            return
        if not rows:
            await reply(interaction, "No tickets on record.", ephemeral=True)
            return
        embed = discord.Embed(title=f"Tickets | {self.target.display_name}", color=0x2B2D31)
        for row in rows[:10]:
            officer = interaction.guild.get_member(row["issued_by"])
            officer_name = officer.display_name if officer else f"ID {row['issued_by']}"
            embed.add_field(
                name=f"Ticket #{row['id']} | {row['payment_status'].title()}",
                value=(
                    f"Violation: {row['violation']}\n"
                    f"Fine: ${row['fine']:,}\n"
                    f"Issuing Officer: {officer_name}\n"
                    f"Issue Date: {format_datetime(row['created_at'])}"
                ),
                inline=False,
            )
        await reply(interaction, embed=embed, ephemeral=True)

    async def trailers(self, interaction: discord.Interaction):
        await defer(interaction, ephemeral=True)
        try:
            db = await get_db()
            rows = await db.execute_fetchall(
                "SELECT * FROM trailers WHERE user_id = ? ORDER BY id",
                (self.target.id,),
            )
        except (DatabaseTimeoutError, DatabaseError) as exc:
            await self._db_error(interaction, exc)
            return
        if not rows:
            await reply(interaction, "No registered trailers.", ephemeral=True)
            return
        view = discord.ui.View(timeout=120)
        view.add_item(TrailerSelect(rows, self.target.id))
        await reply(interaction, "Select a trailer to view details:", view=view, ephemeral=True)

    async def warrants(self, interaction: discord.Interaction):
        await defer(interaction, ephemeral=True)
        try:
            db = await get_db()
            rows = await db.execute_fetchall(
                "SELECT id, reason, status, issued_by, active, created_at FROM warrants WHERE user_id = ? ORDER BY id DESC",
                (self.target.id,),
            )
        except (DatabaseTimeoutError, DatabaseError) as exc:
            await self._db_error(interaction, exc)
            return
        if not rows:
            await reply(interaction, "No warrants on record.", ephemeral=True)
            return
        embed = discord.Embed(title=f"Warrants | {self.target.display_name}", color=0x2B2D31)
        for row in rows[:10]:
            status = "Active" if row["active"] else row.get("status", "cleared").title()
            officer = interaction.guild.get_member(row["issued_by"])
            officer_name = officer.display_name if officer and row["issued_by"] else "System"
            embed.add_field(
                name=f"Warrant #{row['id']} | {status}",
                value=(
                    f"Reason: {row['reason']}\n"
                    f"Issuing Officer: {officer_name}\n"
                    f"Issue Date: {format_datetime(row['created_at'])}"
                ),
                inline=False,
            )
        await reply(interaction, embed=embed, ephemeral=True)

    async def economy(self, interaction: discord.Interaction):
        await defer(interaction, ephemeral=True)
        try:
            account = await get_account(self.target.id)
            transactions = await get_transactions(self.target.id, 5)
        except (DatabaseTimeoutError, DatabaseError) as exc:
            await self._db_error(interaction, exc)
            return
        net = account["wallet"] + account["bank"]
        daily_status = (
            "Available"
            if not account.get("daily_last")
            else f"Last claimed: {format_datetime(account['daily_last'])}"
        )
        embed = discord.Embed(title=f"Economy | {self.target.display_name}", color=0x2B2D31)
        embed.add_field(name="Wallet", value=f"${account['wallet']:,}", inline=True)
        embed.add_field(name="Bank", value=f"${account['bank']:,}", inline=True)
        embed.add_field(name="Net Worth", value=f"${net:,}", inline=True)
        embed.add_field(name="Daily Reward", value=daily_status, inline=False)
        embed.add_field(name="Account Status", value="Frozen" if account.get("frozen") else "Active", inline=True)
        if transactions:
            tx_lines = [
                f"{t['tx_type']}: ${t['amount']:+,} (balance ${t['balance_after']:,}) | {t['description']}"
                for t in transactions
            ]
            embed.add_field(name="Recent Transactions", value="\n".join(tx_lines)[:1024], inline=False)
        await reply(interaction, embed=embed, ephemeral=True)


class Profiles(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="profile", description="View a member's profile dashboard")
    @app_commands.describe(user="Member to view (defaults to yourself)")
    async def profile(self, interaction: discord.Interaction, user: discord.Member | None = None):
        target = user or interaction.user
        await defer(interaction, ephemeral=False)
        try:
            db = await get_db()
            await ensure_user(db, target.id)
            user_row, vehicle_count, ticket_count, warrant_count = await asyncio.gather(
                db.execute_fetchone("SELECT * FROM users WHERE user_id = ?", (target.id,)),
                db.execute_fetchone("SELECT COUNT(*) AS cnt FROM vehicles WHERE user_id = ?", (target.id,)),
                db.execute_fetchone(
                    "SELECT COUNT(*) AS cnt FROM citation_tickets WHERE user_id = ? AND payment_status = 'unpaid'",
                    (target.id,),
                ),
                db.execute_fetchone(
                    "SELECT COUNT(*) AS cnt FROM warrants WHERE user_id = ? AND active = 1",
                    (target.id,),
                ),
            )
        except DatabaseTimeoutError:
            await reply(interaction, "The database took too long. Please try again.", ephemeral=True)
            return
        except DatabaseError:
            await reply(interaction, "A database error occurred. Please try again.", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"Profile | {target.display_name}",
            description="Use the buttons below to view detailed records.",
            color=target.color if target.color.value else 0x2B2D31,
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="Registered Vehicles", value=str(vehicle_count["cnt"]), inline=True)
        embed.add_field(name="Unpaid Tickets", value=str(ticket_count["cnt"]), inline=True)
        embed.add_field(name="Active Warrants", value=str(warrant_count["cnt"]), inline=True)

        registration_suspended = bool(user_row and user_row.get("registration_suspended"))
        license_suspended = bool(user_row and user_row.get("license_suspended"))
        reg_value = "Suspended" if registration_suspended else "Active"
        if license_suspended:
            license_value = "Suspended | Pay all unpaid tickets to restore"
        elif user_row and license_is_valid(user_row):
            license_value = f"Active until {user_row['license_expires_at']}"
        else:
            license_value = "Expired | Buy in shop (/shop)"
        if user_row and insurance_is_valid(user_row):
            insurance_value = f"Active until {format_date(user_row['insurance_expires_at'])}"
        else:
            insurance_value = "None | Buy in shop (/shop)"
        embed.add_field(name="Registration Status", value=reg_value, inline=True)
        embed.add_field(name="License Status", value=license_value, inline=True)
        embed.add_field(name="Insurance", value=insurance_value, inline=True)

        view = ProfileView(target)
        await reply(interaction, embed=embed, view=view, ephemeral=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(Profiles(bot))
