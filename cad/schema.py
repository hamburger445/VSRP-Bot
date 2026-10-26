"""CAD table DDL. Idempotent. Called from bot init_tables and CAD startup."""

from __future__ import annotations

import logging

import asyncpg

log = logging.getLogger("vsrp_bot.cad.schema")

_TABLES = [
    """
    CREATE TABLE IF NOT EXISTS cad_users (
        discord_user_id BIGINT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
        username TEXT,
        global_name TEXT,
        avatar_hash TEXT,
        dm_verified_at TIMESTAMPTZ,
        totp_enabled BOOLEAN DEFAULT FALSE,
        totp_secret_enc TEXT,
        last_role_sync_at TIMESTAMPTZ,
        last_login_at TIMESTAMPTZ,
        main_roles TEXT DEFAULT '[]',
        fd_roles TEXT DEFAULT '[]',
        pd_roles TEXT DEFAULT '[]',
        in_main_guild BOOLEAN DEFAULT FALSE,
        status TEXT DEFAULT 'active',
        created_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cad_oauth_tokens (
        discord_user_id BIGINT PRIMARY KEY REFERENCES cad_users(discord_user_id) ON DELETE CASCADE,
        access_token_enc TEXT NOT NULL,
        refresh_token_enc TEXT,
        access_expires_at TIMESTAMPTZ,
        scopes TEXT,
        updated_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cad_sessions (
        id UUID PRIMARY KEY,
        token_hash TEXT UNIQUE NOT NULL,
        discord_user_id BIGINT NOT NULL REFERENCES cad_users(discord_user_id) ON DELETE CASCADE,
        expires_at TIMESTAMPTZ NOT NULL,
        last_seen_at TIMESTAMPTZ DEFAULT NOW(),
        revoked_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cad_dm_codes (
        id SERIAL PRIMARY KEY,
        discord_user_id BIGINT NOT NULL REFERENCES cad_users(discord_user_id) ON DELETE CASCADE,
        code_hash TEXT NOT NULL,
        expires_at TIMESTAMPTZ NOT NULL,
        consumed_at TIMESTAMPTZ,
        attempts INTEGER DEFAULT 0,
        created_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cad_permission_overrides (
        discord_user_id BIGINT NOT NULL REFERENCES cad_users(discord_user_id) ON DELETE CASCADE,
        permission TEXT NOT NULL,
        granted BOOLEAN NOT NULL DEFAULT TRUE,
        reason TEXT,
        granted_by BIGINT,
        expires_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        PRIMARY KEY (discord_user_id, permission)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cad_characters (
        id SERIAL PRIMARY KEY,
        discord_user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        display_name TEXT NOT NULL,
        dob TEXT,
        notes TEXT,
        is_primary BOOLEAN DEFAULT FALSE,
        created_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cad_units (
        id SERIAL PRIMARY KEY,
        callsign TEXT UNIQUE NOT NULL,
        department TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'available',
        discord_user_id BIGINT REFERENCES users(user_id) ON DELETE SET NULL,
        shift_session_id INTEGER,
        notes TEXT,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cad_unit_positions (
        unit_id INTEGER PRIMARY KEY REFERENCES cad_units(id) ON DELETE CASCADE,
        lat DOUBLE PRECISION NOT NULL,
        lng DOUBLE PRECISION NOT NULL,
        label TEXT,
        source TEXT DEFAULT 'manual',
        updated_by BIGINT,
        updated_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cad_calls (
        id SERIAL PRIMARY KEY,
        call_number TEXT UNIQUE NOT NULL,
        discipline TEXT NOT NULL,
        priority INTEGER NOT NULL DEFAULT 3,
        status TEXT NOT NULL DEFAULT 'pending',
        nature TEXT,
        location_text TEXT,
        lat DOUBLE PRECISION,
        lng DOUBLE PRECISION,
        details TEXT,
        created_by BIGINT,
        primary_unit_id INTEGER REFERENCES cad_units(id) ON DELETE SET NULL,
        opened_at TIMESTAMPTZ DEFAULT NOW(),
        closed_at TIMESTAMPTZ
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cad_call_units (
        call_id INTEGER NOT NULL REFERENCES cad_calls(id) ON DELETE CASCADE,
        unit_id INTEGER NOT NULL REFERENCES cad_units(id) ON DELETE CASCADE,
        assigned_at TIMESTAMPTZ DEFAULT NOW(),
        cleared_at TIMESTAMPTZ,
        PRIMARY KEY (call_id, unit_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cad_call_notes (
        id SERIAL PRIMARY KEY,
        call_id INTEGER NOT NULL REFERENCES cad_calls(id) ON DELETE CASCADE,
        author_id BIGINT,
        body TEXT NOT NULL,
        created_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cad_incidents (
        id SERIAL PRIMARY KEY,
        call_id INTEGER REFERENCES cad_calls(id) ON DELETE SET NULL,
        title TEXT,
        narrative TEXT,
        status TEXT DEFAULT 'open',
        created_by BIGINT,
        discord_subject_id BIGINT,
        created_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cad_incident_links (
        incident_id INTEGER NOT NULL REFERENCES cad_incidents(id) ON DELETE CASCADE,
        record_type TEXT NOT NULL,
        record_id INTEGER NOT NULL,
        UNIQUE (incident_id, record_type, record_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cad_bolos (
        id SERIAL PRIMARY KEY,
        kind TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        plate TEXT,
        discord_user_id BIGINT,
        description TEXT NOT NULL,
        danger TEXT,
        issued_by BIGINT,
        expires_at TIMESTAMPTZ,
        cleared_at TIMESTAMPTZ,
        cleared_by BIGINT,
        created_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cad_pcrs (
        id SERIAL PRIMARY KEY,
        call_id INTEGER REFERENCES cad_calls(id) ON DELETE SET NULL,
        patient_name TEXT,
        chief_complaint TEXT,
        disposition TEXT,
        author_id BIGINT,
        created_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cad_road_closures (
        id SERIAL PRIMARY KEY,
        location_text TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'open',
        lat DOUBLE PRECISION,
        lng DOUBLE PRECISION,
        notes TEXT,
        opened_by BIGINT,
        cleared_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cad_charges (
        id SERIAL PRIMARY KEY,
        code TEXT UNIQUE NOT NULL,
        description TEXT NOT NULL,
        discipline TEXT,
        active BOOLEAN DEFAULT TRUE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cad_audit (
        id SERIAL PRIMARY KEY,
        actor_id BIGINT,
        action TEXT NOT NULL,
        entity_type TEXT,
        entity_id TEXT,
        details_json TEXT,
        created_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cad_outbox (
        id SERIAL PRIMARY KEY,
        kind TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        available_at TIMESTAMPTZ DEFAULT NOW(),
        locked_at TIMESTAMPTZ,
        delivered_at TIMESTAMPTZ,
        attempts INTEGER DEFAULT 0,
        last_error TEXT,
        created_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_cad_sessions_user ON cad_sessions(discord_user_id) WHERE revoked_at IS NULL",
    "CREATE INDEX IF NOT EXISTS idx_cad_calls_status ON cad_calls(status, opened_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_cad_units_dept ON cad_units(department, status)",
    "CREATE INDEX IF NOT EXISTS idx_cad_bolos_status ON cad_bolos(status)",
    "CREATE INDEX IF NOT EXISTS idx_cad_outbox_pending ON cad_outbox(available_at) WHERE delivered_at IS NULL",
    "CREATE INDEX IF NOT EXISTS idx_cad_characters_user ON cad_characters(discord_user_id)",
]

_NOTIFY_FN = """
CREATE OR REPLACE FUNCTION cad_notify() RETURNS trigger AS $$
DECLARE
  rec_id TEXT;
BEGIN
  IF TG_OP = 'DELETE' THEN
    rec_id := COALESCE(OLD.id::text, OLD.discord_user_id::text, OLD.unit_id::text);
  ELSE
    rec_id := COALESCE(NEW.id::text, NEW.discord_user_id::text, NEW.unit_id::text);
  END IF;
  PERFORM pg_notify('cad_events', json_build_object('table', TG_TABLE_NAME, 'op', TG_OP, 'id', rec_id)::text);
  RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;
"""

_TRIGGER_TABLES = (
    "cad_calls",
    "cad_units",
    "cad_unit_positions",
    "cad_bolos",
    "cad_call_notes",
    "citation_tickets",
    "warrants",
    "shift_sessions",
)


async def init_cad_schema(conn: asyncpg.Connection) -> None:
    for stmt in _TABLES:
        await conn.execute(stmt)
    await conn.execute(_NOTIFY_FN)
    for table in _TRIGGER_TABLES:
        exists = await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = $1
            )
            """,
            table,
        )
        if not exists:
            continue
        await conn.execute(f"DROP TRIGGER IF EXISTS cad_notify_{table} ON {table}")
        trigger_sql = f"""
            CREATE TRIGGER cad_notify_{table}
            AFTER INSERT OR UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE PROCEDURE cad_notify()
            """
        try:
            await conn.execute(trigger_sql)
        except asyncpg.PostgresError:
            await conn.execute(
                trigger_sql.replace("EXECUTE PROCEDURE", "EXECUTE FUNCTION")
            )
    log.info("CAD schema ready")
