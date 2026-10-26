"""Map main-guild Discord department roles to CAD workspaces."""

from __future__ import annotations

DEFAULT_NAMES = {
    "police1": "Wythe County Sheriff's Office",
    "police2": "Wytheville Police Department",
    "state_police": "Department of State Police",
    "rural_pd": "Rural Retreat Police Department",
    "fire1": "Wytheville Fire & Rescue",
    "fire2": "Rural Retreat Volunteer Fire Department",
    "fire3": "Max Meadows Volunteer Fire Department",
    "ems": "Emergency Medical Services",
    "dot": "Department of Transportation",
    "dispatch": "Dispatch / Communications",
}

DEFAULT_AGENCIES = {
    "police1": "Wythe County Sheriffs Office",
    "police2": "Wytheville Police Department",
    "state_police": "Department of State Police",
    "rural_pd": "Rural Retreat Police Department",
}

KIND = {
    "police1": "sheriff",
    "police2": "police",
    "state_police": "state_police",
    "rural_pd": "police",
    "fire1": "fire",
    "fire2": "fire",
    "fire3": "fire",
    "ems": "ems",
    "dot": "dot",
    "dispatch": "dispatch",
}

PERM_FOR_KIND = {
    "sheriff": "mdt.police",
    "police": "mdt.police",
    "state_police": "mdt.police",
    "fire": "mdt.fire",
    "ems": "mdt.ems",
    "dot": "mdt.dot",
    "dispatch": "dispatch.operate",
}


def memberships(
    *,
    main_roles: set[int],
    department_roles: dict[str, int],
    department_names: dict[str, str] | None = None,
    is_admin: bool = False,
) -> list[dict]:
    names = {**DEFAULT_NAMES, **(department_names or {})}
    out: list[dict] = []
    for key, role_id in department_roles.items():
        rid = int(role_id or 0)
        if not is_admin and (not rid or rid not in main_roles):
            continue
        kind = KIND.get(key, key)
        out.append(
            {
                "key": key,
                "name": names.get(key) or key,
                "agency": DEFAULT_AGENCIES.get(key) or names.get(key) or key,
                "kind": kind,
                "permission": PERM_FOR_KIND.get(kind, "civilian.self"),
                "role_id": rid,
            }
        )
    return out
