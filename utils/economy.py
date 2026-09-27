import random
from datetime import datetime, timedelta, timezone

from utils.core import load_config
from utils.database import DatabaseError, DatabaseTimeoutError, get_db


class EconomyError(Exception):
    pass


class InsufficientFunds(EconomyError):
    pass


class AccountFrozen(EconomyError):
    pass


class CooldownActive(EconomyError):
    pass


async def _get_pool():
    db = await get_db()
    return db._pool  # noqa: SLF001 — internal pool for transactions


async def ensure_account(user_id: int) -> None:
    from utils.users import ensure_user

    db = await get_db()
    await ensure_user(db, user_id)


async def get_account(user_id: int) -> dict:
    await ensure_account(user_id)
    db = await get_db()
    row = await db.execute_fetchone(
        "SELECT user_id, wallet, bank, frozen, daily_last, work_last FROM economy_accounts WHERE user_id = ?",
        (user_id,),
    )
    return row


async def log_transaction(
    conn,
    user_id: int,
    tx_type: str,
    amount: int,
    balance_after: int,
    description: str,
    related_user: int | None = None,
) -> None:
    await conn.execute(
        """
        INSERT INTO economy_transactions (user_id, tx_type, amount, balance_after, related_user, description)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        user_id,
        tx_type,
        amount,
        balance_after,
        related_user,
        description,
    )


async def modify_wallet(
    user_id: int,
    delta: int,
    tx_type: str,
    description: str,
    *,
    related_user: int | None = None,
    allow_negative: bool = False,
) -> dict:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT wallet, bank, frozen FROM economy_accounts WHERE user_id = $1 FOR UPDATE",
                user_id,
            )
            if not row:
                cfg = load_config().get("economy", {})
                await conn.execute(
                    "INSERT INTO economy_accounts (user_id, wallet, bank) VALUES ($1, $2, $3)",
                    user_id,
                    cfg.get("starting_wallet", 1000),
                    cfg.get("starting_bank", 0),
                )
                row = await conn.fetchrow(
                    "SELECT wallet, bank, frozen FROM economy_accounts WHERE user_id = $1 FOR UPDATE",
                    user_id,
                )
            if row["frozen"]:
                raise AccountFrozen("This account is frozen.")
            new_balance = row["wallet"] + delta
            if not allow_negative and new_balance < 0:
                raise InsufficientFunds("Insufficient wallet balance.")
            await conn.execute(
                "UPDATE economy_accounts SET wallet = $1 WHERE user_id = $2",
                new_balance,
                user_id,
            )
            await log_transaction(conn, user_id, tx_type, delta, new_balance, description, related_user)
            return {"wallet": new_balance, "bank": row["bank"], "frozen": row["frozen"]}


async def set_balances(
    user_id: int,
    wallet: int | None = None,
    bank: int | None = None,
    frozen: bool | None = None,
) -> dict:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT wallet, bank, frozen FROM economy_accounts WHERE user_id = $1 FOR UPDATE",
                user_id,
            )
            if not row:
                await ensure_account(user_id)
                row = await conn.fetchrow(
                    "SELECT wallet, bank, frozen FROM economy_accounts WHERE user_id = $1 FOR UPDATE",
                    user_id,
                )
            new_wallet = wallet if wallet is not None else row["wallet"]
            new_bank = bank if bank is not None else row["bank"]
            new_frozen = frozen if frozen is not None else row["frozen"]
            await conn.execute(
                "UPDATE economy_accounts SET wallet = $1, bank = $2, frozen = $3 WHERE user_id = $4",
                new_wallet,
                new_bank,
                int(new_frozen),
                user_id,
            )
            await log_transaction(
                conn,
                user_id,
                "admin_set",
                0,
                new_wallet,
                f"Balance set | wallet: {new_wallet}, bank: {new_bank}, frozen: {new_frozen}",
            )
            return {"wallet": new_wallet, "bank": new_bank, "frozen": new_frozen}


async def transfer(from_id: int, to_id: int, amount: int) -> None:
    if amount <= 0:
        raise EconomyError("Amount must be positive.")
    if from_id == to_id:
        raise EconomyError("Cannot pay yourself.")
    pool = await _get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            sender = await conn.fetchrow(
                "SELECT wallet, frozen FROM economy_accounts WHERE user_id = $1 FOR UPDATE",
                from_id,
            )
            receiver = await conn.fetchrow(
                "SELECT wallet, frozen FROM economy_accounts WHERE user_id = $1 FOR UPDATE",
                to_id,
            )
            if not sender or not receiver:
                raise EconomyError("Account not found.")
            if sender["frozen"] or receiver["frozen"]:
                raise AccountFrozen("One or both accounts are frozen.")
            if sender["wallet"] < amount:
                raise InsufficientFunds("Insufficient wallet balance.")
            new_sender = sender["wallet"] - amount
            new_receiver = receiver["wallet"] + amount
            await conn.execute(
                "UPDATE economy_accounts SET wallet = $1 WHERE user_id = $2",
                new_sender,
                from_id,
            )
            await conn.execute(
                "UPDATE economy_accounts SET wallet = $1 WHERE user_id = $2",
                new_receiver,
                to_id,
            )
            await log_transaction(conn, from_id, "transfer_out", -amount, new_sender, f"Paid user {to_id}", to_id)
            await log_transaction(conn, to_id, "transfer_in", amount, new_receiver, f"Received from {from_id}", from_id)


async def claim_daily(user_id: int) -> int:
    cfg = load_config().get("economy", {})
    amount = cfg.get("daily_amount", 500)
    cooldown_hours = cfg.get("daily_cooldown_hours", 24)
    pool = await _get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT wallet, frozen, daily_last FROM economy_accounts WHERE user_id = $1 FOR UPDATE",
                user_id,
            )
            if not row:
                await ensure_account(user_id)
                row = await conn.fetchrow(
                    "SELECT wallet, frozen, daily_last FROM economy_accounts WHERE user_id = $1 FOR UPDATE",
                    user_id,
                )
            if row["frozen"]:
                raise AccountFrozen("This account is frozen.")
            now = datetime.now(timezone.utc)
            if row["daily_last"]:
                next_claim = row["daily_last"] + timedelta(hours=cooldown_hours)
                if now < next_claim:
                    raise CooldownActive(f"Daily available <t:{int(next_claim.timestamp())}:R>")
            new_balance = row["wallet"] + amount
            await conn.execute(
                "UPDATE economy_accounts SET wallet = $1, daily_last = $2 WHERE user_id = $3",
                new_balance,
                now,
                user_id,
            )
            await log_transaction(conn, user_id, "daily", amount, new_balance, "Daily reward claimed")
            return amount


async def claim_work(user_id: int) -> tuple[int, str]:
    cfg = load_config().get("economy", {})
    jobs = cfg.get("work_jobs", ["Delivery Driver", "Mechanic", "Cashier", "Security Guard"])
    min_pay = cfg.get("work_min", 100)
    max_pay = cfg.get("work_max", 350)
    cooldown_min = cfg.get("work_cooldown_minutes", 30)
    pool = await _get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT wallet, frozen, work_last FROM economy_accounts WHERE user_id = $1 FOR UPDATE",
                user_id,
            )
            if not row:
                await ensure_account(user_id)
                row = await conn.fetchrow(
                    "SELECT wallet, frozen, work_last FROM economy_accounts WHERE user_id = $1 FOR UPDATE",
                    user_id,
                )
            if row["frozen"]:
                raise AccountFrozen("This account is frozen.")
            now = datetime.now(timezone.utc)
            if row["work_last"]:
                next_work = row["work_last"] + timedelta(minutes=cooldown_min)
                if now < next_work:
                    raise CooldownActive(f"Work available <t:{int(next_work.timestamp())}:R>")
            job = random.choice(jobs)
            amount = random.randint(min_pay, max_pay)
            new_balance = row["wallet"] + amount
            await conn.execute(
                "UPDATE economy_accounts SET wallet = $1, work_last = $2 WHERE user_id = $3",
                new_balance,
                now,
                user_id,
            )
            await log_transaction(conn, user_id, "work", amount, new_balance, f"Worked as {job}")
            return amount, job


async def get_leaderboard(limit: int = 10) -> list[dict]:
    db = await get_db()
    return await db.execute_fetchall(
        """
        SELECT user_id, wallet, bank, (wallet + bank) AS net_worth
        FROM economy_accounts
        ORDER BY net_worth DESC
        LIMIT ?
        """,
        (limit,),
    )


async def get_transactions(user_id: int, limit: int = 10) -> list[dict]:
    db = await get_db()
    return await db.execute_fetchall(
        """
        SELECT tx_type, amount, balance_after, description, created_at
        FROM economy_transactions
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (user_id, limit),
    )
