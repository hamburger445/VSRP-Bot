"""HTTP paper forms served by the Discord bot."""

from __future__ import annotations

import html
import logging
from pathlib import Path
from urllib.parse import unquote

from aiohttp import web

from utils.core import ROOT, format_datetime
from utils.database import get_db
from utils.form_tokens import parse_form_token
from utils.form_urls import form_bind_host, form_bind_port
from utils.helpers import officer_agency_name
from utils.leo_forms import CITATION_AGENCIES
from utils.leo_submit import submit_citation, submit_serve_warrant, submit_warrant

log = logging.getLogger("wcrp_bot.forms")
FORMS = ROOT / "webforms"
_used_jti: set[str] = set()
_bot = None


def _page(name: str) -> str:
    return (FORMS / name).read_text(encoding="utf-8")


def _render(name: str, **parts: str) -> str:
    text = _page(name)
    parts.setdefault("CSS", _page("forms.css"))
    for key, value in parts.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def _html(text: str, status: int = 200) -> web.Response:
    return web.Response(text=text, content_type="text/html", charset="utf-8", status=status)


def _err(msg: str) -> str:
    return f'<p class="err">{html.escape(msg)}</p>'


async def _context(request: web.Request, kind: str) -> tuple[dict, web.Response | None]:
    if request.method == "POST":
        post = await request.post()
        token = str(post.get("token") or request.match_info.get("token") or "")
    else:
        post = None
        token = str(request.match_info.get("token") or request.query.get("token") or "")
    token = unquote(token)
    try:
        payload = parse_form_token(token)
    except ValueError as exc:
        return {}, _html(_render("error.html", MESSAGE=html.escape(str(exc))), 400)
    if payload.get("t") != kind:
        return {}, web.Response(
            text=_render("error.html", MESSAGE="Wrong form type for this link."),
            content_type="text/html",
            status=400,
        )
    jti = str(payload.get("j") or "")
    if request.method == "POST" and jti in _used_jti:
        return {}, web.Response(text=_render("done.html", CASE="already filed"), content_type="text/html")
    payload["_token"] = token
    payload["_post"] = post
    payload["_jti"] = jti
    return payload, None


async def _users(payload: dict):
    bot = _bot
    guild = bot.get_guild(int(payload["g"])) if bot else None
    oid, sid = int(payload["o"]), int(payload["s"])
    officer = guild.get_member(oid) if guild else None
    subject = guild.get_member(sid) if guild else None
    if guild:
        if officer is None:
            try:
                officer = await guild.fetch_member(oid)
            except Exception:
                officer = None
        if subject is None:
            try:
                subject = await guild.fetch_member(sid)
            except Exception:
                subject = None
    if officer is None and bot:
        officer = bot.get_user(oid)
    if subject is None and bot:
        subject = bot.get_user(sid)
    return bot, guild, officer, subject


async def get_citation(request: web.Request):
    payload, err = await _context(request, "citation")
    if err:
        return err
    bot, guild, officer, subject = await _users(payload)
    agency = officer_agency_name(officer)
    opts = []
    for a in CITATION_AGENCIES:
        sel = " selected" if a == agency else ""
        opts.append(f'<option{sel}>{html.escape(a)}</option>')
    issued = " ".join(x for x in [getattr(officer, "display_name", ""), agency] if x)
    return web.Response(
        text=_render(
            "citation.html",
            TOKEN=html.escape(payload["_token"]),
            SUBJECT=html.escape(str(subject) if subject else payload["s"]),
            OFFICER=html.escape(getattr(officer, "display_name", "")),
            ISSUED_BY=html.escape(issued),
            AGENCY_OPTIONS="".join(opts),
            ERROR="",
        ),
        content_type="text/html",
    )


