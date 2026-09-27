import logging
from datetime import datetime, timedelta, timezone

import discord

from utils.core import load_config
from utils.database import Database, get_db

log = logging.getLogger("vsrp_bot.users")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _reg_cfg() -> dict:
    return load_config().get("registration", {})


async def ensure_user(db: Database, user_id: int) -> None:
    cfg = load_config().get("economy", {})
    await db.execute(
        "INSERT INTO users (user_id) VALUES (?) ON CONFLICT (user_id) DO NOTHING",
        (user_id,),
    )
    await db.execute(
        """
        INSERT INTO economy_accounts (user_id, wallet, bank)
        VALUES (?, ?, ?)
        ON CONFLICT (user_id) DO NOTHING
        """,
        (user_id, cfg.get("starting_wallet", 1000), cfg.get("starting_bank", 0)),
    )


async def get_user_row(db: Database, user_id: int) -> dict:
    await ensure_user(db, user_id)
    row = await db.execute_fetchone("SELECT * FROM users WHERE user_id = ?", (user_id,))
    return row or {
        "user_id": user_id,
        "registration_suspended": 0,
        "license_suspended": 0,
        "license_expires_at": None,
        "insurance_expires_at": None,
    }


async def is_registration_suspended(user_id: int) -> bool:
    db = await get_db()
    row = await get_user_row(db, user_id)
    return bool(row.get("registration_suspended"))


async def is_license_suspended(user_id: int) -> bool:
    db = await get_db()
    row = await get_user_row(db, user_id)
    return bool(row.get("license_suspended"))


def license_is_valid(row: dict) -> bool:
    if row.get("license_suspended"):
        return False
    expires = row.get("license_expires_at")
    if not expires:
        return False
    if isinstance(expires, datetime) and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return expires > _now()


def insurance_is_valid(row: dict) -> bool:
    expires = row.get("insurance_expires_at")
    if not expires:
        return False
    if isinstance(expires, datetime) and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return expires > _now()


async def has_valid_vehicle_registration(user_id: int) -> bool:
    db = await get_db()
    row = await db.execute_fetchone(
        """
        SELECT COUNT(*) AS cnt FROM vehicles
        WHERE user_id = ? AND status = 'active'
          AND (expires_at IS NULL OR expires_at > NOW())
        """,
        (user_id,),
    )
    return bool(row and row["cnt"] > 0)


async def _apply_registration_records(db: Database, user_id: int, *, suspended: bool) -> None:
    if suspended:
        await db.execute(
            "UPDATE vehicles SET status = 'suspended' WHERE user_id = ? AND status = 'active'",
            (user_id,),
        )
        await db.execute(
            "UPDATE trailers SET status = 'suspended' WHERE user_id = ? AND status = 'active'",
            (user_id,),
        )
    else:
        await db.execute(
            """
            UPDATE vehicles SET status = 'active'
            WHERE user_id = ? AND status = 'suspended'
              AND (expires_at IS NULL OR expires_at > NOW())
            """,
            (user_id,),
        )
        await db.execute(
            "UPDATE trailers SET status = 'active' WHERE user_id = ? AND status = 'suspended'",
            (user_id,),
        )


async def set_registration_suspended(
    user_id: int,
    suspended: bool,
    *,
    notify_client=None,
    guild=None,
    reason: str | None = None,
) -> bool:
    db = await get_db()
    await ensure_user(db, user_id)
    row = await db.execute_fetchone(
        "SELECT registration_suspended FROM users WHERE user_id = ?",
        (user_id,),
    )
    was_suspended = bool(row and row["registration_suspended"])
    if was_suspended == suspended:
        return False

    await db.execute(
        "UPDATE users SET registration_suspended = ? WHERE user_id = ?",
        (1 if suspended else 0, user_id),
    )
    await _apply_registration_records(db, user_id, suspended=suspended)
    await db.commit()

    if notify_client and guild:
        await sync_member_roles(notify_client, guild, user_id)
        from utils.helpers import notify_registration_restored, notify_registration_suspended

        if suspended:
            await notify_registration_suspended(
                notify_client, user_id, reason=reason or "Registration suspended by staff.", guild=guild
            )
        else:
            await notify_registration_restored(notify_client, user_id, reason=reason, guild=guild)
    return True


async def set_license_suspended(
    user_id: int,
    suspended: bool,
    *,
    notify_client=None,
    guild=None,
    reason: str | None = None,
) -> bool:
    db = await get_db()
    await ensure_user(db, user_id)
    row = await db.execute_fetchone(
        "SELECT license_suspended FROM users WHERE user_id = ?",
        (user_id,),
    )
    was_suspended = bool(row and row["license_suspended"])
    if was_suspended == suspended:
        return False

    await db.execute(
        "UPDATE users SET license_suspended = ? WHERE user_id = ?",
        (1 if suspended else 0, user_id),
    )
    await db.commit()

    if notify_client and guild:
        await sync_member_roles(notify_client, guild, user_id)
        from utils.helpers import notify_license_restored, notify_license_suspended

        if suspended:
            await notify_license_suspended(
                notify_client, user_id, reason=reason or "License suspended.", guild=guild
            )
        else:
            await notify_license_restored(notify_client, user_id, reason=reason, guild=guild)
    return True


