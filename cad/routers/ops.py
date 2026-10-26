"""Dashboard, people, vehicles, citations, warrants, BOLOs, civilian, shifts, applications."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from cad.db import audit, connection, enqueue_outbox, row_to_dict, rows_to_dicts
from cad.deps import current_session, require_perm
from utils.leo_forms import CITATION_AGENCIES, COURT_TYPES, citation_summary, warrant_form_to_reason
from utils.leo_records import (
    clear_warrant,
    create_citation,
    create_warrant,
    pay_citation,
    serve_warrant,
)

router = APIRouter(prefix="/api", tags=["ops"])


def _json_safe(row: dict | None) -> dict | None:
    if not row:
        return None
    out = {}
    for k, v in row.items():
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        else:
            out[k] = v
    for key in ("form_json", "serve_form_json"):
        raw = out.get(key)
        if isinstance(raw, str):
            try:
                out[key.replace("_json", "")] = json.loads(raw)
            except json.JSONDecodeError:
                out[key.replace("_json", "")] = {}
        elif isinstance(raw, dict):
            out[key.replace("_json", "")] = raw
    return out


@router.get("/dashboard")
async def dashboard(user: dict = Depends(current_session)):
    uid = int(user["discord_user_id"])
    perms = set(user.get("permissions") or [])
    async with connection() as conn:
        open_calls = await conn.fetchval(
            "SELECT COUNT(*) FROM cad_calls WHERE status NOT IN ('closed','cancelled')"
        )
        pending_calls = await conn.fetchval(
            "SELECT COUNT(*) FROM cad_calls WHERE status = 'pending'"
        )
        units = await conn.fetch(
            "SELECT department, status, COUNT(*)::int AS n FROM cad_units GROUP BY department, status"
        )
        bolos = await conn.fetchval("SELECT COUNT(*) FROM cad_bolos WHERE status = 'active'")
        unpaid = await conn.fetchval(
            "SELECT COUNT(*) FROM citation_tickets WHERE user_id = $1 AND payment_status = 'unpaid'",
            uid,
        )
        warrants = await conn.fetchval(
            "SELECT COUNT(*) FROM warrants WHERE user_id = $1 AND active = 1",
            uid,
        )
        recent = await conn.fetch(
            """
            SELECT id, call_number, discipline, priority, status, nature, location_text, opened_at
            FROM cad_calls ORDER BY opened_at DESC LIMIT 8
            """
        )
        my_unit = await conn.fetchrow(
            "SELECT * FROM cad_units WHERE discord_user_id = $1 ORDER BY updated_at DESC LIMIT 1",
            uid,
        )
        shift = await conn.fetchrow(
            """
            SELECT id, guild_id, status, started_at FROM shift_sessions
            WHERE user_id = $1 AND ended_at IS NULL AND deleted = 0
            ORDER BY started_at DESC LIMIT 1
            """,
            uid,
        )
    return {
        "open_calls": int(open_calls or 0),
        "pending_calls": int(pending_calls or 0),
        "active_bolos": int(bolos or 0),
        "my_unpaid_citations": int(unpaid or 0),
        "my_active_warrants": int(warrants or 0),
        "units": rows_to_dicts(units),
        "recent_calls": [_json_safe(dict(r)) for r in recent],
        "my_unit": _json_safe(row_to_dict(my_unit)),
        "my_shift": _json_safe(row_to_dict(shift)),
        "permissions": sorted(perms),
    }


@router.get("/people")
async def search_people(
    q: str = Query(min_length=1),
    user: dict = Depends(require_perm("records.read", "mdt.police", "cad.supervisor")),
):
    like = f"%{q}%"
    async with connection() as conn:
        if q.isdigit():
            rows = await conn.fetch(
                """
                SELECT u.user_id, c.display_name, u.license_suspended, u.registration_suspended
                FROM users u
                LEFT JOIN cad_characters c ON c.discord_user_id = u.user_id AND c.is_primary = TRUE
                WHERE u.user_id = $1
                LIMIT 25
                """,
                int(q),
            )
        else:
            rows = await conn.fetch(
                """
                SELECT u.user_id, c.display_name, u.license_suspended, u.registration_suspended
                FROM cad_characters c
                JOIN users u ON u.user_id = c.discord_user_id
                WHERE c.display_name ILIKE $1
                LIMIT 25
                """,
                like,
            )
    return {"results": [_json_safe(dict(r)) for r in rows]}


@router.get("/people/{discord_user_id}")
async def person_file(
    discord_user_id: int,
    user: dict = Depends(require_perm("records.read", "mdt.police", "cad.supervisor", "civilian.self")),
):
    if "civilian.self" in user["permissions"] and discord_user_id != int(user["discord_user_id"]):
        if not set(user["permissions"]) & {"records.read", "mdt.police", "cad.supervisor", "cad.admin"}:
            raise HTTPException(403, "Permission denied")
    async with connection() as conn:
        profile = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", discord_user_id)
        if not profile:
            raise HTTPException(404, "Person not found")
        chars = await conn.fetch(
            "SELECT * FROM cad_characters WHERE discord_user_id = $1 ORDER BY is_primary DESC, id",
            discord_user_id,
        )
        vehicles = await conn.fetch(
            "SELECT id, make, model, year, color, plate, status, expires_at FROM vehicles WHERE user_id = $1",
            discord_user_id,
        )
        trailers = await conn.fetch(
            "SELECT id, trailer_type, plate, status FROM trailers WHERE user_id = $1",
            discord_user_id,
        )
        citations = await conn.fetch(
            """
            SELECT id, case_number, violation, fine, payment_status, created_at, issued_by
            FROM citation_tickets WHERE user_id = $1 ORDER BY created_at DESC LIMIT 50
            """,
            discord_user_id,
        )
        warrants = await conn.fetch(
            """
            SELECT id, case_number, reason, active, status, created_at, issued_by
            FROM warrants WHERE user_id = $1 ORDER BY created_at DESC LIMIT 50
            """,
            discord_user_id,
        )
    return {
        "user": _json_safe(dict(profile)),
        "characters": [_json_safe(dict(r)) for r in chars],
        "vehicles": [_json_safe(dict(r)) for r in vehicles],
        "trailers": [_json_safe(dict(r)) for r in trailers],
        "citations": [_json_safe(dict(r)) for r in citations],
        "warrants": [_json_safe(dict(r)) for r in warrants],
    }


class CharacterBody(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)
    dob: str | None = None
    notes: str | None = None
    is_primary: bool = False


@router.post("/people/{discord_user_id}/characters")
async def add_character(
    discord_user_id: int,
    body: CharacterBody,
    user: dict = Depends(require_perm("civilian.self", "records.write")),
):
    if discord_user_id != int(user["discord_user_id"]) and "records.write" not in user["permissions"] and "cad.admin" not in user["permissions"]:
        raise HTTPException(403, "Permission denied")
    async with connection() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO cad_characters (discord_user_id, display_name, dob, notes, is_primary)
            VALUES ($1,$2,$3,$4,$5) RETURNING *
            """,
            discord_user_id,
            body.display_name,
            body.dob,
            body.notes,
            body.is_primary,
        )
        await audit(conn, int(user["discord_user_id"]), "character_create", "cad_characters", row["id"], {})
    return _json_safe(dict(row))


