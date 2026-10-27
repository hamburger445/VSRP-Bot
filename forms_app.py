"""HTTPS document editor (Render). Does not use the Discord gateway."""

from __future__ import annotations

import json
import logging
import os
import sys
from urllib.parse import unquote

from aiohttp import web
from dotenv import load_dotenv

from utils.core import ROOT, load_config
from utils.form_discord import FormUser, discord_member, discord_user, display_name
from utils.form_store import load_draft, save_draft
from utils.form_tokens import parse_form_token
from utils.form_urls import form_bind_host, form_bind_port, public_form_base
from utils.leo_forms import CITATION_AGENCIES
from utils.leo_pdf import citation_pdf_bytes, served_warrant_pdf_bytes, warrant_pdf_bytes
from utils.leo_submit import submit_citation, submit_serve_warrant, submit_warrant

load_dotenv(ROOT / ".env", override=True)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("wcrp_forms")
FORMS = ROOT / "webforms"


def _html(text: str, status: int = 200) -> web.Response:
    return web.Response(text=text, content_type="text/html", charset="utf-8", status=status)


def _json(data: dict, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def _editor_page(kind: str, token: str) -> str:
    text = (FORMS / "editor.html").read_text(encoding="utf-8")
    return text.replace("{{TOKEN_JSON}}", json.dumps(token)).replace("{{KIND_JSON}}", json.dumps(kind))


def _token_payload(request: web.Request, body: dict | None = None) -> dict:
    raw = ""
    if body:
        raw = str(body.get("token") or "")
    if not raw:
        raw = unquote(str(request.query.get("token") or request.match_info.get("token") or ""))
    return parse_form_token(raw)


async def home(_request: web.Request):
    base = public_form_base() or "(set PUBLIC_FORM_URL to https://your-service.onrender.com)"
    return _html(
        "<!doctype html><meta charset=utf-8><title>WCRP forms</title>"
        "<body style='font-family:Georgia;background:#1c1c1c;color:#eee;padding:40px'>"
        "<h1>WCRP document editor</h1>"
        f"<p>This host is up. Open a form from Discord <code>/citation</code>."
        f" Public URL: {base}</p></body>"
    )


async def health(_request: web.Request):
    return _json({"ok": True, "forms": True, "public": public_form_base() or None})


async def editor(request: web.Request):
    kind = request.match_info.get("kind") or "citation"
    token = unquote(request.match_info.get("token") or "")
    try:
        payload = parse_form_token(token)
    except ValueError as exc:
        return _html(f"<!doctype html><meta charset=utf-8><p>{exc}</p>", 400)
    if payload.get("t") not in {kind, "editor"} and kind != "editor":
        if payload.get("t") != kind:
            kind = str(payload.get("t") or kind)
    return _html(_editor_page(kind, token))


async def bootstrap(request: web.Request):
    try:
        payload = _token_payload(request)
    except ValueError as exc:
        return _json({"error": str(exc)}, 400)
    oid, sid, gid = int(payload["o"]), int(payload["s"]), int(payload["g"])
    import asyncio

    gathered = await asyncio.gather(
        discord_user(oid),
        discord_user(sid),
        discord_member(gid, oid),
        discord_member(gid, sid),
        return_exceptions=True,
    )

    def _ok(item, fallback):
        return fallback if isinstance(item, Exception) else item

    officer_u = _ok(gathered[0], {"id": str(oid), "username": str(oid)})
    subject_u = _ok(gathered[1], {"id": str(sid), "username": str(sid)})
    officer_m = _ok(gathered[2], {})
    subject_m = _ok(gathered[3], {})
    draft = {}
    warrants = []
    try:
        draft = await asyncio.wait_for(load_draft(str(payload.get("j") or "")), timeout=4)
    except Exception:
        log.warning("bootstrap draft skipped")
    try:
        from utils.database import get_db

        db = await asyncio.wait_for(get_db(), timeout=4)
        rows = await asyncio.wait_for(
            db.execute_fetchall(
                "SELECT id, reason, case_number, status FROM warrants WHERE user_id = ? AND active = 1 ORDER BY id DESC",
                (sid,),
            ),
            timeout=4,
        )
        warrants = [dict(r) for r in rows or []]
    except Exception:
        log.warning("bootstrap warrants skipped")
    return _json(
        {
            "officer": display_name(officer_u, officer_m),
            "subject": display_name(subject_u, subject_m),
            "officer_id": str(oid),
            "subject_id": str(sid),
            "agencies": list(CITATION_AGENCIES),
            "agency": "",
            "warrants": warrants,
            "draft": draft,
            "kind": payload.get("t"),
        }
    )


def _identity(user_id: int, name: str) -> FormUser:
    return FormUser(user_id, name)


def _warrant_form(form: dict) -> dict:
    data = dict(form)
    data["courts"] = {
        "general_district": bool(form.get("court_general_district")),
        "criminal": bool(form.get("court_criminal")),
        "traffic": bool(form.get("court_traffic")),
        "jdr": bool(form.get("court_jdr")),
    }
    return data


async def save_draft_api(request: web.Request):
    body = await request.json()
    try:
        payload = _token_payload(request, body)
    except ValueError as exc:
        return _json({"error": str(exc)}, 400)
    kind = str(body.get("kind") or payload.get("t") or "citation")
    await save_draft(
        jti=str(payload.get("j") or ""),
        kind=kind,
        officer_id=int(payload["o"]),
        subject_id=int(payload["s"]),
        guild_id=int(payload["g"]),
        payload=body.get("form") or {},
    )
    return _json({"ok": True})


async def preview_pdf(request: web.Request):
    body = await request.json()
    try:
        payload = _token_payload(request, body)
    except ValueError as exc:
        return _json({"error": str(exc)}, 400)
    form = body.get("form") or {}
    kind = str(body.get("kind") or payload.get("t") or "citation")
    if kind == "warrant":
        data = warrant_pdf_bytes(_warrant_form(form), case_no="DRAFT", subject_label="", issuer_label="")
        name = "warrant-draft.pdf"
    elif kind == "serve":
        data = served_warrant_pdf_bytes({}, form, case_no="DRAFT")
        name = "served-draft.pdf"
    else:
        try:
            fine = int(str(form.get("fine") or "0").replace(",", "").replace("$", "") or "0")
        except ValueError:
            fine = 0
        data = citation_pdf_bytes(form, case_no="DRAFT", fine=fine)
        name = "summons-draft.pdf"
    return web.Response(body=data, content_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{name}"'})


async def submit_api(request: web.Request):
    body = await request.json()
    try:
        payload = _token_payload(request, body)
    except ValueError as exc:
        return _json({"error": str(exc)}, 400)
    form = body.get("form") or {}
    kind = str(body.get("kind") or payload.get("t") or "citation")
    if payload.get("t") not in {kind, "editor"}:
        return _json({"error": "This link is for a different document type."}, 400)
    officer = _identity(int(payload["o"]), str(form.get("issued_by") or form.get("arresting_officer") or "Officer"))
    subject = _identity(int(payload["s"]), str(form.get("accused") or "Subject"))
    jti = str(payload.get("j") or "")
    gid = int(payload["g"])
    try:
        if kind == "warrant":
            result = await submit_warrant(
                None, guild=None, officer=officer, subject=subject, form_data=_warrant_form(form), jti=jti, guild_id=gid
            )
        elif kind == "serve":
            warrant_id = int(str(form.get("warrant_id") or "0"))
            result = await submit_serve_warrant(
                None, guild=None, officer=officer, warrant_id=warrant_id, serve_data=form, jti=jti, guild_id=gid
            )
        else:
            if not form.get("agency") or not form.get("charge_description"):
                return _json({"error": "Agency and charge description are required."}, 400)
            try:
                fine = int(str(form.get("fine") or "0").replace(",", "").replace("$", "") or "0")
            except ValueError:
                return _json({"error": "Fine must be a number."}, 400)
            form["court_date"] = f"{form.get('court_month', '')} {form.get('court_day', '')}, {form.get('court_year', '')}".strip()
            result = await submit_citation(
                None, guild=None, officer=officer, subject=subject, form_data=form, fine=fine, jti=jti, guild_id=gid
            )
    except ValueError as exc:
        return _json({"error": str(exc)}, 400)
    except Exception:
        log.exception("submit failed")
        return _json({"error": "Could not save or post this document."}, 500)
    return _json({"ok": True, "case_number": result["case_number"], "review_id": result.get("review_id")})


_runner: web.AppRunner | None = None


async def start_attached_server() -> web.AppRunner:
    """Bind the editor on PORT so Render's web process (the Discord bot) stays healthy."""
    global _runner
    if _runner:
        return _runner
    host = form_bind_host()
    port = form_bind_port()
    _runner = web.AppRunner(make_app())
    await _runner.setup()
    await web.TCPSite(_runner, host, port).start()
    log.info("Document editor on %s:%s public=%s", host, port, public_form_base() or "(unset)")
    return _runner


def make_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", home)
    app.router.add_get("/health", health)
    app.router.add_get("/e/{kind}/{token}", editor)
    app.router.add_get("/api/bootstrap", bootstrap)
    app.router.add_put("/api/draft", save_draft_api)
    app.router.add_post("/api/submit", submit_api)
    app.router.add_post("/api/preview.pdf", preview_pdf)
    return app


async def _async_main():
    from utils.database import get_db

    load_config()
    await get_db()
    await start_attached_server()
    while True:
        await __import__("asyncio").sleep(3600)


def main():
    import asyncio

    try:
        asyncio.run(_async_main())
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
