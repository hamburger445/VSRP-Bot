"""FastAPI application: CAD API + production static frontend."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.cors import CORSMiddleware

from cad.realtime import start_listener, stop_listener
from cad.routers import admin, auth, dispatch, ops, ws
from cad.settings import get_settings

log = logging.getLogger("vsrp_bot.cad")
WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    from utils.database import get_db

    await get_db()
    start_listener()
    log.info("CAD API started")
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
    errors = settings.health_errors()
    db_ok = False
    try:
        from cad.db import connection

        async with connection() as conn:
            await conn.fetchval("SELECT 1")
        db_ok = True
    except Exception as exc:
        errors.append(f"database: {exc}")
    status = "ok" if db_ok and not errors else "degraded"
    code = 200 if db_ok else 503
    return JSONResponse(
        {"status": status, "database": db_ok, "errors": errors, "oauth_configured": settings.oauth_configured()},
        status_code=code,
    )


if WEB_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def spa(full_path: str):
        candidate = WEB_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(WEB_DIST / "index.html")
