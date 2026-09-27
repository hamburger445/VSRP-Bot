from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from utils.helpers import log_action
from utils.core import load_config
from utils.database import DatabaseError, DatabaseTimeoutError, get_db
from utils.core import defer, reply
from utils.permissions import is_staff
from utils.helpers import (
    notify_account_frozen,
    notify_account_unfrozen,
    notify_ticket_paid,
    notify_trailer_registered,
    notify_vehicle_registered,
    notify_wallet_adjustment,
)
from utils.tickets import check_registration_allowed
from utils.users import ensure_user, set_license_suspended, sync_member_roles


class Admin(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="adminview", description="View a complete overview for a member (staff only)")
    @app_commands.describe(member="Member to inspect")
    async def adminview(self, interaction: discord.Interaction, member: discord.Member):
        # Restrict to role 1513623449715609800
        required_role = 1513623449715609800
        allowed = False
        if isinstance(interaction.user, discord.Member):
            for role in interaction.user.roles:
                if role.id == required_role:
                    allowed = True
                    break
        if not allowed:
            embed = discord.Embed(title="Insufficient Permissions", description="You do not have permission to use this command.", color=0xE74C3C)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        # Build overview embed
        embed = discord.Embed(title="Member Overview", color=0x2B2D31)
        embed.set_thumbnail(url=member.display_avatar.url if getattr(member, 'display_avatar', None) else member.avatar.url if member.avatar else None)
        embed.add_field(name="Username", value=str(member), inline=True)
        embed.add_field(name="Display Name", value=member.display_name or "N/A", inline=True)
        embed.add_field(name="Discord ID", value=str(member.id), inline=True)
        if member.created_at:
            embed.add_field(name="Account Created", value=f"<t:{int(member.created_at.timestamp())}:F>", inline=True)
        if member.joined_at:
            embed.add_field(name="Server Join Date", value=f"<t:{int(member.joined_at.timestamp())}:F>", inline=True)
        embed.add_field(name="Highest Role", value=member.top_role.name if member.top_role else "N/A", inline=True)
        embed.add_field(name="Nickname", value=member.nick or "None", inline=True)
        embed.add_field(name="Bot", value="Yes" if member.bot else "No", inline=True)

        # Verification info
        db = await get_db()
        row = await db.execute_fetchone("SELECT verified_at FROM users WHERE user_id = ?", (member.id,))
        verified = bool(row and row.get("verified_at"))
        verified_at = row["verified_at"].strftime("%Y-%m-%d %H:%M UTC") if row and row.get("verified_at") else "N/A"
        account_age_days = 0
        if member.created_at:
            from datetime import datetime, timezone

            now = datetime.now(timezone.utc)
            account_age_days = (now - member.created_at).days
        meets_requirements = "Yes" if account_age_days >= 14 else "No"
        embed.add_field(name="Verified", value="Yes" if verified else "No", inline=True)
        embed.add_field(name="Verification Date", value=verified_at, inline=True)
        embed.add_field(name="Account Age (days)", value=str(account_age_days), inline=True)
        embed.add_field(name="Meets Verification Requirements", value=meets_requirements, inline=True)

        # Applications summary
        try:
            apps = await db.execute_fetchall(
                "SELECT id, status, created_at, review_reason FROM applications WHERE user_id = ? ORDER BY created_at DESC",
                (member.id,),
            )
        except DatabaseError:
            # review_reason column may not exist; fall back to a safe query
            apps = await db.execute_fetchall(
                "SELECT id, status, created_at FROM applications WHERE user_id = ? ORDER BY created_at DESC",
                (member.id,),
            )
        total_apps = len(apps)
        latest_app = apps[0] if apps else None
        latest_date = latest_app["created_at"].strftime("%Y-%m-%d %H:%M UTC") if latest_app else "N/A"
        status_counts = {"pending": 0, "accepted": 0, "denied": 0}
        for a in apps:
            status_counts[a["status"]] = status_counts.get(a["status"], 0) + 1
        current_status = latest_app["status"] if latest_app else "Never Applied"
        embed.add_field(name="Active Application", value=latest_app["id"] if latest_app and latest_app["status"] == "pending" else "None", inline=True)
        embed.add_field(name="Total Applications", value=str(total_apps), inline=True)
        embed.add_field(name="Latest Application Date", value=latest_date, inline=True)
        embed.add_field(name="Application Statuses", value=f"Pending: {status_counts.get('pending',0)} • Accepted: {status_counts.get('accepted',0)} • Denied: {status_counts.get('denied',0)}", inline=False)
        if latest_app and latest_app["status"] == "denied" and latest_app.get("review_reason"):
            embed.add_field(name="Last Denial Reason", value=latest_app.get("review_reason") or "N/A", inline=False)

        # Departments & roles
        cfg = load_config()
        dept_cfg = cfg.get("roles", {}).get("departments", {})
        dept_role_ids = [int(v) for v in (dept_cfg.values() if isinstance(dept_cfg, dict) else []) if v]
        member_role_ids = [r.id for r in member.roles]
        dept_roles = [r.mention for r in member.roles if r.id in dept_role_ids]
        staff_role_id = cfg.get("roles", {}).get("staff")
        admin_role_id = cfg.get("roles", {}).get("admin")
        leadership_roles = [r.mention for r in member.roles if r.id in (admin_role_id,)]
        staff_roles = [r.mention for r in member.roles if r.id == staff_role_id]
        embed.add_field(name="Department Roles", value=", ".join(dept_roles) or "None", inline=True)
        embed.add_field(name="Leadership Roles", value=", ".join(leadership_roles) or "None", inline=True)
        embed.add_field(name="Staff Roles", value=", ".join(staff_roles) or "None", inline=True)
        embed.add_field(name="Total Department Roles", value=str(len(dept_roles)), inline=True)

        # Support tickets
        try:
            tickets = await db.execute_fetchall(
                "SELECT id, status, created_at, closed_by FROM support_tickets WHERE user_id = ? ORDER BY created_at DESC",
                (member.id,),
            )
        except DatabaseError:
            tickets = await db.execute_fetchall(
                "SELECT id, status, created_at FROM support_tickets WHERE user_id = ? ORDER BY created_at DESC",
                (member.id,),
            )
        total_tickets = len(tickets)
        active_tickets = sum(1 for t in tickets if t["status"] == "open")
        closed_tickets = sum(1 for t in tickets if t["status"] == "closed")
        embed.add_field(name="Tickets Opened", value=str(total_tickets), inline=True)
        embed.add_field(name="Active Tickets", value=str(active_tickets), inline=True)
        embed.add_field(name="Closed Tickets", value=str(closed_tickets), inline=True)

        # Moderation history
        warns = await db.execute_fetchone("SELECT COUNT(*) AS cnt FROM moderation_strikes WHERE user_id = ?", (member.id,))
        bans = await db.execute_fetchone("SELECT COUNT(*) AS cnt FROM moderation_bans WHERE user_id = ?", (member.id,))
        cases = await db.execute_fetchone("SELECT COUNT(*) AS cnt FROM mod_cases WHERE user_id = ?", (member.id,))
        warns_cnt = warns["cnt"] if warns else 0
        bans_cnt = bans["cnt"] if bans else 0
        cases_cnt = cases["cnt"] if cases else 0
        embed.add_field(name="Warnings", value=str(warns_cnt), inline=True)
        embed.add_field(name="Bans", value=str(bans_cnt), inline=True)
        embed.add_field(name="Mod Cases", value=str(cases_cnt), inline=True)

        # Server activity (not tracked)
        embed.add_field(name="Messages Sent", value="Not Tracked", inline=True)
        embed.add_field(name="Last Recorded Activity", value="Not Tracked", inline=True)
        embed.add_field(name="Applications Submitted", value=str(total_apps), inline=True)
        embed.add_field(name="Tickets Created", value=str(total_tickets), inline=True)

        # Buttons: Refresh, Applications, Tickets, Moderation
        class AdminView(discord.ui.View):
            def __init__(self, owner_id: int, target_member: discord.Member):
                super().__init__(timeout=300)
                self.owner_id = owner_id
                self.target_member = target_member

            async def interaction_check(self, inter: discord.Interaction) -> bool:
                return inter.user.id == self.owner_id

            @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary)
            async def refresh(self, inter: discord.Interaction, button: discord.ui.Button):
                await inter.response.defer(ephemeral=True)
                # simply regenerate main embed
                await inter.followup.send("Refreshed.", ephemeral=True)

            @discord.ui.button(label="Applications", style=discord.ButtonStyle.primary)
            async def applications(self, inter: discord.Interaction, button: discord.ui.Button):
                await inter.response.defer(ephemeral=True)
                rows = apps
                if not rows:
                    await inter.followup.send("No applications found.", ephemeral=True)
                    return
                # build list
                lines = []
                for a in rows[:10]:
                    lines.append(f"ID: {a['id']} • {a['status'].title()} • {a['created_at'].strftime('%Y-%m-%d %H:%M UTC')}" + (f" • Denial: {a.get('review_reason')}" if a.get('review_reason') else ""))
                text = "\n".join(lines)
                await inter.followup.send(embed=discord.Embed(title="Applications", description=text, color=0x2B2D31), ephemeral=True)

            @discord.ui.button(label="Tickets", style=discord.ButtonStyle.primary)
            async def tickets_btn(self, inter: discord.Interaction, button: discord.ui.Button):
                await inter.response.defer(ephemeral=True)
                rows = tickets
                if not rows:
                    await inter.followup.send("No tickets found.", ephemeral=True)
                    return
                lines = []
                for t in rows[:10]:
                    lines.append(f"ID: {t['id']} • {t['status'].title()} • {t['created_at'].strftime('%Y-%m-%d %H:%M UTC')}")
                await inter.followup.send(embed=discord.Embed(title="Support Tickets", description="\n".join(lines), color=0x2B2D31), ephemeral=True)

            @discord.ui.button(label="Moderation", style=discord.ButtonStyle.danger)
            async def moderation_btn(self, inter: discord.Interaction, button: discord.ui.Button):
                await inter.response.defer(ephemeral=True)
                rows = await db.execute_fetchall("SELECT * FROM mod_cases WHERE user_id = ? ORDER BY created_at DESC", (self.target_member.id,))
                if not rows:
                    await inter.followup.send("No moderation cases.", ephemeral=True)
                    return
                lines = []
                for r in rows[:10]:
                    lines.append(f"Case {r['id']} • {r['action_type']} • {r['created_at'].strftime('%Y-%m-%d %H:%M UTC')} • By: <@{r['moderator_id']}>")
                await inter.followup.send(embed=discord.Embed(title="Moderation History", description="\n".join(lines), color=0xE74C3C), ephemeral=True)

        view = AdminView(interaction.user.id, member)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    def _max_vehicles(self) -> int:
        return load_config().get("vehicles", {}).get("max_per_member", 5)

    @app_commands.command(name="payticket", description="Pay a citation ticket by ID")
    @app_commands.describe(ticket_id="Ticket ID to pay")
    async def pay_ticket(self, interaction: discord.Interaction, ticket_id: int):
        await defer(interaction, ephemeral=True)
        try:
            db = await get_db()
            pool = db._pool  # noqa: SLF001
            async with pool.acquire() as conn:
                async with conn.transaction():
                    ticket = await conn.fetchrow(
                        """
                        SELECT * FROM citation_tickets
                        WHERE id = $1 AND user_id = $2 FOR UPDATE
                        """,
                        ticket_id,
                        interaction.user.id,
                    )
                    if not ticket:
                        await reply(interaction, "Ticket not found.", ephemeral=True)
                        return
                    if ticket["payment_status"] == "paid":
                        await reply(interaction, "This ticket is already paid.", ephemeral=True)
                        return
                    account = await conn.fetchrow(
                        "SELECT wallet, frozen FROM economy_accounts WHERE user_id = $1 FOR UPDATE",
                        interaction.user.id,
                    )
                    if not account or account["frozen"]:
                        await reply(interaction, "Account unavailable.", ephemeral=True)
                        return
                    if account["wallet"] < ticket["fine"]:
                        await reply(interaction, "Insufficient funds to pay this ticket.", ephemeral=True)
                        return
                    new_balance = account["wallet"] - ticket["fine"]
                    await conn.execute(
                        "UPDATE economy_accounts SET wallet = $1 WHERE user_id = $2",
                        new_balance,
                        interaction.user.id,
                    )
                    await conn.execute(
                        """
                        INSERT INTO economy_transactions (user_id, tx_type, amount, balance_after, description)
                        VALUES ($1, 'ticket_payment', $2, $3, $4)
                        """,
                        interaction.user.id,
                        -ticket["fine"],
                        new_balance,
                        f"Paid ticket #{ticket_id}",
                    )
                    await conn.execute(
                        "UPDATE citation_tickets SET payment_status = 'paid', paid_at = NOW() WHERE id = $1",
                        ticket_id,
                    )
                    unpaid = await conn.fetchval(
                        """
                        SELECT COUNT(*) FROM citation_tickets
                        WHERE user_id = $1 AND payment_status = 'unpaid'
                        """,
                        interaction.user.id,
                    )
            fine = ticket["fine"]
            all_paid = unpaid == 0
        except (DatabaseTimeoutError, DatabaseError):
            await reply(interaction, "Database error. Please try again.", ephemeral=True)
            return

        license_restored = False
        if all_paid:
            license_restored = await set_license_suspended(
                interaction.user.id,
                False,
                notify_client=self.bot,
                guild=interaction.guild,
                reason="All citation tickets have been paid.",
            )

        await notify_ticket_paid(
            self.bot,
            interaction.user.id,
            ticket_id=ticket_id,
            fine=fine,
            registration_restored=license_restored,
            guild=interaction.guild,
        )

        await log_action(
            self.bot,
            "ticket_payment",
            interaction.user.id,
            target_id=interaction.user.id,
            details={"ticket_id": ticket_id, "fine": fine},
            channel_key="tickets",
        )
        await reply(interaction, f"Ticket #{ticket_id} paid for ${fine:,}.", ephemeral=True)

    @app_commands.command(name="registervehicle", description="Register a vehicle to your profile")
    @app_commands.describe(make="Make", model="Model", year="Year", color="Color", plate="Plate")
    async def register_vehicle(
        self,
        interaction: discord.Interaction,
        make: str,
        model: str,
        year: app_commands.Range[int, 1900, 2100],
        color: str,
        plate: str,
    ):
        member = interaction.user
        allowed, msg = await check_registration_allowed(member.id)
        if not allowed:
            await reply(interaction, msg, ephemeral=True)
            return
        await defer(interaction, ephemeral=True)
        plate_upper = plate.strip().upper()
        try:
            db = await get_db()
            count = await db.execute_fetchone(
                "SELECT COUNT(*) AS cnt FROM vehicles WHERE user_id = ?", (member.id,)
            )
            if count["cnt"] >= self._max_vehicles():
                await reply(
                    interaction,
                    f"You have reached the vehicle limit ({self._max_vehicles()}).",
                    ephemeral=True,
                )
                return
            existing = await db.execute_fetchone("SELECT id FROM vehicles WHERE plate = ?", (plate_upper,))
            if existing:
                await reply(interaction, f"Plate {plate_upper} is already registered.", ephemeral=True)
                return
            await ensure_user(db, member.id)
            days = load_config().get("registration", {}).get("vehicle_month_days", 30)
            expires_at = datetime.now(timezone.utc) + timedelta(days=days)
            await db.execute(
                """
                INSERT INTO vehicles (user_id, make, model, year, color, plate, status, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, 'active', ?)
                """,
                (member.id, make.strip(), model.strip(), year, color.strip(), plate_upper, expires_at),
            )
            await db.commit()
        except (DatabaseTimeoutError, DatabaseError):
            await reply(interaction, "Database error. Please try again.", ephemeral=True)
            return
        await log_action(
            self.bot,
            "vehicle_registration",
            interaction.user.id,
            target_id=member.id,
            details={"plate": plate_upper, "make": make, "model": model},
        )
        channel_id = load_config().get("channels", {}).get("vehicle_registration")
        if channel_id:
            ch = interaction.guild.get_channel(channel_id)
            if ch:
                embed = discord.Embed(title="Vehicle Registered", color=0x27AE60)
                embed.add_field(name="Owner", value=member.mention, inline=True)
                embed.add_field(name="Plate", value=plate_upper, inline=True)
                embed.add_field(name="Vehicle", value=f"{year} {color} {make} {model}", inline=False)
                await ch.send(embed=embed)
        await notify_vehicle_registered(
            self.bot,
            member.id,
            plate=plate_upper,
            vehicle=f"{year} {color} {make} {model}",
            guild=interaction.guild,
        )
        if interaction.guild:
            await sync_member_roles(self.bot, interaction.guild, member.id)
        reg = load_config().get("registration", {})
        await reply(
            interaction,
            f"Vehicle {plate_upper} registered. Registration expires in {reg.get('vehicle_month_days', 30)} days. "
            f"Renew for ${reg.get('vehicle_renew_cost', 32):,} in the shop (`/shop`).",
            ephemeral=True,
        )

    @app_commands.command(name="registertrailer", description="Register a trailer to your profile")
    @app_commands.describe(trailer_type="Trailer type", plate="Plate number")
    async def register_trailer(
        self,
        interaction: discord.Interaction,
        trailer_type: str,
        plate: str,
    ):
        member = interaction.user
        allowed, msg = await check_registration_allowed(member.id)
        if not allowed:
            await reply(interaction, msg, ephemeral=True)
            return
        await defer(interaction, ephemeral=True)
        plate_upper = plate.strip().upper()
        try:
            db = await get_db()
            existing = await db.execute_fetchone("SELECT id FROM trailers WHERE plate = ?", (plate_upper,))
            if existing:
                await reply(interaction, f"Plate {plate_upper} is already registered.", ephemeral=True)
                return
            await ensure_user(db, member.id)
            await db.execute(
                "INSERT INTO trailers (user_id, trailer_type, plate, status) VALUES (?, ?, ?, 'active')",
                (member.id, trailer_type.strip(), plate_upper),
            )
            await db.commit()
        except (DatabaseTimeoutError, DatabaseError):
            await reply(interaction, "Database error. Please try again.", ephemeral=True)
            return
        await log_action(
            self.bot,
            "trailer_registration",
            interaction.user.id,
            target_id=member.id,
            details={"plate": plate_upper, "type": trailer_type},
        )
        await notify_trailer_registered(
            self.bot,
            member.id,
            plate=plate_upper,
            trailer_type=trailer_type.strip(),
            guild=interaction.guild,
        )
        await reply(interaction, f"Trailer {plate_upper} registered to your profile.", ephemeral=True)

    economyadmin = app_commands.Group(name="economyadmin", description="Economy management (staff only)")

    @economyadmin.command(name="add", description="Add money to a user's wallet")
    async def eco_add(self, interaction: discord.Interaction, member: discord.Member, amount: int):
        if not is_staff(interaction.user):
            await reply(interaction, "Staff only.", ephemeral=True)
            return
        await defer(interaction, ephemeral=True)
        try:
            from utils.economy import modify_wallet

            await modify_wallet(member.id, amount, "admin_add", f"Added by {interaction.user.id}")
        except (DatabaseTimeoutError, DatabaseError):
            await reply(interaction, "Database error.", ephemeral=True)
            return
        await log_action(
            self.bot, "economy", interaction.user.id, target_id=member.id, details={"action": "add", "amount": amount}
        )
        await notify_wallet_adjustment(
            self.bot,
            member.id,
            amount=amount,
            action="Staff added funds",
            by_staff=interaction.user.display_name,
            guild=interaction.guild,
        )
        await reply(interaction, f"Added ${amount:,} to {member.mention}'s wallet.", ephemeral=True)

    @economyadmin.command(name="remove", description="Remove money from a user's wallet")
    async def eco_remove(self, interaction: discord.Interaction, member: discord.Member, amount: int):
        if not is_staff(interaction.user):
            await reply(interaction, "Staff only.", ephemeral=True)
            return
        await defer(interaction, ephemeral=True)
        try:
            from utils.economy import modify_wallet

            await modify_wallet(member.id, -amount, "admin_remove", f"Removed by {interaction.user.id}")
        except (DatabaseTimeoutError, DatabaseError):
            await reply(interaction, "Database error.", ephemeral=True)
            return
        await log_action(
            self.bot, "economy", interaction.user.id, target_id=member.id, details={"action": "remove", "amount": amount}
        )
        await notify_wallet_adjustment(
            self.bot,
            member.id,
            amount=-amount,
            action="Staff removed funds",
            by_staff=interaction.user.display_name,
            guild=interaction.guild,
        )
        await reply(interaction, f"Removed ${amount:,} from {member.mention}'s wallet.", ephemeral=True)

    @economyadmin.command(name="freeze", description="Freeze a user's economy account")
    async def eco_freeze(self, interaction: discord.Interaction, member: discord.Member):
        if not is_staff(interaction.user):
            await reply(interaction, "Staff only.", ephemeral=True)
            return
        await defer(interaction, ephemeral=True)
        try:
            from utils.economy import set_balances

            await set_balances(member.id, frozen=True)
        except (DatabaseTimeoutError, DatabaseError):
            await reply(interaction, "Database error.", ephemeral=True)
            return
        await log_action(
            self.bot, "economy", interaction.user.id, target_id=member.id, details={"action": "freeze"}
        )
        await notify_account_frozen(
            self.bot,
            member.id,
            by_staff=interaction.user.display_name,
            guild=interaction.guild,
        )
        await reply(interaction, f"Froze {member.mention}'s account.", ephemeral=True)

    @economyadmin.command(name="unfreeze", description="Unfreeze a user's economy account")
    async def eco_unfreeze(self, interaction: discord.Interaction, member: discord.Member):
        if not is_staff(interaction.user):
            await reply(interaction, "Staff only.", ephemeral=True)
            return
        await defer(interaction, ephemeral=True)
        try:
            from utils.economy import set_balances

            await set_balances(member.id, frozen=False)
        except (DatabaseTimeoutError, DatabaseError):
            await reply(interaction, "Database error.", ephemeral=True)
            return
        await log_action(
            self.bot, "economy", interaction.user.id, target_id=member.id, details={"action": "unfreeze"}
        )
        await notify_account_unfrozen(
            self.bot,
            member.id,
            by_staff=interaction.user.display_name,
            guild=interaction.guild,
        )
        await reply(interaction, f"Unfroze {member.mention}'s account.", ephemeral=True)

    @app_commands.command(name="update", description="Post an official server or bot update.")
    @app_commands.describe(
        title="The title of the update",
        description="The full update details.",
        update_type="Select the update category.",
        version="Optional update version, e.g. v2.3.1.",
        ping="Ping the configured Updates role.",
    )
    @app_commands.choices(
        update_type=[
            app_commands.Choice(name="Server Update", value="Server Update"),
            app_commands.Choice(name="Bot Update", value="Bot Update"),
            app_commands.Choice(name="Hotfix", value="Hotfix"),
            app_commands.Choice(name="Announcement", value="Announcement"),
            app_commands.Choice(name="Maintenance", value="Maintenance"),
        ]
    )
    async def update(
        self,
        interaction: discord.Interaction,
        title: str,
        description: str,
        update_type: app_commands.Choice[str] | None = None,
        version: str | None = None,
        ping: bool = False,
    ):
        if not any(role.id == 1522402531484110888 for role in interaction.user.roles):
            await reply(interaction, "You do not have permission to use this command.", ephemeral=True)
            return

        await defer(interaction, ephemeral=True)

        config = load_config()
        channel_id = int(config.get("channels", {}).get("updates", 1250622030240546887))
        target_channel = self.bot.get_channel(channel_id) or interaction.guild.get_channel(channel_id)
        if not target_channel or not isinstance(target_channel, discord.TextChannel):
            await reply(
                interaction,
                "Unable to post the update because the configured updates channel was not found.",
                ephemeral=True,
            )
            return

        embed_title = "WCRP Update"
        if version:
            embed_title = f"{embed_title} • {version.strip()}"

        embed = discord.Embed(
            title=embed_title,
            description=f"## {title.strip()}\n{description.strip()}",
            color=config.get("info_panel", {}).get("color", 0x3498DB),
            timestamp=datetime.now(timezone.utc),
        )
        if interaction.guild and interaction.guild.icon:
            embed.set_thumbnail(url=interaction.guild.icon.url)

        category_value = update_type.value if update_type else "General Update"
        embed.add_field(name="Category", value=category_value, inline=True)
        embed.add_field(name="Posted By", value=interaction.user.mention, inline=True)
        embed.add_field(
            name="Date",
            value=f"<t:{int(datetime.now(timezone.utc).timestamp())}:F>",
            inline=False,
        )
        embed.set_footer(text="Wythe County Roleplay • Official Updates")

        updates_role_id = config.get("roles", {}).get("updates")
        content = None
        if ping and updates_role_id:
            content = f"<@&{int(updates_role_id)}>"

        try:
            await target_channel.send(content=content, embed=embed, allowed_mentions=discord.AllowedMentions(roles=True))
        except discord.Forbidden:
            await reply(interaction, "The bot does not have permission to send messages in the updates channel.", ephemeral=True)
            return
        except discord.HTTPException:
            await reply(interaction, "Failed to post the update. Please try again later.", ephemeral=True)
            return

        await reply(interaction, "Successfully posted the update.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Admin(bot))
