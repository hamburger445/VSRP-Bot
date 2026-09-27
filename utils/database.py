import asyncio
import logging
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import asyncpg

from utils.core import get_database_url, load_config

log = logging.getLogger("vsrp_bot.db")

_pool: asyncpg.Pool | None = None
_db: "Database | None" = None
_pool_lock = asyncio.Lock()

_TRANSIENT_DB_ERRORS = (
    asyncpg.ConnectionDoesNotExistError,
    asyncpg.InterfaceError,
    asyncpg.PostgresConnectionError,
    asyncpg.InternalClientError,
    ConnectionResetError,
    TimeoutError,
    asyncio.TimeoutError,
    OSError,
)


class DatabaseTimeoutError(Exception):
    pass


class DatabaseError(Exception):
    pass


def _normalize_url(url: str) -> str:
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    params.pop("channel_binding", None)
    if "sslmode" not in params:
        params["sslmode"] = ["require"]
    new_query = urlencode({k: v[0] for k, v in params.items()})
    return urlunparse(parsed._replace(query=new_query))


def _query_timeout() -> float | None:
    raw = load_config().get("database", {}).get("query_timeout_seconds", 0)
    if raw is None or raw == 0:
        return None
    return float(raw)


def _pool_timeout(key: str, default: int | None) -> float | None:
    raw = load_config().get("database", {}).get(key, default)
    if raw is None or raw == 0:
        return None
    return float(raw)


_SCHEMA_VERSION = 3


def _to_pg_placeholders(query: str) -> str:
    if "?" not in query:
        return query
    parts = query.split("?")
    return parts[0] + "".join(f"${i}{part}" for i, part in enumerate(parts[1:], 1))


@dataclass
class ExecuteResult:
    rowcount: int


class Database:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def _execute_with_retry(self, runner):
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                timeout = _query_timeout()
                coro = runner(self._pool)
                if timeout:
                    return await asyncio.wait_for(coro, timeout=timeout)
                return await coro
            except _TRANSIENT_DB_ERRORS as exc:
                last_exc = exc
                if attempt == 0:
                    log.warning("Database connection lost (%s), reconnecting pool", exc)
                    await reset_pool()
                    db = await get_db()
                    self._pool = db._pool
                    continue
                log.warning("Database unavailable after retry: %s", exc)
                raise DatabaseError(str(exc)) from exc
            except asyncio.TimeoutError as exc:
                raise DatabaseTimeoutError(f"Query exceeded {_query_timeout()}s") from exc
            except asyncpg.PostgresError as exc:
                log.exception("PostgreSQL error")
                raise DatabaseError(str(exc)) from exc
        if last_exc:
            raise DatabaseError(str(last_exc)) from last_exc
        raise DatabaseError("Database query failed")

    async def execute_fetchone(self, query: str, params: tuple = ()) -> dict | None:
        query = _to_pg_placeholders(query)

        async def _run(pool: asyncpg.Pool):
            async with pool.acquire() as conn:
                row = await conn.fetchrow(query, *params)
                return dict(row) if row else None

        return await self._execute_with_retry(_run)

    async def execute_fetchall(self, query: str, params: tuple = ()) -> list[dict]:
        query = _to_pg_placeholders(query)

        async def _run(pool: asyncpg.Pool):
            async with pool.acquire() as conn:
                rows = await conn.fetch(query, *params)
                return [dict(row) for row in rows]

        return await self._execute_with_retry(_run)

    async def execute(self, query: str, params: tuple = ()) -> ExecuteResult:
        query = _to_pg_placeholders(query)

        async def _run(pool: asyncpg.Pool):
            async with pool.acquire() as conn:
                status = await conn.execute(query, *params)
                parts = status.split()
                count = int(parts[-1]) if parts and parts[-1].isdigit() else 0
                return ExecuteResult(rowcount=count)

        return await self._execute_with_retry(_run)

    async def commit(self) -> None:
        pass


async def reset_pool() -> None:
    global _pool, _db
    async with _pool_lock:
        if _pool is not None:
            try:
                await _pool.close()
            except Exception as exc:
                log.warning("Error closing database pool: %s", exc)
        _pool = None
        _db = None


