"""Discord OAuth2, sessions, DM verification, TOTP."""

from __future__ import annotations

import json
import secrets
import uuid
from datetime import timedelta
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from cad.db import audit, connection, enqueue_outbox
from cad.deps import COOKIE_NAME, CSRF_COOKIE, current_session
from cad.security import (
    encrypt_secret,
    generate_dm_code,
    hash_dm_code,
    hash_token,
    new_session_token,
    session_expiry,
    utcnow,
)
from cad.settings import get_settings, origin_from_request
from utils.cad_permissions import needs_dm_verify

router = APIRouter(prefix="/api/auth", tags=["auth"])

DISCORD_API = "https://discord.com/api/v10"
OAUTH_AUTHORIZE = "https://discord.com/api/oauth2/authorize"
OAUTH_TOKEN = "https://discord.com/api/oauth2/token"


class CodeBody(BaseModel):
    code: str


class TotpBody(BaseModel):
    code: str


def _cookie_kwargs(origin: str, settings) -> dict:
    secure = origin.startswith("https://")
    return {
        "httponly": True,
        "secure": secure,
        "samesite": "lax",
        "path": "/",
        "max_age": settings.session_max_days * 86400,
    }


@router.get("/discord/login")
async def discord_login(request: Request):
    settings = get_settings()
    if not settings.oauth_configured():
        raise HTTPException(503, "Discord OAuth is not configured")
    origin = origin_from_request(request)
    redirect_uri = f"{origin}/api/auth/discord/callback"
    state = secrets.token_urlsafe(24)
    secure = origin.startswith("https://")
    params = {
        "client_id": settings.discord_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "identify guilds.members.read",
        "state": state,
        "prompt": "consent",
    }
    redir = RedirectResponse(f"{OAUTH_AUTHORIZE}?{urlencode(params)}")
    redir.set_cookie(
        "cad_oauth_state",
        state,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
        max_age=600,
    )
    return redir


async def _guild_member(client: httpx.AsyncClient, token: str, guild_id: int) -> dict | None:
    if not guild_id:
        return None
    r = await client.get(
        f"{DISCORD_API}/users/@me/guilds/{guild_id}/member",
        headers={"Authorization": f"Bearer {token}"},
    )
    if r.status_code == 404:
        return None
    if r.status_code >= 400:
        return None
    return r.json()