@router.get("/vehicles")
async def search_vehicles(
    plate: str = Query(min_length=1),
    user: dict = Depends(require_perm("records.read", "mdt.police", "cad.supervisor")),
):
    async with connection() as conn:
        rows = await conn.fetch(
            """
            SELECT id, user_id, make, model, year, color, plate, status, expires_at
            FROM vehicles WHERE plate ILIKE $1 LIMIT 25
            """,
            f"%{plate.strip()}%",
        )
    return {"results": [_json_safe(dict(r)) for r in rows]}


@router.get("/catalog")
async def catalog(user: dict = Depends(current_session)):
    charges = []
    async with connection() as conn:
        try:
            rows = await conn.fetch("SELECT code, description, discipline FROM cad_charges ORDER BY code")
            charges = [_json_safe(dict(r)) for r in rows]
        except Exception:
            charges = []
    if not charges:
        charges = [
            {"code": "46.2-862", "description": "Reckless driving; exceeding speed limit", "discipline": "police"},
            {"code": "46.2-870", "description": "Maximum speed limits", "discipline": "police"},
            {"code": "18.2-57", "description": "Assault and battery", "discipline": "police"},
        ]
    return {
        "agencies": list(CITATION_AGENCIES),
        "courts": list(COURT_TYPES),
        "counties": ["WYTHE"],
        "charges": charges,
        "departments": user.get("departments") or [],
    }


