import discord
from discord import app_commands
from discord.ext import commands

from utils.helpers import log_action
from utils.core import load_config
from utils.database import DatabaseError, DatabaseTimeoutError, get_db
from utils.core import format_date
from utils.core import defer, reply
from utils.users import (
    get_user_row,
    grant_insurance,
    grant_license,
    is_license_suspended,
    is_registration_suspended,
    license_is_valid,
    renew_vehicle_registration,
    set_license_suspended,
    set_registration_suspended,
    sync_member_roles,
)

REGISTRATION_SKUS = frozenset({
    "drivers_license",
    "insurance",
    "vehicle_renewal",
    "license_buyback",
    "registration_buyback",
})


async def sync_registration_shop_items() -> None:
    reg = load_config().get("registration", {})
    license_months = reg.get("license_months", 2)
    vehicle_days = reg.get("vehicle_month_days", 30)
    items = [
        (
            "drivers_license",
            "Driver's License",
            "registration",
            f"Valid for {license_months} months. Required to register vehicles.",
            reg.get("license_cost", 37),
        ),
        (
            "insurance",
            "Vehicle Insurance",
            "registration",
            "Valid for 1 month. Covers your registered vehicles.",
            reg.get("insurance_cost", 31),
        ),
        (
            "vehicle_renewal",
            "Vehicle Registration Renewal",
            "registration",
            f"Renews one vehicle plate for {vehicle_days} days.",
            reg.get("vehicle_renew_cost", 32),
        ),
        (
            "license_buyback",
            "License Buy Back",
            "registration",
            "Restore a suspended or revoked driver's license to active.",
            reg.get("license_buyback_cost", 500),
        ),
        (
            "registration_buyback",
            "Registration Buy Back",
            "registration",
            "Restore suspended vehicle and trailer registration privileges.",
            reg.get("registration_buyback_cost", 500),
        ),
    ]
    db = await get_db()
    pool = db._pool  # noqa: SLF001
    async with pool.acquire() as conn:
        async with conn.transaction():
            for item_key, name, category, description, price in items:
                existing = await conn.fetchval(
                    "SELECT id FROM shop_items WHERE item_key = $1",
                    item_key,
                )
                if existing:
                    await conn.execute(
                        """
                        UPDATE shop_items
                        SET name = $1, category = $2, description = $3, price = $4, active = 1
                        WHERE item_key = $5
                        """,
                        name,
                        category,
                        description,
                        price,
                        item_key,
                    )
                else:
                    await conn.execute(
                        """
                        INSERT INTO shop_items (name, category, description, price, stock, active, item_key)
                        VALUES ($1, $2, $3, $4, -1, 1, $5)
                        """,
                        name,
                        category,
                        description,
                        price,
                        item_key,
                    )


async def _validate_purchase(
    user_id: int,
    item_key: str,
    *,
    plate: str | None = None,
) -> tuple[bool, str, int | None]:
    if item_key == "drivers_license":
        db = await get_db()
        row = await get_user_row(db, user_id)
        if license_is_valid(row):
            return False, "You already have a valid driver's license.", None
        if await is_license_suspended(user_id):
            return False, "Your license is suspended. Purchase **License Buy Back** instead.", None

    if item_key == "license_buyback":
        if not await is_license_suspended(user_id):
            db = await get_db()
            row = await get_user_row(db, user_id)
            if license_is_valid(row):
                return False, "Your license is already active.", None
            return False, "Your license is not suspended. Purchase a **Driver's License** instead.", None

    if item_key == "registration_buyback":
        if not await is_registration_suspended(user_id):
            return False, "Your registration is not suspended.", None

    if item_key == "vehicle_renewal":
        if not plate or not plate.strip():
            return False, "Enter your vehicle plate to renew.", None
        plate_upper = plate.strip().upper()
        db = await get_db()
        vehicle = await db.execute_fetchone(
            "SELECT id FROM vehicles WHERE user_id = ? AND plate = ?",
            (user_id, plate_upper),
        )
        if not vehicle:
            return False, f"Vehicle **{plate_upper}** not found on your profile.", None
        return True, "", vehicle["id"]

    return True, "", None


