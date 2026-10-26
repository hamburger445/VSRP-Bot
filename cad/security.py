"""Session tokens, encryption, DM code hashing."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet, InvalidToken

from cad.settings import get_settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def fernet() -> Fernet | None:
    key = get_settings().secrets_key.strip()
    if not key:
        return None
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception:
        digest = hashlib.sha256(key.encode()).digest()
        import base64

        return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plain: str) -> str:
    f = fernet()
    if not f:
        return plain
    return f.encrypt(plain.encode()).decode()


def decrypt_secret(blob: str) -> str:
    f = fernet()
    if not f:
        return blob
    try:
        return f.decrypt(blob.encode()).decode()
    except InvalidToken:
        return blob


def hash_dm_code(code: str) -> str:
    pepper = get_settings().code_pepper or get_settings().session_secret or "cad"
    return hmac.new(pepper.encode(), code.encode(), hashlib.sha256).hexdigest()


def generate_dm_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def session_expiry() -> datetime:
    s = get_settings()
    return utcnow() + timedelta(hours=s.session_hours)