async def _create_pool() -> tuple[asyncpg.Pool, Database]:
    cfg = load_config().get("database", {})
    url = _normalize_url(get_database_url())
    pool = await asyncpg.create_pool(
        url,
        min_size=cfg.get("pool_min", 1),
        max_size=cfg.get("pool_max", 3),
        command_timeout=_pool_timeout("command_timeout_seconds", 5),
        timeout=_pool_timeout("connect_timeout_seconds", 5),
        max_inactive_connection_lifetime=60,
        max_queries=5000,
        statement_cache_size=20,
    )
    await init_tables(pool)
    return pool, Database(pool)


async def get_db() -> Database:
    global _pool, _db
    if _db is not None:
        return _db
    async with _pool_lock:
        if _db is None:
            _pool, _db = await _create_pool()
            log.info("PostgreSQL pool ready")
    return _db


async def close_db() -> None:
    await reset_pool()


async def init_tables(pool: asyncpg.Pool) -> None:
    statements = [
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            registration_suspended INTEGER DEFAULT 0,
            license_suspended INTEGER DEFAULT 0,
            license_expires_at TIMESTAMPTZ,
            insurance_expires_at TIMESTAMPTZ,            verified_at TIMESTAMPTZ,            created_at TIMESTAMPTZ DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS vehicles (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            make TEXT NOT NULL,
            model TEXT NOT NULL,
            year INTEGER NOT NULL,
            color TEXT NOT NULL,
            plate TEXT NOT NULL UNIQUE,
            status TEXT DEFAULT 'active',
            expires_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS trailers (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            trailer_type TEXT NOT NULL,
            plate TEXT NOT NULL UNIQUE,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS citation_tickets (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            violation TEXT NOT NULL,
            fine INTEGER NOT NULL DEFAULT 0,
            issued_by BIGINT NOT NULL,
            payment_status TEXT DEFAULT 'unpaid',
            paid_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS warrants (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            reason TEXT NOT NULL,
            issued_by BIGINT NOT NULL,
            active INTEGER DEFAULT 1,
            status TEXT DEFAULT 'active',
            cleared_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS economy_accounts (
            user_id BIGINT PRIMARY KEY,
            wallet BIGINT NOT NULL DEFAULT 0,
            bank BIGINT NOT NULL DEFAULT 0,
            frozen INTEGER DEFAULT 0,
            daily_last TIMESTAMPTZ,
            work_last TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS economy_transactions (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            tx_type TEXT NOT NULL,
            amount BIGINT NOT NULL,
            balance_after BIGINT NOT NULL,
            related_user BIGINT,
            description TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS shop_items (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'general',
            description TEXT DEFAULT '',
            price BIGINT NOT NULL DEFAULT 0,
            stock INTEGER DEFAULT -1,
            active INTEGER DEFAULT 1,
            item_key TEXT UNIQUE,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS shop_inventory (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            item_id INTEGER NOT NULL REFERENCES shop_items(id) ON DELETE CASCADE,
            quantity INTEGER NOT NULL DEFAULT 1,
            UNIQUE(user_id, item_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS audit_logs (
            id SERIAL PRIMARY KEY,
            action_type TEXT NOT NULL,
            actor_id BIGINT NOT NULL,
            target_id BIGINT,
            details TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS blackjack_games (
            user_id BIGINT PRIMARY KEY,
            bet BIGINT NOT NULL,
            player_cards TEXT NOT NULL,
            dealer_cards TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            doubled INTEGER DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS support_tickets (
            id SERIAL PRIMARY KEY,
            channel_id BIGINT NOT NULL UNIQUE,
            user_id BIGINT NOT NULL,
            category TEXT NOT NULL,
            status TEXT DEFAULT 'open',
            frozen INTEGER DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS reaction_roles (
            id SERIAL PRIMARY KEY,
            message_id BIGINT NOT NULL UNIQUE,
            channel_id BIGINT NOT NULL,
            guild_id BIGINT NOT NULL,
            title TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS reaction_role_entries (
            id SERIAL PRIMARY KEY,
            message_id BIGINT NOT NULL REFERENCES reaction_roles(message_id) ON DELETE CASCADE,
            emoji TEXT NOT NULL,
            role_id BIGINT NOT NULL,
            UNIQUE(message_id, emoji)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS session_state (
            guild_id BIGINT PRIMARY KEY,
            active INTEGER DEFAULT 0,
            started_by BIGINT,
            started_at TIMESTAMPTZ
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS moderation_strikes (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            discord_id BIGINT NOT NULL,
            roblox_username TEXT NOT NULL,
            strike_number INTEGER NOT NULL,
            proof TEXT NOT NULL,
            issued_by BIGINT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS moderation_bans (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            discord_id BIGINT NOT NULL,
            roblox_username TEXT NOT NULL,
            ban_type TEXT NOT NULL,
            proof TEXT NOT NULL,
            appealable INTEGER NOT NULL DEFAULT 1,
            issued_by BIGINT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS mod_cases (
            id SERIAL PRIMARY KEY,
            guild_id BIGINT NOT NULL,
            user_id BIGINT NOT NULL,
            action_type TEXT NOT NULL,
            reason TEXT NOT NULL,
            moderator_id BIGINT NOT NULL,
            active INTEGER DEFAULT 1,
            appealable INTEGER DEFAULT 1,
            duration TEXT,
            extra TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS bot_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS guild_settings (
            guild_id BIGINT PRIMARY KEY,
            staff_role_id BIGINT,
            admin_role_id BIGINT,
            permissions_json TEXT,
            configured_at TIMESTAMPTZ DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS shift_sessions (
            id SERIAL PRIMARY KEY,
            guild_id BIGINT NOT NULL,
            user_id BIGINT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            started_at TIMESTAMPTZ NOT NULL,
            ended_at TIMESTAMPTZ,
            break_seconds INTEGER NOT NULL DEFAULT 0,
            break_started_at TIMESTAMPTZ,
            adjust_seconds INTEGER NOT NULL DEFAULT 0,
            started_by BIGINT,
            ended_by BIGINT,
            deleted INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS mod_appeals (
            id SERIAL PRIMARY KEY,
            case_id INTEGER NOT NULL REFERENCES mod_cases(id) ON DELETE CASCADE,
            user_id BIGINT NOT NULL,
            status TEXT DEFAULT 'pending',
            events TEXT NOT NULL,
            why_reduced TEXT NOT NULL,
            rules_ack TEXT NOT NULL,
            learned_prevent TEXT NOT NULL,
            additional TEXT,
            reviewed_by BIGINT,
            message_id BIGINT,
            channel_id BIGINT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS applications (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            status TEXT DEFAULT 'pending',
            reviewer_id BIGINT,
            reviewed_at TIMESTAMPTZ,
            review_reason TEXT,
            channel_id BIGINT,
            message_id BIGINT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS application_answers (
            id SERIAL PRIMARY KEY,
            application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
            question TEXT NOT NULL,
            answer TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_shift_sessions_guild_user ON shift_sessions(guild_id, user_id, deleted)",
        "CREATE INDEX IF NOT EXISTS idx_shift_sessions_active ON shift_sessions(guild_id, ended_at)",
        "CREATE INDEX IF NOT EXISTS idx_mod_cases_user ON mod_cases(user_id, active)",
        "CREATE INDEX IF NOT EXISTS idx_mod_appeals_case ON mod_appeals(case_id)",
        "CREATE INDEX IF NOT EXISTS idx_vehicles_user_id ON vehicles(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_vehicles_plate ON vehicles(plate)",
        "CREATE INDEX IF NOT EXISTS idx_trailers_user_id ON trailers(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_citation_tickets_user ON citation_tickets(user_id, payment_status)",
        "CREATE INDEX IF NOT EXISTS idx_warrants_user_active ON warrants(user_id, active)",
        "CREATE INDEX IF NOT EXISTS idx_economy_tx_user ON economy_transactions(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_support_tickets_user ON support_tickets(user_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_applications_user_pending ON applications(user_id) WHERE status = 'pending'",
        "CREATE INDEX IF NOT EXISTS idx_application_answers_app ON application_answers(application_id)",
        "CREATE INDEX IF NOT EXISTS idx_audit_logs_type ON audit_logs(action_type)",
    ]
    async with pool.acquire() as conn:
        for stmt in statements:
            await conn.execute(stmt)
        await _migrate_legacy(conn)


async def _add_column_if_missing(
    conn: asyncpg.Connection, table: str, column: str, definition: str
) -> None:
    try:
        await conn.execute(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {definition}"
        )
    except asyncpg.PostgresError as exc:
        log.warning("Could not add column %s.%s: %s", table, column, exc)


async def _migrate_legacy(conn: asyncpg.Connection) -> None:
    """Add missing columns and migrate old table names."""
    column_migrations = [
        ("vehicles", "status", "TEXT DEFAULT 'active'"),
        ("trailers", "status", "TEXT DEFAULT 'active'"),
        ("warrants", "status", "TEXT DEFAULT 'active'"),
        ("warrants", "cleared_at", "TIMESTAMPTZ"),
        ("citation_tickets", "payment_status", "TEXT DEFAULT 'unpaid'"),
        ("citation_tickets", "paid_at", "TIMESTAMPTZ"),
        ("users", "registration_suspended", "INTEGER DEFAULT 0"),
        ("users", "license_suspended", "INTEGER DEFAULT 0"),
        ("users", "license_expires_at", "TIMESTAMPTZ"),
        ("users", "insurance_expires_at", "TIMESTAMPTZ"),
        ("users", "verified_at", "TIMESTAMPTZ"),
        ("vehicles", "expires_at", "TIMESTAMPTZ"),
        ("shop_items", "item_key", "TEXT"),
        ("economy_accounts", "frozen", "INTEGER DEFAULT 0"),
        ("economy_accounts", "daily_last", "TIMESTAMPTZ"),
        ("economy_accounts", "work_last", "TIMESTAMPTZ"),
        ("blackjack_games", "doubled", "INTEGER DEFAULT 0"),
        ("blackjack_games", "updated_at", "TIMESTAMPTZ DEFAULT NOW()"),
        ("support_tickets", "frozen", "INTEGER DEFAULT 0"),
        ("guild_settings", "permissions_json", "TEXT"),
    ]
    for table, column, definition in column_migrations:
        table_exists = await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = $1
            )
            """,
            table,
        )
        if table_exists:
            await _add_column_if_missing(conn, table, column, definition)

    log.info("Database column migrations complete")

    has_bot_state = await conn.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'bot_state'
        )
        """
    )
    if has_bot_state:
        current = await conn.fetchval(
            "SELECT value FROM bot_state WHERE key = 'schema_version'"
        )
        if current and int(current) >= _SCHEMA_VERSION:
            return

    shop_exists = await conn.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'shop_items'
        )
        """
    )
    if shop_exists:
        await conn.execute(
            """
            DO $$ BEGIN
                ALTER TABLE shop_items ADD CONSTRAINT shop_items_item_key_key UNIQUE (item_key);
            EXCEPTION
                WHEN duplicate_object THEN NULL;
                WHEN duplicate_table THEN NULL;
            END $$
            """
        )

    users_exists = await conn.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'users'
        )
        """
    )
    if users_exists:
        await conn.execute(
            """
            UPDATE users
            SET license_suspended = 1
            WHERE registration_suspended = 1 AND license_suspended = 0
            """
        )
        await conn.execute(
            """
            UPDATE vehicles v
            SET status = 'suspended'
            FROM users u
            WHERE v.user_id = u.user_id
              AND u.registration_suspended = 1
              AND v.status = 'active'
            """
        )
        await conn.execute(
            """
            UPDATE trailers t
            SET status = 'suspended'
            FROM users u
            WHERE t.user_id = u.user_id
              AND u.registration_suspended = 1
              AND t.status = 'active'
            """
        )

    has_old_tickets = await conn.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_name = 'tickets' AND table_schema = 'public'
        )
        """
    )
    has_support = await conn.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_name = 'support_tickets' AND table_schema = 'public'
        )
        """
    )
    if has_old_tickets and has_support:
        await conn.execute(
            """
            INSERT INTO support_tickets (channel_id, user_id, category, status, created_at)
            SELECT channel_id, user_id, category, status, created_at FROM tickets
            ON CONFLICT (channel_id) DO NOTHING
            """
        )

    await conn.execute(
        """
        INSERT INTO bot_state (key, value) VALUES ('schema_version', $1)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """,
        str(_SCHEMA_VERSION),
    )