@router.get("/discord/callback")
async def discord_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    cad_oauth_state: str | None = Cookie(default=None),
):
    settings = get_settings()
    origin = origin_from_request(request)
    redirect_uri = f"{origin}/api/auth/discord/callback"
    if not code or not state or state != cad_oauth_state:
        raise HTTPException(400, "Invalid OAuth state")
    async with httpx.AsyncClient(timeout=20) as client:
        token_res = await client.post(
            OAUTH_TOKEN,
            data={
                "client_id": settings.discord_client_id,
                "client_secret": settings.discord_client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if token_res.status_code >= 400:
            raise HTTPException(401, "Discord token exchange failed")
        tokens = token_res.json()
        access = tokens.get("access_token")
        me = await client.get(
            f"{DISCORD_API}/users/@me",
            headers={"Authorization": f"Bearer {access}"},
        )
        if me.status_code >= 400:
            raise HTTPException(401, "Could not load Discord profile")
        profile = me.json()
        uid = int(profile["id"])
        main = await _guild_member(client, access, settings.main_guild_id)
        if not main:
            raise HTTPException(403, "You must be a member of the main Discord server")
        fd = await _guild_member(client, access, settings.fd_guild_id)
        pd = await _guild_member(client, access, settings.pd_guild_id)

    main_roles = [int(x) for x in (main.get("roles") or [])]
    fd_roles = [int(x) for x in (fd.get("roles") or [])] if fd else []
    pd_roles = [int(x) for x in (pd.get("roles") or [])] if pd else []

    raw_token = new_session_token()
    csrf = secrets.token_urlsafe(16)
    session_id = uuid.uuid4()
    async with connection() as conn:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO users (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING",
                uid,
            )
            await conn.execute(
                """
                INSERT INTO cad_users (
                    discord_user_id, username, global_name, avatar_hash, last_login_at,
                    last_role_sync_at, main_roles, fd_roles, pd_roles, in_main_guild, status
                )
                VALUES ($1,$2,$3,$4,NOW(),NOW(),$5,$6,$7,TRUE,'active')
                ON CONFLICT (discord_user_id) DO UPDATE SET
                    username = EXCLUDED.username,
                    global_name = EXCLUDED.global_name,
                    avatar_hash = EXCLUDED.avatar_hash,
                    last_login_at = NOW(),
                    last_role_sync_at = NOW(),
                    main_roles = EXCLUDED.main_roles,
                    fd_roles = EXCLUDED.fd_roles,
                    pd_roles = EXCLUDED.pd_roles,
                    in_main_guild = TRUE
                """,
                uid,
                profile.get("username"),
                profile.get("global_name"),
                profile.get("avatar"),
                json.dumps(main_roles),
                json.dumps(fd_roles),
                json.dumps(pd_roles),
            )
            expires_at = utcnow() + timedelta(seconds=int(tokens.get("expires_in") or 604800))
            await conn.execute(
                """
                INSERT INTO cad_oauth_tokens (discord_user_id, access_token_enc, refresh_token_enc, access_expires_at, scopes)
                VALUES ($1,$2,$3,$4,$5)
                ON CONFLICT (discord_user_id) DO UPDATE SET
                    access_token_enc = EXCLUDED.access_token_enc,
                    refresh_token_enc = EXCLUDED.refresh_token_enc,
                    access_expires_at = EXCLUDED.access_expires_at,
                    scopes = EXCLUDED.scopes,
                    updated_at = NOW()
                """,
                uid,
                encrypt_secret(access),
                encrypt_secret(tokens.get("refresh_token") or ""),
                expires_at,
                tokens.get("scope", "identify guilds.members.read"),
            )
            await conn.execute(
                """
                INSERT INTO cad_sessions (id, token_hash, discord_user_id, expires_at)
                VALUES ($1,$2,$3,$4)
                """,
                session_id,
                hash_token(raw_token),
                uid,
                session_expiry(),
            )
            await audit(conn, uid, "login", "session", str(session_id), {"via": "oauth2"})

    dest = f"{origin}/"
    # 200 HTML, not 302: browsers often drop Set-Cookie on OAuth redirect responses.
    resp = HTMLResponse(
        "<!doctype html><html><head>"
        f'<meta http-equiv="refresh" content="0;url={dest}">'
        "</head><body>Signing in…"
        f"<script>location.replace({json.dumps(dest)});</script>"
        "</body></html>",
        headers={"Cache-Control": "no-store"},
    )
    kw = _cookie_kwargs(origin, settings)
    resp.set_cookie(COOKIE_NAME, raw_token, **kw)
    resp.set_cookie(
        CSRF_COOKIE,
        csrf,
        httponly=False,
        secure=kw["secure"],
        samesite="lax",
        path="/",
        max_age=kw["max_age"],
    )
    resp.delete_cookie(
        "cad_oauth_state",
        path="/",
        secure=kw["secure"],
        samesite="lax",
    )
    return resp


@router.get("/me")
async def me(user: dict = Depends(current_session)):
    settings = get_settings()
    return {
        **user,
        "require_dm_verify": settings.require_dm_verify,
        "public_url": settings.public_url,
    }


@router.post("/logout")
async def logout(
    response: Response,
    vsrp_cad: str | None = Cookie(default=None),
):
    if vsrp_cad:
        async with connection() as conn:
            await conn.execute(
                "UPDATE cad_sessions SET revoked_at = NOW() WHERE token_hash = $1",
                hash_token(vsrp_cad),
            )
    response.delete_cookie(COOKIE_NAME, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"ok": True}


@router.post("/dm-code/request")
async def request_dm_code(user: dict = Depends(current_session)):
    uid = int(user["discord_user_id"])
    code = generate_dm_code()
    async with connection() as conn:
        await conn.execute(
            "UPDATE cad_dm_codes SET consumed_at = NOW() WHERE discord_user_id = $1 AND consumed_at IS NULL",
            uid,
        )
        await conn.execute(
            """
            INSERT INTO cad_dm_codes (discord_user_id, code_hash, expires_at)
            VALUES ($1, $2, NOW() + INTERVAL '10 minutes')
            """,
            uid,
            hash_dm_code(code),
        )
        await enqueue_outbox(conn, "dm_code", {"user_id": uid, "code": code})
        await audit(conn, uid, "dm_code_requested", "cad_users", uid, {})
    return {"ok": True, "expires_seconds": 600}


@router.post("/dm-code/verify")
async def verify_dm_code(body: CodeBody, user: dict = Depends(current_session)):
    uid = int(user["discord_user_id"])
    submitted = (body.code or "").strip()
    async with connection() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, code_hash, expires_at, attempts
            FROM cad_dm_codes
            WHERE discord_user_id = $1 AND consumed_at IS NULL
            ORDER BY id DESC LIMIT 1
            """,
            uid,
        )
        if not row:
            raise HTTPException(400, "No verification code pending")
        if row["expires_at"] < utcnow():
            raise HTTPException(400, "Code expired")
        if row["attempts"] >= 5:
            await conn.execute("UPDATE cad_dm_codes SET consumed_at = NOW() WHERE id = $1", row["id"])
            raise HTTPException(429, "Too many attempts")
        if hash_dm_code(submitted) != row["code_hash"]:
            await conn.execute(
                "UPDATE cad_dm_codes SET attempts = attempts + 1 WHERE id = $1",
                row["id"],
            )
            raise HTTPException(400, "Invalid code")
        await conn.execute(
            "UPDATE cad_dm_codes SET consumed_at = NOW() WHERE id = $1",
            row["id"],
        )
        await conn.execute(
            "UPDATE cad_users SET dm_verified_at = NOW() WHERE discord_user_id = $1",
            uid,
        )
        await audit(conn, uid, "dm_verified", "cad_users", uid, {})
    return {"ok": True}


@router.post("/totp/setup")
async def totp_setup(user: dict = Depends(current_session)):
    import pyotp

    secret = pyotp.random_base32()
    uid = int(user["discord_user_id"])
    async with connection() as conn:
        await conn.execute(
            "UPDATE cad_users SET totp_secret_enc = $1, totp_enabled = FALSE WHERE discord_user_id = $2",
            encrypt_secret(secret),
            uid,
        )
    uri = pyotp.TOTP(secret).provisioning_uri(
        name=user.get("username") or str(uid),
        issuer_name="VSRP CAD",
    )
    return {"otpauth_url": uri}


@router.post("/totp/verify")
async def totp_verify(body: TotpBody, user: dict = Depends(current_session)):
    import pyotp

    from cad.security import decrypt_secret

    uid = int(user["discord_user_id"])
    async with connection() as conn:
        row = await conn.fetchrow(
            "SELECT totp_secret_enc FROM cad_users WHERE discord_user_id = $1",
            uid,
        )
        if not row or not row["totp_secret_enc"]:
            raise HTTPException(400, "TOTP is not set up")
        secret = decrypt_secret(row["totp_secret_enc"])
        if not pyotp.TOTP(secret).verify(body.code, valid_window=1):
            raise HTTPException(400, "Invalid authenticator code")
        await conn.execute(
            "UPDATE cad_users SET totp_enabled = TRUE WHERE discord_user_id = $1",
            uid,
        )
    return {"ok": True}
