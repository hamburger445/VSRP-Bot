"""FastAPI application: CAD API + production static frontend."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.cors import CORSMiddleware

from cad.realtime import start_listener, stop_listener
from cad.routers import admin, auth, dispatch, ops, ws
from cad.settings import get_settings

log = logging.getLogger("vsrp_bot.cad")
WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    from cad.github_db import github_enabled
    from utils.database import get_db

    if github_enabled():
        from cad.github_db import github_connection

        try:
            async with github_connection() as conn:
                await conn.fetchval("SELECT 1")
            log.info("CAD using GitHub JSON database")
        except Exception:
            log.exception("CAD GitHub database init failed; serving HTTP anyway")
    else:
        try:
            await get_db()
            start_listener()
            log.info("CAD API started (PostgreSQL)")
        except Exception:
            log.exception("CAD database init failed; serving HTTP anyway")
    yield
    await stop_listener()


app = FastAPI(
    title="VSRP CAD / MDT",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.public_url, settings.frontend_url, "http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(ops.router)
app.include_router(dispatch.router)
app.include_router(admin.router)
app.include_router(ws.router)


@app.get("/api/health")
async def health():
    from cad.db import connection, using_github

    errors = settings.health_errors()
    db_ok = False
    backend = "github" if using_github() else "postgres"
    try:
        async with connection() as conn:
            await conn.fetchval("SELECT 1")
        db_ok = True
    except Exception as exc:
        errors.append(f"database: {exc}")
    status = "ok" if db_ok and not errors else "degraded"
    # Always 200 so Render liveness does not restart a running API.
    return JSONResponse(
        {
            "status": status,
            "database": db_ok,
            "backend": backend,
            "errors": errors,
            "oauth_configured": settings.oauth_configured(),
            "public_url": settings.public_url,
            "oauth_redirect": settings.discord_redirect_uri,
        }
    )


_FALLBACK_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>VSRP CAD</title>
  <style>
    body { margin:0; font:14px/1.45 system-ui,sans-serif; background:#0b1220; color:#e8eef7; }
    main { max-width:520px; margin:12vh auto; padding:28px; background:#121a2b; border:1px solid #2a3548; }
    h1 { font-size:18px; letter-spacing:.12em; text-transform:uppercase; color:#8b97ab; }
    a.btn { display:inline-block; margin-top:16px; padding:10px 14px; background:#3d7ea6; color:#fff; text-decoration:none; }
    .err { color:#e74c3c; white-space:pre-wrap; }
    .ok { color:#2ecc71; }
  </style>
</head>
<body>
  <main>
    <h1>VSRP CAD</h1>
    <p>Operations console</p>
    <p id="status">Checking database…</p>
    <p><a class="btn" href="/api/auth/discord/login">Continue with Discord</a></p>
    <p><a href="/api/docs">API docs</a> · <a href="/api/health">Health</a></p>
  </main>
  <script>
    fetch("/api/health").then(r => r.json()).then(d => {
      const el = document.getElementById("status");
      if (d.database) el.innerHTML = '<span class="ok">Database connected.</span>';
      else el.innerHTML = '<span class="err">Database unavailable: ' + (d.errors||[]).join(" ") + '</span>';
    }).catch(() => {
      document.getElementById("status").textContent = "Could not reach /api/health.";
    });
  </script>
</body>
</html>
"""


def _spa_index() -> FileResponse | HTMLResponse:
    index = WEB_DIST / "index.html"
    if index.is_file():
        return FileResponse(index)
    return HTMLResponse(_FALLBACK_HTML)


if WEB_DIST.is_dir() and (WEB_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")


@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
async def root():
    return _spa_index()


@app.get("/{full_path:path}", include_in_schema=False)
async def spa(full_path: str):
    if full_path.startswith("api"):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    candidate = WEB_DIST / full_path
    if full_path and candidate.is_file():
        return FileResponse(candidate)
    return _spa_index()
