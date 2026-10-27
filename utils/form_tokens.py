"""Signed one-time links from Discord to the paper forms website."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from uuid import uuid4

from utils.core import get_token


def _secret() -> bytes:
    extra = os.getenv("FORM_SECRET", "")
    raw = extra or get_token()
    return hashlib.sha256(raw.encode()).digest()


def sign_form_token(*, kind: str, guild_id: int, officer_id: int, subject_id: int, ttl: int = 7200) -> str:
    payload = {
        "t": kind,
        "g": int(guild_id),
        "o": int(officer_id),
        "s": int(subject_id),
        "exp": int(time.time()) + ttl,
        "j": uuid4().hex,
    }
    body = urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).rstrip(b"=")
    sig = hmac.new(_secret(), body, hashlib.sha256).digest()
    return body.decode() + "." + urlsafe_b64encode(sig).rstrip(b"=").decode()


def _pad(raw: str) -> bytes:
    return urlsafe_b64decode(raw + "=" * (-len(raw) % 4))


def parse_form_token(token: str) -> dict:
    if not token or "." not in token:
        raise ValueError("Invalid form link")
    body, sig = token.split(".", 1)
    expected = hmac.new(_secret(), body.encode(), hashlib.sha256).digest()
    got = _pad(sig)
    if not hmac.compare_digest(expected, got):
        raise ValueError("This form link is not valid")
    payload = json.loads(_pad(body))
    if int(payload.get("exp") or 0) < time.time():
        raise ValueError("This form link has expired. Run the Discord command again.")
    return payload
