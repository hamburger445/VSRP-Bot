"""Public URL for the in-bot paper forms server."""

from __future__ import annotations

import ipaddress
import os
from urllib.parse import quote, urlparse


def form_bind_host() -> str:
    return os.getenv("FORM_BIND", "0.0.0.0")


def form_bind_port() -> int:
    for key in ("FORM_PORT", "SERVER_PORT"):
        raw = os.getenv(key, "").strip()
        if raw.isdigit():
            return int(raw)
    return 8080


def _host_is_public(host: str) -> bool:
    name = (host or "").strip().lower().rstrip(".")
    if not name or name in {"localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal"}:
        return False
    if name.endswith(".local"):
        return False
    try:
        ip = ipaddress.ip_address(name)
    except ValueError:
        return True
    return ip.is_global


def public_form_base() -> str:
    explicit = (os.getenv("PUBLIC_FORM_URL") or os.getenv("FORM_PUBLIC_URL") or "").strip().rstrip("/")
    if explicit:
        host = urlparse(explicit if "://" in explicit else f"http://{explicit}").hostname
        if _host_is_public(host or ""):
            return explicit if "://" in explicit else f"http://{explicit}"
        return ""
    ip = (os.getenv("SERVER_IP") or os.getenv("CONNECT_IP") or "").strip()
    port = form_bind_port()
    if ip and _host_is_public(ip):
        scheme = "https" if os.getenv("FORM_HTTPS", "").strip().lower() in {"1", "true", "yes"} else "http"
        return f"{scheme}://{ip}:{port}"
    return ""


def form_page_url(kind: str, token: str) -> str | None:
    base = public_form_base()
    if not base:
        return None
    return f"{base}/f/{kind}/{quote(token, safe='')}"
