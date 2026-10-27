"""Public HTTPS URL for the document editor (Render). Never IP, localhost, or HTTP."""

from __future__ import annotations

import ipaddress
import os
from urllib.parse import quote, urlparse


def form_bind_host() -> str:
    return os.getenv("FORM_BIND", "0.0.0.0")


def form_bind_port() -> int:
    raw = (os.getenv("PORT") or os.getenv("FORM_PORT") or "").strip()
    if raw.isdigit():
        return int(raw)
    return 10000


def _host_ok(host: str) -> bool:
    name = (host or "").strip().lower().rstrip(".")
    if not name or name in {"localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal"}:
        return False
    if name.endswith(".local"):
        return False
    try:
        ipaddress.ip_address(name)
        return False
    except ValueError:
        return True


def public_form_base() -> str:
    explicit = (os.getenv("PUBLIC_FORM_URL") or os.getenv("FORM_PUBLIC_URL") or "").strip().rstrip("/")
    if not explicit:
        return ""
    parsed = urlparse(explicit if "://" in explicit else f"https://{explicit}")
    if parsed.scheme != "https":
        return ""
    if not _host_ok(parsed.hostname or ""):
        return ""
    return f"https://{parsed.netloc}{parsed.path}".rstrip("/")


def form_page_url(kind: str, token: str) -> str | None:
    base = public_form_base()
    if not base:
        return None
    return f"{base}/e/{kind}/{quote(token, safe='')}"
