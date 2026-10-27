"""Public URL for the in-bot paper forms server."""

from __future__ import annotations

import os


def form_bind_host() -> str:
    return os.getenv("FORM_BIND", "0.0.0.0")


def form_bind_port() -> int:
    for key in ("FORM_PORT", "SERVER_PORT"):
        raw = os.getenv(key, "").strip()
        if raw.isdigit():
            return int(raw)
    return 8080


def public_form_base() -> str:
    explicit = (os.getenv("PUBLIC_FORM_URL") or os.getenv("FORM_PUBLIC_URL") or "").strip().rstrip("/")
    if explicit:
        return explicit
    ip = (os.getenv("SERVER_IP") or os.getenv("CONNECT_IP") or "").strip()
    port = form_bind_port()
    if ip:
        scheme = "https" if os.getenv("FORM_HTTPS", "").strip() in {"1", "true", "yes"} else "http"
        return f"{scheme}://{ip}:{port}"
    return ""
