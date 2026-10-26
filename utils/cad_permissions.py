"""Pure Discord role/permission mapping for CAD. No discord.py."""

from __future__ import annotations

PERMISSIONS = (
    "civilian.self",
    "records.read",
    "records.write",
    "cad.supervisor",
    "cad.admin",
    "mdt.police",
    "mdt.fire",
    "mdt.ems",
    "mdt.dot",
    "dispatch.operate",
    "calls.manage",
    "units.self",
    "units.manage",
    "citations.manage",
    "warrants.manage",
    "bolos.manage",
    "shifts.read",
    "applications.review",
)

ELEVATED = frozenset(
    {
        "mdt.police",
        "mdt.fire",
        "mdt.ems",
        "mdt.dot",
        "dispatch.operate",
        "cad.admin",
        "calls.manage",
        "warrants.manage",
        "citations.manage",
    }
)


def _has_role(member_roles: set[int], mapped: dict[str, list[int]], key: str) -> bool:
    return bool(member_roles & set(mapped.get(key) or []))


def compute_permissions(
    *,
    discord_user_id: int,
    in_main_guild: bool,
    owners: list[int],
    main_roles: set[int],
    fd_roles: set[int],
    pd_roles: set[int],
    main_perms: dict[str, list[int]],
    fd_perms: dict[str, list[int]],
    pd_perms: dict[str, list[int]],
    department_roles: dict[str, int],
    reviewer_role_ids: list[int],
    overrides: dict[str, bool] | None = None,
) -> set[str]:
    perms: set[str] = set()
    if not in_main_guild:
        return perms

    perms.add("civilian.self")

    dept = {k: int(v) for k, v in department_roles.items() if v}
    if discord_user_id in set(owners):
        return set(PERMISSIONS)

    if _has_role(main_roles, main_perms, "admin"):
        return set(PERMISSIONS)

    staffish = (
        _has_role(main_roles, main_perms, "staff")
        or _has_role(main_roles, main_perms, "moderate")
        or _has_role(main_roles, main_perms, "admin")
    )
    if staffish:
        perms.update({"records.read", "cad.supervisor", "shifts.read"})

    leo = _has_role(main_roles, main_perms, "leo") or any(
        dept.get(k) in main_roles for k in ("police1", "police2", "state_police", "rural_pd")
    )
    if leo:
        perms.update(
            {
                "mdt.police",
                "records.write",
                "warrants.manage",
                "citations.manage",
                "bolos.manage",
                "units.self",
            }
        )

    if any(dept.get(k) in main_roles for k in ("fire1", "fire2", "fire3")):
        perms.update({"mdt.fire", "units.self"})
    if dept.get("ems") in main_roles:
        perms.update({"mdt.ems", "units.self"})
    if dept.get("dot") in main_roles:
        perms.update({"mdt.dot", "units.self"})
    if dept.get("dispatch") in main_roles:
        perms.update({"dispatch.operate", "calls.manage", "units.manage", "records.read"})

    if _has_role(fd_roles, fd_perms, "shift") or _has_role(pd_roles, pd_perms, "shift"):
        perms.add("shifts.read")
    if _has_role(fd_roles, fd_perms, "shift_admin") or _has_role(pd_roles, pd_perms, "shift_admin"):
        perms.add("shifts.read")

    if main_roles & set(reviewer_role_ids):
        perms.add("applications.review")

    for key, granted in (overrides or {}).items():
        if granted:
            perms.add(key)
        else:
            perms.discard(key)
    return perms


def needs_dm_verify(permissions: set[str]) -> bool:
    return bool(permissions & ELEVATED)
