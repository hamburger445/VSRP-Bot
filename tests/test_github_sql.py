from datetime import timedelta

from utils.github_sql import dump_state, load_state, run, utcnow


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


def test_count_alias():
    state = load_state({})
    run(state, "INSERT INTO citation_tickets (user_id, payment_status) VALUES ($1, 'unpaid')", (1,), "execute")
    row = run(
        state,
        "SELECT COUNT(*) AS cnt FROM citation_tickets WHERE user_id = $1 AND payment_status = 'unpaid'",
        (1,),
        "fetchrow",
    )
    assert row["cnt"] == 1


def test_roundtrip_datetime():
    state = load_state({})
    now = utcnow()
    run(state, "INSERT INTO cad_sessions (id, expires_at) VALUES ($1,$2)", ("a", now), "execute")
    raw = dump_state(state)
    again = load_state(raw)
    row = run(again, "SELECT * FROM cad_sessions WHERE id = $1", ("a",), "fetchrow")
    assert row["expires_at"] == now or row["expires_at"].isoformat() == now.isoformat()
