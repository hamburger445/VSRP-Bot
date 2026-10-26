from datetime import timedelta

from cad.github_sql import dump_state, load_state, run, utcnow


def test_select_one():
    assert run({}, "SELECT 1", mode="fetchval") == 1


def test_insert_join_session():
    state = load_state({})
    run(
        state,
        """
        INSERT INTO cad_users (discord_user_id, username, status)
        VALUES ($1,$2,'active')
        """,
        (99, "alice"),
        "execute",
    )
    sid = "session-1"
    exp = utcnow() + timedelta(hours=12)
    run(
        state,
        "INSERT INTO cad_sessions (id, token_hash, discord_user_id, expires_at) VALUES ($1,$2,$3,$4)",
        (sid, "hash", 99, exp),
        "execute",
    )
    row = run(
        state,
        """
        SELECT s.id, s.discord_user_id, u.username, u.status
        FROM cad_sessions s
        JOIN cad_users u ON u.discord_user_id = s.discord_user_id
        WHERE s.token_hash = $1 AND s.revoked_at IS NULL
        """,
        ("hash",),
        "fetchrow",
    )
    assert row["id"] == sid
    assert row["username"] == "alice"


def test_on_conflict_increment():
    state = load_state({})
    run(
        state,
        "INSERT INTO bot_state (key, value) VALUES ('cad_call_sequence', '1') ON CONFLICT (key) DO UPDATE SET value = (COALESCE(NULLIF(bot_state.value, '')::int, 0) + 1)::text RETURNING value",
        (),
        "fetchrow",
    )
    row = run(
        state,
        "INSERT INTO bot_state (key, value) VALUES ('cad_call_sequence', '1') ON CONFLICT (key) DO UPDATE SET value = (COALESCE(NULLIF(bot_state.value, '')::int, 0) + 1)::text RETURNING value",
        (),
        "fetchrow",
    )
    assert row["value"] == "2"


def test_login_then_lookup_session():
    from cad.security import hash_token

    state = load_state({})
    uid = 1513602696379105362
    raw = "test-session-token"
    run(state, "INSERT INTO users (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING", (uid,), "execute")
    run(
        state,
        """
        INSERT INTO cad_users (
            discord_user_id, username, global_name, avatar_hash, last_login_at,
            last_role_sync_at, main_roles, fd_roles, pd_roles, in_main_guild, status
        )
        VALUES ($1,$2,$3,$4,NOW(),NOW(),$5,$6,$7,TRUE,'active')
        ON CONFLICT (discord_user_id) DO UPDATE SET
            username = EXCLUDED.username,
            global_name = EXCLUDED.global_name,
            avatar_hash = EXCLUDED.avatar_hash,
            last_login_at = NOW(),
            last_role_sync_at = NOW(),
            main_roles = EXCLUDED.main_roles,
            fd_roles = EXCLUDED.fd_roles,
            pd_roles = EXCLUDED.pd_roles,
            in_main_guild = TRUE
        """,
        (uid, "alice", "Alice", "abc", "[]", "[]", "[]"),
        "execute",
    )
    run(
        state,
        "INSERT INTO cad_sessions (id, token_hash, discord_user_id, expires_at) VALUES ($1,$2,$3,$4)",
        ("sid-1", hash_token(raw), uid, utcnow() + timedelta(hours=12)),
        "execute",
    )
    sess = run(state, "SELECT * FROM cad_sessions WHERE token_hash = $1", (hash_token(raw),), "fetchrow")
    assert sess is not None
    user = run(state, "SELECT * FROM cad_users WHERE discord_user_id = $1", (uid,), "fetchrow")
    assert user["username"] == "alice"


def test_roundtrip_datetime():
    state = load_state({})
    now = utcnow()
    run(state, "INSERT INTO cad_sessions (id, expires_at) VALUES ($1,$2)", ("a", now), "execute")
    raw = dump_state(state)
    again = load_state(raw)
    row = run(again, "SELECT * FROM cad_sessions WHERE id = $1", ("a",), "fetchrow")
    assert row["expires_at"] == now or row["expires_at"].isoformat() == now.isoformat()
