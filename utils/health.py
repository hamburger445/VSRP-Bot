"""Tiny HTTP server so Render's web health check keeps the Discord bot process alive."""

from __future__ import annotations

import logging
import os

from aiohttp import web

log = logging.getLogger("wcrp_bot.health")


def on_render() -> bool:
    return bool(
        os.getenv("RENDER")
        or os.getenv("RENDER_EXTERNAL_URL")
        or os.getenv("RENDER_EXTERNAL_HOSTNAME")
    )


def bind_port() -> int | None:
    raw = (os.getenv("PORT") or "").strip()
    if raw.isdigit():
        return int(raw)
    if on_render():
        return 10000
    return None


async def start_health_server() -> web.AppRunner | None:
    port = bind_port()
    if port is None:
        return None

    async def health(_request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info("Health check listening on 0.0.0.0:%s", port)
    return runner
