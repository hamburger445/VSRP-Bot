"""Calls, units, dispatch, fire/EMS/DOT, map."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from cad.db import audit, connection, enqueue_outbox
from cad.deps import require_perm

router = APIRouter(prefix="/api", tags=["dispatch"])


def _js(row) -> dict | None:
    if row is None:
        return None
    data = dict(row)
    for k, v in list(data.items()):
        if hasattr(v, "isoformat"):
            data[k] = v.isoformat()
    return data


async def _next_call_number(conn) -> str:
    row = await conn.fetchrow(
        """
        INSERT INTO bot_state (key, value) VALUES ('cad_call_sequence', '1')
        ON CONFLICT (key) DO UPDATE
        SET value = (COALESCE(NULLIF(bot_state.value, '')::int, 0) + 1)::text
        RETURNING value
        """
    )
    return f"CAD-{int(row['value']):04d}"


class CallBody(BaseModel):
    discipline: str = Field(pattern="^(police|fire|ems|dot|multi)$")
    priority: int = Field(default=3, ge=1, le=5)
    nature: str | None = None
    location_text: str | None = None
    details: str | None = None
    lat: float | None = None
    lng: float | None = None


class NoteBody(BaseModel):
    body: str = Field(min_length=1, max_length=2000)


class UnitBody(BaseModel):
    callsign: str = Field(min_length=1, max_length=32)
    department: str
    discord_user_id: int | None = None


class StatusBody(BaseModel):
    status: str = Field(min_length=1, max_length=40)


class AssignBody(BaseModel):
    unit_id: int


class PageBody(BaseModel):
    department: str
    message: str = Field(min_length=1, max_length=1000)


class PositionBody(BaseModel):
    lat: float
    lng: float
    label: str | None = None


class PcrBody(BaseModel):
    call_id: int | None = None
    patient_name: str | None = None
    chief_complaint: str | None = None
    disposition: str | None = None


class ClosureBody(BaseModel):
    location_text: str
    notes: str | None = None
    lat: float | None = None
    lng: float | None = None


@router.get("/calls")
async def list_calls(
    discipline: str | None = None,
    status: str | None = None,
    user: dict = Depends(require_perm("dispatch.operate", "mdt.police", "mdt.fire", "mdt.ems", "mdt.dot", "records.read")),
):
    async with connection() as conn:
        if discipline and status:
            rows = await conn.fetch(
                "SELECT * FROM cad_calls WHERE discipline=$1 AND status=$2 ORDER BY opened_at DESC LIMIT 200",
                discipline,
                status,
            )
        elif discipline:
            rows = await conn.fetch(
                "SELECT * FROM cad_calls WHERE discipline=$1 ORDER BY opened_at DESC LIMIT 200",
                discipline,
            )
        elif status:
            rows = await conn.fetch(
                "SELECT * FROM cad_calls WHERE status=$1 ORDER BY opened_at DESC LIMIT 200",
                status,
            )
        else:
            rows = await conn.fetch("SELECT * FROM cad_calls ORDER BY opened_at DESC LIMIT 200")
    return {"results": [_js(r) for r in rows]}


@router.post("/calls")
async def create_call(body: CallBody, user: dict = Depends(require_perm("dispatch.operate", "calls.manage", "mdt.police"))):
    async with connection() as conn:
        async with conn.transaction():
            number = await _next_call_number(conn)
            row = await conn.fetchrow(
                """
                INSERT INTO cad_calls (call_number, discipline, priority, nature, location_text, details, lat, lng, created_by, status)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,'pending') RETURNING *
                """,
                number,
                body.discipline,
                body.priority,
                body.nature,
                body.location_text,
                body.details,
                body.lat,
                body.lng,
                int(user["discord_user_id"]),
            )
            await audit(conn, int(user["discord_user_id"]), "call_create", "cad_calls", row["id"], {"number": number})
    return _js(row)


@router.get("/calls/{call_id}")
async def get_call(
    call_id: int,
    user: dict = Depends(require_perm("dispatch.operate", "mdt.police", "mdt.fire", "mdt.ems", "mdt.dot", "records.read")),
):
    async with connection() as conn:
        row = await conn.fetchrow("SELECT * FROM cad_calls WHERE id=$1", call_id)
        if not row:
            raise HTTPException(404, "Call not found")
        notes = await conn.fetch(
            "SELECT * FROM cad_call_notes WHERE call_id=$1 ORDER BY created_at",
            call_id,
        )
        units = await conn.fetch(
            """
            SELECT u.*, cu.assigned_at, cu.cleared_at
            FROM cad_call_units cu JOIN cad_units u ON u.id = cu.unit_id
            WHERE cu.call_id=$1
            """,
            call_id,
        )
    return {"call": _js(row), "notes": [_js(n) for n in notes], "units": [_js(u) for u in units]}


@router.post("/calls/{call_id}/notes")
async def add_note(call_id: int, body: NoteBody, user: dict = Depends(require_perm("dispatch.operate", "mdt.police", "mdt.fire", "mdt.ems", "mdt.dot"))):
    async with connection() as conn:
        row = await conn.fetchrow(
            "INSERT INTO cad_call_notes (call_id, author_id, body) VALUES ($1,$2,$3) RETURNING *",
            call_id,
            int(user["discord_user_id"]),
            body.body,
        )
    return _js(row)


@router.post("/calls/{call_id}/assign")
async def assign_unit(call_id: int, body: AssignBody, user: dict = Depends(require_perm("dispatch.operate", "calls.manage"))):
    async with connection() as conn:
        async with conn.transaction():
            exists = await conn.fetchval(
                "SELECT 1 FROM cad_call_units WHERE call_id=$1 AND unit_id=$2 FOR UPDATE",
                call_id,
                body.unit_id,
            )
            if exists:
                raise HTTPException(409, "Unit already assigned")
            await conn.execute(
                """
                INSERT INTO cad_call_units (call_id, unit_id) VALUES ($1,$2)
                ON CONFLICT (call_id, unit_id) DO NOTHING
                """,
                call_id,
                body.unit_id,
            )
            await conn.execute(
                "UPDATE cad_calls SET status='assigned', primary_unit_id=COALESCE(primary_unit_id,$2) WHERE id=$1 AND status IN ('pending','assigned')",
                call_id,
                body.unit_id,
            )
            await conn.execute(
                "UPDATE cad_units SET status='en_route', updated_at=NOW() WHERE id=$1",
                body.unit_id,
            )
            await audit(conn, int(user["discord_user_id"]), "call_assign", "cad_calls", call_id, {"unit_id": body.unit_id})
    return {"ok": True}


@router.post("/calls/{call_id}/close")
async def close_call(call_id: int, user: dict = Depends(require_perm("dispatch.operate", "calls.manage"))):
    async with connection() as conn:
        row = await conn.fetchrow(
            "UPDATE cad_calls SET status='closed', closed_at=NOW() WHERE id=$1 RETURNING *",
            call_id,
        )
        if not row:
            raise HTTPException(404, "Call not found")
        await conn.execute(
            "UPDATE cad_call_units SET cleared_at=NOW() WHERE call_id=$1 AND cleared_at IS NULL",
            call_id,
        )
    return _js(row)


@router.get("/units")
async def list_units(user: dict = Depends(require_perm("dispatch.operate", "units.self", "units.manage", "mdt.police", "mdt.fire", "mdt.ems", "mdt.dot"))):
    async with connection() as conn:
        rows = await conn.fetch(
            """
            SELECT u.*, p.lat, p.lng, p.source AS position_source, p.updated_at AS position_updated_at
            FROM cad_units u
            LEFT JOIN cad_unit_positions p ON p.unit_id = u.id
            ORDER BY u.callsign
            """
        )
    return {"results": [_js(r) for r in rows]}


@router.post("/units")
async def create_unit(body: UnitBody, user: dict = Depends(require_perm("units.manage", "cad.admin", "dispatch.operate"))):
    async with connection() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO cad_units (callsign, department, discord_user_id)
            VALUES ($1,$2,$3) RETURNING *
            """,
            body.callsign.strip().upper(),
            body.department,
            body.discord_user_id,
        )
        await audit(conn, int(user["discord_user_id"]), "unit_create", "cad_units", row["id"], {})
    return _js(row)