@router.get("/officer/profile")
async def officer_profile(user: dict = Depends(current_session)):
    unit = None
    async with connection() as conn:
        unit = await conn.fetchrow(
            "SELECT * FROM cad_units WHERE discord_user_id = $1 ORDER BY id DESC LIMIT 1",
            int(user["discord_user_id"]),
        )
    depts = user.get("departments") or []
    agency = depts[0]["agency"] if depts else ""
    name = user.get("global_name") or user.get("username") or ""
    unit_d = dict(unit) if unit else {}
    callsign = unit_d.get("callsign") or ""
    return {
        "discord_user_id": user["discord_user_id"],
        "name": name,
        "callsign": callsign,
        "agency": agency,
        "issued_by": " ".join(x for x in [name, callsign, agency] if x),
        "departments": depts,
        "unit": _json_safe(unit_d) if unit_d else None,
    }


class CitationBody(BaseModel):
    subject_id: int
    fine: int = Field(ge=0, le=100000)
    form: dict = Field(default_factory=dict)


@router.get("/citations")
async def list_citations(
    subject_id: int | None = None,
    user: dict = Depends(require_perm("citations.manage", "records.read", "civilian.self")),
):
    async with connection() as conn:
        if subject_id:
            rows = await conn.fetch(
                "SELECT * FROM citation_tickets WHERE user_id = $1 ORDER BY created_at DESC LIMIT 100",
                subject_id,
            )
        else:
            rows = await conn.fetch(
                "SELECT * FROM citation_tickets ORDER BY created_at DESC LIMIT 100"
            )
    return {"results": [_json_safe(dict(r)) for r in rows]}


@router.get("/citations/{ticket_id}")
async def get_citation(ticket_id: int, user: dict = Depends(require_perm("citations.manage", "records.read", "civilian.self"))):
    async with connection() as conn:
        row = await conn.fetchrow("SELECT * FROM citation_tickets WHERE id = $1", ticket_id)
    if not row:
        raise HTTPException(404, "Citation not found")
    return _json_safe(dict(row))


@router.post("/citations")
async def issue_citation(
    body: CitationBody,
    user: dict = Depends(require_perm("citations.manage")),
):
    violation = citation_summary(body.form) if body.form else body.form.get("charge_description") or "Citation"
    async with connection() as conn:
        async with conn.transaction():
            row = await create_citation(
                subject_id=body.subject_id,
                issued_by=int(user["discord_user_id"]),
                form_data=body.form,
                fine=body.fine,
                violation=violation,
                conn=conn,
            )
            await enqueue_outbox(
                conn,
                "citation_issued",
                {
                    "ticket_id": row["id"],
                    "user_id": body.subject_id,
                    "issued_by": int(user["discord_user_id"]),
                    "issued_by_name": user.get("global_name") or user.get("username"),
                    "violation": violation,
                    "fine": body.fine,
                    "case_number": row.get("case_number"),
                    "form": body.form,
                },
            )
            await audit(conn, int(user["discord_user_id"]), "citation_issue", "citation_tickets", row["id"], {"subject": body.subject_id})
    return _json_safe(row)


@router.post("/citations/{ticket_id}/pay")
async def pay_ticket(
    ticket_id: int,
    user: dict = Depends(require_perm("citations.manage", "civilian.self")),
):
    async with connection() as conn:
        existing = await conn.fetchrow("SELECT * FROM citation_tickets WHERE id = $1", ticket_id)
        if not existing:
            raise HTTPException(404, "Citation not found")
        if "citations.manage" not in user["permissions"] and "cad.admin" not in user["permissions"]:
            if int(existing["user_id"]) != int(user["discord_user_id"]):
                raise HTTPException(403, "Permission denied")
        async with conn.transaction():
            row = await pay_citation(ticket_id, conn)
            if not row:
                raise HTTPException(400, "Citation is already paid")
            await enqueue_outbox(
                conn,
                "citation_paid",
                {"ticket_id": ticket_id, "user_id": row["user_id"]},
            )
            await audit(conn, int(user["discord_user_id"]), "citation_pay", "citation_tickets", ticket_id, {})
    return _json_safe(row)


