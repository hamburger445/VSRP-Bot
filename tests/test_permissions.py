from utils.cad_permissions import compute_permissions


def _base(**kwargs):
    args = dict(
        discord_user_id=1,
        in_main_guild=True,
        owners=[],
        main_roles=set(),
        fd_roles=set(),
        pd_roles=set(),
        main_perms={},
        fd_perms={},
        pd_perms={},
        department_roles={"police2": 10, "fire1": 20, "dispatch": 30, "ems": 40, "dot": 50},
        reviewer_role_ids=[99],
        overrides=None,
    )
    args.update(kwargs)
    return compute_permissions(**args)


def test_civilian_only():
    p = _base()
    assert p == {"civilian.self"}


def test_not_in_guild():
    assert _base(in_main_guild=False) == set()


def test_police_role():
    p = _base(main_roles={10})
    assert "mdt.police" in p
    assert "citations.manage" in p
    assert "cad.admin" not in p


def test_staff_is_supervisor_not_police():
    p = _base(main_perms={"staff": [5]}, main_roles={5})
    assert "cad.supervisor" in p
    assert "mdt.police" not in p


def test_owner_is_admin():
    p = _base(owners=[1])
    assert "cad.admin" in p
    assert "mdt.police" in p


def test_dispatch_role():
    p = _base(main_roles={30})
    assert "dispatch.operate" in p


def test_reviewer():
    p = _base(main_roles={99})
    assert "applications.review" in p