@router.post("/units/{unit_id}/status")
async def set_status(unit_id: int, body: StatusBody, user: dict = Depends(require_perm("units.self", "units.manage", "dispatch.operate"))):
    async with connection() as conn:
        unit = await conn.fetchrow("SELECT * FROM cad_units WHERE id=$1", unit_id)
        if not unit:
            raise HTTPException(404, "Unit not found")
        if "units.manage" not in user["permissions"] and "dispatch.operate" not in user["permissions"] and "cad.admin" not in user["permissions"]:
            if unit["discord_user_id"] != int(user["discord_user_id"]):
                raise HTTPException(403, "Not your unit")
        row = await conn.fetchrow(
            "UPDATE cad_units SET status=$1, updated_at=NOW() WHERE id=$2 RETURNING *",
            body.status,
            unit_id,
        )
    return _js(row)


@router.post("/dispatch/page")
async def page_dept(body: PageBody, user: dict = Depends(require_perm("dispatch.operate", "mdt.police"))):
    async with connection() as conn:
        await enqueue_outbox(
            conn,
            "pager",
            {
                "department": body.department,
                "message": body.message,
                "paged_by": user.get("global_name") or user.get("username"),
            },
        )
        await audit(conn, int(user["discord_user_id"]), "pager", "department", body.department, {})
    return {"ok": True, "pending_delivery": True}


