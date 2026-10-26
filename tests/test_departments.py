from utils.cad_departments import memberships
from utils.cad_permissions import compute_permissions
from utils.leo_case import format_case_number
from cad.github_sql import load_state, run


def test_multi_department_memberships():
    depts = memberships(
        main_roles={10, 20, 30},
        department_roles={"police2": 10, "fire1": 20, "dispatch": 30, "ems": 99},
        is_admin=False,
    )
    keys = {d["key"] for d in depts}
    assert keys == {"police2", "fire1", "dispatch"}
    assert all(d["name"] for d in depts)


def test_admin_sees_configured_departments():
    depts = memberships(
        main_roles=set(),
        department_roles={"police1": 1, "ems": 2},
        is_admin=True,
    )
    assert {d["key"] for d in depts} == {"police1", "ems"}


def test_state_police_is_leo():
    p = compute_permissions(
        discord_user_id=1,
        in_main_guild=True,
        owners=[],
        main_roles={77},
        fd_roles=set(),
        pd_roles=set(),
        main_perms={},
        fd_perms={},
        pd_perms={},
        department_roles={"state_police": 77},
        reviewer_role_ids=[],
        overrides=None,
    )
    assert "mdt.police" in p
    assert "warrants.manage" in p
    assert "citations.manage" in p


def test_format_case_number_leading_zeros():
    assert format_case_number(0) == "0000"
    assert format_case_number("12") == "0012"


def test_case_sequence_starts_at_0000():
    state = load_state({})
    insert = "INSERT INTO bot_state (key, value) VALUES ($1, '-1') ON CONFLICT (key) DO NOTHING"
    update = "UPDATE bot_state SET value = (value::int + 1)::text WHERE key = $1 RETURNING value"
    run(state, insert, ("leo_case_sequence",), "execute")
    first = str(run(state, update, ("leo_case_sequence",), "fetchrow")["value"])
    run(state, insert, ("leo_case_sequence",), "execute")
    second = str(run(state, update, ("leo_case_sequence",), "fetchrow")["value"])
    assert first == "0"
    assert second == "1"
    assert format_case_number(first) == "0000"
    assert format_case_number(second) == "0001"


def test_case_sequence_continues_existing():
    state = load_state({})
    run(state, "INSERT INTO bot_state (key, value) VALUES ($1, '12')", ("leo_case_sequence",), "execute")
    run(state, "INSERT INTO bot_state (key, value) VALUES ($1, '-1') ON CONFLICT (key) DO NOTHING", ("leo_case_sequence",), "execute")
    nxt = run(state, "UPDATE bot_state SET value = (value::int + 1)::text WHERE key = $1 RETURNING value", ("leo_case_sequence",), "fetchrow")
    assert nxt["value"] == "13"
    assert format_case_number(nxt["value"]) == "0013"