async def _apply_purchase(
    bot: commands.Bot,
    interaction: discord.Interaction,
    item_key: str,
    *,
    plate: str | None = None,
    vehicle_id: int | None = None,
) -> str:
    user_id = interaction.user.id
    guild = interaction.guild
    reg = load_config().get("registration", {})

    if item_key == "drivers_license":
        expires = await grant_license(user_id)
        if guild:
            await sync_member_roles(bot, guild, user_id)
        months = reg.get("license_months", 2)
        return f"Driver's license active until **{format_date(expires)}** ({months} months)."

    if item_key == "insurance":
        expires = await grant_insurance(user_id)
        if guild:
            await sync_member_roles(bot, guild, user_id)
        return f"Insurance active until **{format_date(expires)}**."

    if item_key == "vehicle_renewal":
        plate_upper = plate.strip().upper()
        expires = await renew_vehicle_registration(vehicle_id, user_id)
        if guild:
            await sync_member_roles(bot, guild, user_id)
        return f"Vehicle **{plate_upper}** renewed until **{format_date(expires)}**."

    if item_key == "license_buyback":
        await set_license_suspended(
            user_id,
            False,
            notify_client=bot,
            guild=guild,
            reason="License restored via shop buy back.",
        )
        expires = await grant_license(user_id)
        if guild:
            await sync_member_roles(bot, guild, user_id)
        return f"License restored and active until **{format_date(expires)}**."

    if item_key == "registration_buyback":
        await set_registration_suspended(
            user_id,
            False,
            notify_client=bot,
            guild=guild,
            reason="Registration restored via shop buy back.",
        )
        if guild:
            await sync_member_roles(bot, guild, user_id)
        return "Registration privileges restored. Suspended vehicles and trailers are active again."

    return ""


async def purchase_shop_item(
    bot: commands.Bot,
    interaction: discord.Interaction,
    item_key: str,
    *,
    plate: str | None = None,
) -> None:
    user_id = interaction.user.id
    ok, err, vehicle_id = await _validate_purchase(user_id, item_key, plate=plate)
    if not ok:
        await reply(interaction, err, ephemeral=True)
        return

    db = await get_db()
    item = await db.execute_fetchone(
        "SELECT * FROM shop_items WHERE item_key = ? AND active = 1",
        (item_key,),
    )
    if not item:
        await reply(interaction, "Item not available.", ephemeral=True)
        return

    pool = db._pool  # noqa: SLF001
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                item = await conn.fetchrow(
                    "SELECT * FROM shop_items WHERE item_key = $1 AND active = 1 FOR UPDATE",
                    item_key,
                )
                if not item or item["stock"] == 0:
                    await reply(interaction, "Item not found or out of stock.", ephemeral=True)
                    return
                account = await conn.fetchrow(
                    "SELECT wallet, frozen FROM economy_accounts WHERE user_id = $1 FOR UPDATE",
                    user_id,
                )
                if not account or account["frozen"]:
                    await reply(interaction, "Account unavailable.", ephemeral=True)
                    return
                if account["wallet"] < item["price"]:
                    await reply(interaction, f"Insufficient funds. Need ${item['price']:,}.", ephemeral=True)
                    return

                new_balance = account["wallet"] - item["price"]
                await conn.execute(
                    "UPDATE economy_accounts SET wallet = $1 WHERE user_id = $2",
                    new_balance,
                    user_id,
                )
                await conn.execute(
                    """
                    INSERT INTO economy_transactions (user_id, tx_type, amount, balance_after, description)
                    VALUES ($1, 'shop_purchase', $2, $3, $4)
                    """,
                    user_id,
                    -item["price"],
                    new_balance,
                    f"Purchased {item['name']}",
                )
                if item["stock"] > 0:
                    await conn.execute(
                        "UPDATE shop_items SET stock = stock - 1 WHERE item_key = $1", item_key
                    )
                if item_key not in REGISTRATION_SKUS:
                    await conn.execute(
                        """
                        INSERT INTO shop_inventory (user_id, item_id, quantity)
                        VALUES ($1, $2, 1)
                        ON CONFLICT (user_id, item_id) DO UPDATE
                        SET quantity = shop_inventory.quantity + 1
                        """,
                        user_id,
                        item["id"],
                    )
    except (DatabaseTimeoutError, DatabaseError):
        await reply(interaction, "Database error. Please try again.", ephemeral=True)
        return

    extra = ""
    if item_key in REGISTRATION_SKUS:
        extra = await _apply_purchase(bot, interaction, item_key, plate=plate, vehicle_id=vehicle_id)

    await log_action(
        bot,
        "shop",
        user_id,
        details={
            "item_id": item["id"],
            "item": item["name"],
            "price": item["price"],
            "sku": item_key,
            "plate": plate,
        },
    )
    msg = f"Purchased **{item['name']}** for ${item['price']:,}."
    if extra:
        msg += f"\n{extra}"
    await reply(interaction, msg, ephemeral=True)