async def post_citation(request: web.Request):
    payload, err = await _context(request, "citation")
    if err:
        return err
    post = payload["_post"]
    bot, guild, officer, subject = await _users(payload)
    if not officer or not subject:
        return web.Response(text=_render("error.html", MESSAGE="Officer or subject not found. Stay in the Discord server."), content_type="text/html", status=400)
    try:
        fine = int(str(post.get("fine") or "0").replace(",", "").replace("$", "").strip() or "0")
    except ValueError:
        return web.Response(text=_render("error.html", MESSAGE="Fine must be a number."), content_type="text/html", status=400)
    form = {
        "agency": str(post.get("agency") or ""),
        "county": str(post.get("county") or "WYTHE"),
        "court_type": str(post.get("court_type") or ""),
        "court_month": str(post.get("court_month") or ""),
        "court_day": str(post.get("court_day") or ""),
        "court_year": str(post.get("court_year") or ""),
        "court_time": str(post.get("court_time") or ""),
        "am_pm": str(post.get("am_pm") or "AM"),
        "court_date": f"{post.get('court_month', '')} {post.get('court_day', '')}, {post.get('court_year', '')}".strip(),
        "va_code": str(post.get("va_code") or ""),
        "local_code": str(post.get("local_code") or ""),
        "charge_description": str(post.get("charge_description") or ""),
        "commercial_motor_vehicle": str(post.get("commercial_motor_vehicle") or "No"),
        "hazardous_materials": str(post.get("hazardous_materials") or "No"),
        "resulted_in_fatality": str(post.get("resulted_in_fatality") or "No"),
        "highway_safety_corridor": str(post.get("highway_safety_corridor") or "No"),
        "issued_by": str(post.get("issued_by") or officer.display_name),
    }
    if not form["agency"] or not form["charge_description"]:
        return web.Response(text=_render("error.html", MESSAGE="Agency and charge description are required."), content_type="text/html", status=400)
    try:
        result = await submit_citation(bot, guild=guild, officer=officer, subject=subject, form_data=form, fine=fine)
    except Exception:
        log.exception("citation submit failed")
        return web.Response(text=_render("error.html", MESSAGE="Could not save or post this summons. Try again."), content_type="text/html", status=500)
    _used_jti.add(payload["_jti"])
    return web.Response(text=_render("done.html", CASE=html.escape(result["case_number"])), content_type="text/html")


async def get_warrant(request: web.Request):
    payload, err = await _context(request, "warrant")
    if err:
        return err
    bot, guild, officer, subject = await _users(payload)
    from datetime import datetime, timezone

    return web.Response(
        text=_render(
            "warrant.html",
            TOKEN=html.escape(payload["_token"]),
            SUBJECT=html.escape(str(subject) if subject else payload["s"]),
            OFFICER=html.escape(getattr(officer, "display_name", "")),
            ISSUED_AT=html.escape(format_datetime(datetime.now(timezone.utc))),
            ERROR="",
        ),
        content_type="text/html",
    )


async def post_warrant(request: web.Request):
    payload, err = await _context(request, "warrant")
    if err:
        return err
    post = payload["_post"]
    bot, guild, officer, subject = await _users(payload)
    if not officer or not subject:
        return web.Response(text=_render("error.html", MESSAGE="Officer or subject not found."), content_type="text/html", status=400)
    form = {
        "warrant_type": str(post.get("warrant_type") or "Arrest"),
        "charge_class": str(post.get("charge_class") or "Misdemeanor"),
        "jurisdiction": str(post.get("jurisdiction") or "County"),
        "county": str(post.get("county") or "WYTHE"),
        "courts": {
            "general_district": post.get("court_general_district") == "on",
            "criminal": post.get("court_criminal") == "on",
            "traffic": post.get("court_traffic") == "on",
            "jdr": post.get("court_jdr") == "on",
        },
        "va_code": str(post.get("va_code") or ""),
        "code_description": str(post.get("code_description") or ""),
        "accused": str(post.get("accused") or ""),
        "sex": str(post.get("sex") or ""),
        "race": str(post.get("race") or ""),
        "eyes": str(post.get("eyes") or ""),
        "hair": str(post.get("hair") or ""),
        "issued_datetime": str(post.get("issued_datetime") or ""),
        "issued_by": officer.display_name,
    }
    if not form["accused"]:
        return web.Response(text=_render("error.html", MESSAGE="Accused name is required."), content_type="text/html", status=400)
    try:
        result = await submit_warrant(bot, guild=guild, officer=officer, subject=subject, form_data=form)
    except Exception:
        log.exception("warrant submit failed")
        return web.Response(text=_render("error.html", MESSAGE="Could not save or post this warrant. Try again."), content_type="text/html", status=500)
    _used_jti.add(payload["_jti"])
    return web.Response(text=_render("done.html", CASE=html.escape(result["case_number"])), content_type="text/html")