class WarrantBody(BaseModel):
    subject_id: int
    form: dict = Field(default_factory=dict)


@router.get("/warrants")
async def list_warrants(
    subject_id: int | None = None,
    q: str | None = None,
    status: str | None = None,
    user: dict = Depends(require_perm("warrants.manage", "records.read")),
):
    async with connection() as conn:
        if subject_id:
            rows = await conn.fetch(
                "SELECT * FROM warrants WHERE user_id = $1 ORDER BY created_at DESC LIMIT 200",
                subject_id,
            )
        else:
            rows = await conn.fetch("SELECT * FROM warrants ORDER BY created_at DESC LIMIT 200")
    results = [_json_safe(dict(r)) for r in rows]
    if status:
        want = status.lower()
        results = [r for r in results if str(r.get("status") or "").lower() == want]
    if q:
        needle = q.lower()
        results = [
            r
            for r in results
            if needle in str(r.get("case_number") or "").lower()
            or needle in str(r.get("reason") or "").lower()
            or needle in str((r.get("form") or {}).get("accused") or "").lower()
            or needle in str((r.get("form") or {}).get("warrant_type") or "").lower()
            or needle in str((r.get("form") or {}).get("jurisdiction") or "").lower()
        ]
    return {"results": results}


@router.get("/warrants/{warrant_id}")
async def get_warrant(warrant_id: int, user: dict = Depends(require_perm("warrants.manage", "records.read"))):
    async with connection() as conn:
        row = await conn.fetchrow("SELECT * FROM warrants WHERE id = $1", warrant_id)
    if not row:
        raise HTTPException(404, "Warrant not found")
    return _json_safe(dict(row))


@router.post("/warrants")
async def issue_warrant(
    body: WarrantBody,
    user: dict = Depends(require_perm("warrants.manage")),
):
    reason = warrant_form_to_reason(body.form) if body.form else "Warrant"
    async with connection() as conn:
        async with conn.transaction():
            row = await create_warrant(
                subject_id=body.subject_id,
                issued_by=int(user["discord_user_id"]),
                form_data=body.form,
                reason=reason,
                conn=conn,
            )
            await enqueue_outbox(
                conn,
                "warrant_issued",
                {
                    "warrant_id": row["id"],
                    "user_id": body.subject_id,
                    "issued_by_name": user.get("global_name") or user.get("username"),
                    "reason": reason,
                    "case_number": row.get("case_number"),
                    "form": body.form,
                },
            )
            await audit(conn, int(user["discord_user_id"]), "warrant_issue", "warrants", row["id"], {})
    return _json_safe(row)


class ServeBody(BaseModel):
    model_config = {"extra": "allow"}


@router.post("/warrants/{warrant_id}/serve")
async def serve(
    warrant_id: int,
    user: dict = Depends(require_perm("warrants.manage")),
    body: ServeBody | None = None,
):
    serve_data = body.model_dump() if body else {}
    async with connection() as conn:
        existing = await conn.fetchrow("SELECT * FROM warrants WHERE id = $1", warrant_id)
        if not existing:
            raise HTTPException(404, "Warrant not found")
        if str(existing.get("status") or "").lower() == "served":
            raise HTTPException(409, "This warrant has already been served. Original issue data was not changed.")
        async with conn.transaction():
            row = await serve_warrant(warrant_id, int(user["discord_user_id"]), serve_data, conn)
            if not row:
                raise HTTPException(400, "Warrant is not active")
            await enqueue_outbox(conn, "warrant_served", {"warrant_id": warrant_id, "user_id": row["user_id"]})
            await audit(conn, int(user["discord_user_id"]), "warrant_serve", "warrants", warrant_id, {})
    return _json_safe(row)