class VehiclePlateModal(discord.ui.Modal, title="Vehicle Plate"):
    plate_input = discord.ui.TextInput(
        label="License Plate",
        placeholder="Enter plate to renew",
        required=True,
        max_length=20,
    )

    def __init__(self, bot: commands.Bot):
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction):
        await defer(interaction, ephemeral=True)
        await purchase_shop_item(
            self.bot,
            interaction,
            "vehicle_renewal",
            plate=self.plate_input.value,
        )


class ShopBuyButton(discord.ui.Button):
    def __init__(self, item: dict):
        label = f"Buy {item['name'][:40]} | ${item['price']:,}"
        super().__init__(
            label=label[:80],
            style=discord.ButtonStyle.success,
            custom_id=f"shop_item_{item['item_key']}",
        )


class ShopCategoryView(discord.ui.View):
    def __init__(self, items: list[dict]):
        super().__init__(timeout=180)
        for item in items[:25]:
            if item.get("item_key"):
                self.add_item(ShopBuyButton(item))


class ShopCategorySelect(discord.ui.Select):
    def __init__(self, bot: commands.Bot, categories: list[str]):
        self.bot = bot
        options = [
            discord.SelectOption(label=cat.title(), value=cat)
            for cat in categories[:25]
        ]
        super().__init__(placeholder="Select a category...", options=options)

    async def callback(self, interaction: discord.Interaction):
        await defer(interaction, ephemeral=True)
        db = await get_db()
        items = await db.execute_fetchall(
            """
            SELECT id, name, description, price, stock, item_key
            FROM shop_items WHERE category = ? AND active = 1 ORDER BY price, name
            """,
            (self.values[0],),
        )
        if not items:
            await reply(interaction, "No items in this category.", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"Shop | {self.values[0].title()}",
            description="Press a button below to purchase.",
            color=0x2B2D31,
        )
        for item in items:
            stock = "Unlimited" if item["stock"] < 0 else str(item["stock"])
            hint = ""
            if item.get("item_key") == "vehicle_renewal":
                hint = " (you will be asked for a plate)"
            embed.add_field(
                name=f"{item['name']} | ${item['price']:,}",
                value=f"{item['description'] or 'No description'}{hint}\nStock: {stock}",
                inline=False,
            )

        view = ShopCategoryView(items)
        await reply(interaction, embed=embed, view=view, ephemeral=True)


class ShopBrowseView(discord.ui.View):
    def __init__(self, bot: commands.Bot, categories: list[str]):
        super().__init__(timeout=180)
        self.add_item(ShopCategorySelect(bot, categories))


class Shop(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _open_shop(self, interaction: discord.Interaction) -> None:
        await defer(interaction, ephemeral=True)
        db = await get_db()
        cats = await db.execute_fetchall(
            "SELECT DISTINCT category FROM shop_items WHERE active = 1 ORDER BY category"
        )
        if not cats:
            await reply(interaction, "The shop has no items listed yet.", ephemeral=True)
            return

        categories = [c["category"] for c in cats]
        embed = discord.Embed(
            title="VSRP Shop",
            description=(
                "Select a category, then press **Buy** on the item you want.\n\n"
                "**Registration:** licenses, insurance, renewals, and buy backs."
            ),
            color=0x2B2D31,
        )
        view = ShopBrowseView(self.bot, categories)
        await reply(interaction, embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="shop", description="Browse and purchase from the server shop")
    async def shop(self, interaction: discord.Interaction):
        await self._open_shop(interaction)

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        cid = interaction.data.get("custom_id", "") if interaction.data else ""
        if not cid.startswith("shop_item_"):
            return
        item_key = cid.removeprefix("shop_item_")
        if item_key == "vehicle_renewal":
            await interaction.response.send_modal(VehiclePlateModal(self.bot))
            return
        await defer(interaction, ephemeral=True)
        await purchase_shop_item(self.bot, interaction, item_key)


async def setup(bot: commands.Bot):
    try:
        await sync_registration_shop_items()
    except Exception:
        pass
    await bot.add_cog(Shop(bot))