async def get_serve(request: web.Request):
    payload, err = await _context(request, "serve")
    if err:
        return err
    bot, guild, officer, subject = await _users(payload)
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT id, reason, case_number, status FROM warrants WHERE user_id = ? AND active = 1 ORDER BY id DESC",
        (int(payload["s"]),),
    )
    opts = ['<option value="">Select</option>']
    for row in rows or []:
        label = f"#{row.get('case_number') or row['id']} — {row.get('reason') or ''}"
        opts.append(f'<option value="{int(row["id"])}">{html.escape(label[:120])}</option>')
    agency = officer_agency_name(officer)
    from datetime import datetime, timezone

    badge = ", ".join(x for x in [agency, "Wythe County"] if x)
    return web.Response(
        text=_render(
            "serve.html",
            TOKEN=html.escape(payload["_token"]),
            SUBJECT=html.escape(str(subject) if subject else payload["s"]),
            OFFICER=html.escape(getattr(officer, "display_name", "")),
            BADGE=html.escape(badge),
            SERVED_AT=html.escape(format_datetime(datetime.now(timezone.utc))),
            WARRANT_OPTIONS="".join(opts),
            ERROR="" if rows else _err("No active warrants for this member."),
        ),
        content_type="text/html",
    )


async def post_serve(request: web.Request):
    payload, err = await _context(request, "serve")
    if err:
        return err
    post = payload["_post"]
    bot, guild, officer, subject = await _users(payload)
    try:
        warrant_id = int(str(post.get("warrant_id") or "0"))
    except ValueError:
        return web.Response(text=_render("error.html", MESSAGE="Select a warrant."), content_type="text/html", status=400)
    serve = {
        "arresting_officer": str(post.get("arresting_officer") or ""),
        "badge_agency_jurisdiction": str(post.get("badge_agency_jurisdiction") or ""),
        "served_datetime": str(post.get("served_datetime") or ""),
    }
    try:
        result = await submit_serve_warrant(bot, guild=guild, officer=officer, warrant_id=warrant_id, serve_data=serve)
    except ValueError as exc:
        return web.Response(text=_render("error.html", MESSAGE=html.escape(str(exc))), content_type="text/html", status=400)
    except Exception:
        log.exception("serve warrant submit failed")
        return web.Response(text=_render("error.html", MESSAGE="Could not save or post this served warrant. Try again."), content_type="text/html", status=500)
    _used_jti.add(payload["_jti"])
    return web.Response(text=_render("done.html", CASE=html.escape(result["case_number"])), content_type="text/html")


async def static_css(_request: web.Request):
    return web.FileResponse(FORMS / "forms.css")


async def health(_request: web.Request):
    return web.json_response({"ok": True, "forms": True})


async def home(_request: web.Request):
    return _html(_render("home.html"))


async def not_found(_request: web.Request):
    return _html(
        _render(
            "error.html",
            MESSAGE="This is not a form link. In Discord run /citation and open that URL in Chrome or Safari.",
        ),
        404,
    )


async def start_form_server(bot) -> web.AppRunner:
    global _bot
    _bot = bot
    app = web.Application()
    app.router.add_get("/", home)
    app.router.add_get("/health", health)
    app.router.add_get("/static/forms.css", static_css)
    app.router.add_get("/f/citation/{token}", get_citation)
    app.router.add_get("/f/citation", get_citation)
    app.router.add_post("/f/citation", post_citation)
    app.router.add_get("/f/warrant/{token}", get_warrant)
    app.router.add_get("/f/warrant", get_warrant)
    app.router.add_post("/f/warrant", post_warrant)
    app.router.add_get("/f/serve/{token}", get_serve)
    app.router.add_get("/f/serve", get_serve)
    app.router.add_post("/f/serve", post_serve)
    app.router.add_route("*", "/{tail:.*}", not_found)
    runner = web.AppRunner(app)
    await runner.setup()
    host = form_bind_host()
    port = form_bind_port()
    site = web.TCPSite(runner, host, port)
    await site.start()
    log.info("Paper forms listening on %s:%s", host, port)
    return runner