@router.post("/warrants/{warrant_id}/clear")
async def clear(
    warrant_id: int,
    user: dict = Depends(require_perm("warrants.manage")),
):
    async with connection() as conn:
        async with conn.transaction():
            row = await clear_warrant(warrant_id, conn)
            if not row:
                raise HTTPException(400, "Warrant is not active")
            await enqueue_outbox(conn, "warrant_cleared", {"warrant_id": warrant_id, "user_id": row["user_id"]})
            await audit(conn, int(user["discord_user_id"]), "warrant_clear", "warrants", warrant_id, {})
    return _json_safe(row)


class BoloBody(BaseModel):
    kind: str = Field(pattern="^(person|vehicle)$")
    description: str = Field(min_length=3, max_length=2000)
    plate: str | None = None
    discord_user_id: int | None = None
    danger: str | None = None


@router.get("/bolos")
async def list_bolos(user: dict = Depends(require_perm("bolos.manage", "records.read"))):
    async with connection() as conn:
        rows = await conn.fetch("SELECT * FROM cad_bolos ORDER BY created_at DESC LIMIT 100")
    return {"results": [_json_safe(dict(r)) for r in rows]}


@router.post("/bolos")
async def create_bolo(body: BoloBody, user: dict = Depends(require_perm("bolos.manage"))):
    async with connection() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO cad_bolos (kind, description, plate, discord_user_id, danger, issued_by, status)
            VALUES ($1,$2,$3,$4,$5,$6,'active') RETURNING *
            """,
            body.kind,
            body.description,
            body.plate,
            body.discord_user_id,
            body.danger,
            int(user["discord_user_id"]),
        )
        await audit(conn, int(user["discord_user_id"]), "bolo_create", "cad_bolos", row["id"], {})
    return _json_safe(dict(row))


@router.post("/bolos/{bolo_id}/clear")
async def clear_bolo(bolo_id: int, user: dict = Depends(require_perm("bolos.manage"))):
    async with connection() as conn:
        row = await conn.fetchrow(
            """
            UPDATE cad_bolos SET status='cleared', cleared_at=NOW(), cleared_by=$1
            WHERE id=$2 AND status='active' RETURNING *
            """,
            int(user["discord_user_id"]),
            bolo_id,
        )
        if not row:
            raise HTTPException(404, "BOLO not found")
    return _json_safe(dict(row))


@router.get("/civilian/me")
async def civilian_me(user: dict = Depends(require_perm("civilian.self"))):
    return await person_file(int(user["discord_user_id"]), user)


@router.get("/shifts/active")
async def active_shifts(
    guild: str | None = None,
    user: dict = Depends(require_perm("shifts.read", "cad.supervisor")),
):
    from cad.settings import get_settings

    s = get_settings()
    gid = None
    if guild == "fd":
        gid = s.fd_guild_id
    elif guild == "pd":
        gid = s.pd_guild_id
    async with connection() as conn:
        if gid:
            rows = await conn.fetch(
                """
                SELECT id, guild_id, user_id, status, started_at, break_seconds
                FROM shift_sessions WHERE ended_at IS NULL AND deleted = 0 AND guild_id = $1
                ORDER BY started_at DESC
                """,
                gid,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT id, guild_id, user_id, status, started_at, break_seconds
                FROM shift_sessions WHERE ended_at IS NULL AND deleted = 0
                ORDER BY started_at DESC LIMIT 200
                """
            )
    return {"results": [_json_safe(dict(r)) for r in rows]}


@router.get("/applications")
async def list_applications(user: dict = Depends(require_perm("applications.review", "cad.admin"))):
    async with connection() as conn:
        rows = await conn.fetch(
            """
            SELECT id, user_id, status, department, created_at, reviewed_at, review_reason
            FROM applications ORDER BY created_at DESC LIMIT 100
            """
        )
    return {"results": [_json_safe(dict(r)) for r in rows]}


@router.get("/applications/{application_id}")
async def get_application(
    application_id: int,
    user: dict = Depends(require_perm("applications.review", "cad.admin")),
):
    async with connection() as conn:
        app = await conn.fetchrow("SELECT * FROM applications WHERE id = $1", application_id)
        if not app:
            raise HTTPException(404, "Application not found")
        answers = await conn.fetch(
            "SELECT question, answer FROM application_answers WHERE application_id = $1 ORDER BY id",
            application_id,
        )
    return {"application": _json_safe(dict(app)), "answers": [dict(r) for r in answers]}