async def grant_license(user_id: int, months: int | None = None) -> datetime:
    cfg = _reg_cfg()
    months = months or cfg.get("license_months", 2)
    db = await get_db()
    await ensure_user(db, user_id)
    row = await get_user_row(db, user_id)
    base = _now()
    if license_is_valid(row):
        exp = row["license_expires_at"]
        if isinstance(exp, datetime) and exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        base = max(base, exp)
    expires = base + timedelta(days=30 * months)
    await db.execute(
        "UPDATE users SET license_expires_at = ?, license_suspended = 0 WHERE user_id = ?",
        (expires, user_id),
    )
    await db.commit()
    return expires


async def grant_insurance(user_id: int, months: int | None = None) -> datetime:
    cfg = _reg_cfg()
    months = months or cfg.get("insurance_months", 1)
    db = await get_db()
    await ensure_user(db, user_id)
    row = await get_user_row(db, user_id)
    base = _now()
    if insurance_is_valid(row):
        exp = row["insurance_expires_at"]
        if isinstance(exp, datetime) and exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        base = max(base, exp)
    expires = base + timedelta(days=30 * months)
    await db.execute(
        "UPDATE users SET insurance_expires_at = ? WHERE user_id = ?",
        (expires, user_id),
    )
    await db.commit()
    return expires


async def renew_vehicle_registration(vehicle_id: int, user_id: int) -> datetime:
    cfg = _reg_cfg()
    days = cfg.get("vehicle_month_days", 30)
    db = await get_db()
    expires = _now() + timedelta(days=days)
    await db.execute(
        """
        UPDATE vehicles SET expires_at = ?, status = 'active'
        WHERE id = ? AND user_id = ?
        """,
        (expires, vehicle_id, user_id),
    )
    await db.commit()
    return expires


async def sync_member_roles(client: discord.Client, guild: discord.Guild, user_id: int) -> None:
    roles_cfg = load_config().get("roles", {})
    licensed_id = roles_cfg.get("licensed") or 0
    license_suspended_id = roles_cfg.get("license_suspended") or 0
    insured_id = roles_cfg.get("insured") or 0
    vehicle_registered_id = roles_cfg.get("vehicle_registered") or 0

    member = guild.get_member(user_id)
    if not member:
        try:
            member = await guild.fetch_member(user_id)
        except discord.HTTPException:
            return

    db = await get_db()
    row = await get_user_row(db, user_id)
    valid_license = license_is_valid(row)
    valid_insurance = insurance_is_valid(row)
    valid_vehicle = await has_valid_vehicle_registration(user_id)
    license_suspended = bool(row.get("license_suspended"))

    desired: set[int] = set()
    if valid_license and licensed_id:
        desired.add(licensed_id)
    if license_suspended and license_suspended_id:
        desired.add(license_suspended_id)
    if valid_insurance and insured_id:
        desired.add(insured_id)
    if valid_vehicle and vehicle_registered_id:
        desired.add(vehicle_registered_id)

    managed = {rid for rid in (licensed_id, license_suspended_id, insured_id, vehicle_registered_id) if rid}
    to_add = [guild.get_role(rid) for rid in desired if rid in managed]
    to_add = [r for r in to_add if r and r not in member.roles]
    to_remove = [
        guild.get_role(rid)
        for rid in managed
        if rid not in desired and guild.get_role(rid) in member.roles
    ]
    to_remove = [r for r in to_remove if r]

    try:
        if to_add:
            await member.add_roles(*to_add, reason="VSRP registration sync")
        if to_remove:
            await member.remove_roles(*to_remove, reason="VSRP registration sync")
    except discord.HTTPException:
        log.warning("Role sync failed for user %s", user_id)


async def process_expirations(client: discord.Client) -> None:
    guild_id = load_config().get("guild", {}).get("id")
    if not guild_id:
        return
    guild = client.get_guild(guild_id)
    if not guild:
        return

    db = await get_db()
    expired_vehicles = await db.execute_fetchall(
        """
        SELECT DISTINCT user_id FROM vehicles
        WHERE status = 'active' AND expires_at IS NOT NULL AND expires_at <= NOW()
        """,
    )
    if expired_vehicles:
        await db.execute(
            """
            UPDATE vehicles SET status = 'expired'
            WHERE status = 'active' AND expires_at IS NOT NULL AND expires_at <= NOW()
            """,
        )
        await db.commit()

    license_rows = await db.execute_fetchall(
        """
        SELECT user_id FROM users
        WHERE license_expires_at IS NOT NULL AND license_expires_at <= NOW()
          AND license_suspended = 0
        """,
    )
    insurance_rows = await db.execute_fetchall(
        """
        SELECT user_id FROM users
        WHERE insurance_expires_at IS NOT NULL AND insurance_expires_at <= NOW()
        """,
    )

    affected = {r["user_id"] for r in expired_vehicles}
    affected.update(r["user_id"] for r in license_rows)
    affected.update(r["user_id"] for r in insurance_rows)

    for user_id in affected:
        await sync_member_roles(client, guild, user_id)