@router.get("/map/units")
async def map_units(user: dict = Depends(require_perm("dispatch.operate", "mdt.police", "mdt.fire", "mdt.ems", "mdt.dot", "units.self"))):
    async with connection() as conn:
        placed = await conn.fetch(
            """
            SELECT u.id, u.callsign, u.department, u.status, p.lat, p.lng, p.label, p.source
            FROM cad_units u JOIN cad_unit_positions p ON p.unit_id = u.id
            """
        )
        unplaced = await conn.fetch(
            """
            SELECT u.id, u.callsign, u.department, u.status
            FROM cad_units u LEFT JOIN cad_unit_positions p ON p.unit_id = u.id
            WHERE p.unit_id IS NULL
            """
        )
        calls = await conn.fetch(
            "SELECT id, call_number, discipline, status, location_text, lat, lng FROM cad_calls WHERE status NOT IN ('closed','cancelled') AND lat IS NOT NULL"
        )
    return {
        "units": [_js(r) for r in placed],
        "unplaced": [_js(r) for r in unplaced],
        "calls": [_js(r) for r in calls],
    }


@router.put("/map/units/{unit_id}")
async def set_position(unit_id: int, body: PositionBody, user: dict = Depends(require_perm("units.self", "units.manage", "dispatch.operate"))):
    async with connection() as conn:
        await conn.execute(
            """
            INSERT INTO cad_unit_positions (unit_id, lat, lng, label, source, updated_by, updated_at)
            VALUES ($1,$2,$3,$4,'manual',$5,NOW())
            ON CONFLICT (unit_id) DO UPDATE SET lat=EXCLUDED.lat, lng=EXCLUDED.lng, label=EXCLUDED.label, updated_by=EXCLUDED.updated_by, updated_at=NOW()
            """,
            unit_id,
            body.lat,
            body.lng,
            body.label,
            int(user["discord_user_id"]),
        )
    return {"ok": True}


@router.get("/fire/calls")
async def fire_calls(user: dict = Depends(require_perm("mdt.fire", "dispatch.operate"))):
    async with connection() as conn:
        rows = await conn.fetch(
            "SELECT * FROM cad_calls WHERE discipline IN ('fire','multi') ORDER BY opened_at DESC LIMIT 100"
        )
    return {"results": [_js(r) for r in rows]}


@router.get("/ems/calls")
async def ems_calls(user: dict = Depends(require_perm("mdt.ems", "dispatch.operate"))):
    async with connection() as conn:
        rows = await conn.fetch(
            "SELECT * FROM cad_calls WHERE discipline IN ('ems','multi') ORDER BY opened_at DESC LIMIT 100"
        )
        pcrs = await conn.fetch("SELECT * FROM cad_pcrs ORDER BY created_at DESC LIMIT 50")
    return {"results": [_js(r) for r in rows], "pcrs": [_js(r) for r in pcrs]}


@router.post("/ems/pcr")
async def create_pcr(body: PcrBody, user: dict = Depends(require_perm("mdt.ems"))):
    async with connection() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO cad_pcrs (call_id, patient_name, chief_complaint, disposition, author_id)
            VALUES ($1,$2,$3,$4,$5) RETURNING *
            """,
            body.call_id,
            body.patient_name,
            body.chief_complaint,
            body.disposition,
            int(user["discord_user_id"]),
        )
    return _js(row)


@router.get("/dot/calls")
async def dot_calls(user: dict = Depends(require_perm("mdt.dot", "dispatch.operate"))):
    async with connection() as conn:
        rows = await conn.fetch(
            "SELECT * FROM cad_calls WHERE discipline IN ('dot','multi') ORDER BY opened_at DESC LIMIT 100"
        )
        closures = await conn.fetch("SELECT * FROM cad_road_closures ORDER BY created_at DESC LIMIT 50")
    return {"results": [_js(r) for r in rows], "closures": [_js(r) for r in closures]}


@router.post("/dot/closures")
async def create_closure(body: ClosureBody, user: dict = Depends(require_perm("mdt.dot", "dispatch.operate"))):
    async with connection() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO cad_road_closures (location_text, notes, lat, lng, opened_by, status)
            VALUES ($1,$2,$3,$4,$5,'open') RETURNING *
            """,
            body.location_text,
            body.notes,
            body.lat,
            body.lng,
            int(user["discord_user_id"]),
        )
    return _js(row)


@router.post("/dot/closures/{closure_id}/clear")
async def clear_closure(closure_id: int, user: dict = Depends(require_perm("mdt.dot", "dispatch.operate"))):
    async with connection() as conn:
        row = await conn.fetchrow(
            "UPDATE cad_road_closures SET status='cleared', cleared_at=NOW() WHERE id=$1 RETURNING *",
            closure_id,
        )
        if not row:
            raise HTTPException(404, "Not found")
    return _js(row)
