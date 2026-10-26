"""CAD environment and guild/role configuration. Env wins; config.yaml is local fallback."""

from __future__ import annotations

import os
from functools import lru_cache


def _int_env(name: str, default: int = 0) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _csv_ints(name: str) -> list[int]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return []
    out: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return out


class CadSettings:
    def __init__(self) -> None:
        self.app_env = os.getenv("APP_ENV", "development")
        self.log_level = os.getenv("LOG_LEVEL", "INFO")
        self.database_url = os.getenv("DATABASE_URL", "").strip()
        self.database_direct_url = os.getenv("CAD_DATABASE_DIRECT_URL", "").strip()
        self.public_url = os.getenv("CAD_PUBLIC_URL", "http://127.0.0.1:8000").rstrip("/")
        self.frontend_url = os.getenv("FRONTEND_URL", self.public_url).rstrip("/")
        self.discord_client_id = os.getenv("DISCORD_CLIENT_ID", "").strip()
        self.discord_client_secret = os.getenv("DISCORD_CLIENT_SECRET", "").strip()
        self.discord_redirect_uri = os.getenv(
            "DISCORD_REDIRECT_URI",
            f"{self.public_url}/api/auth/discord/callback",
        ).strip()
        self.session_secret = os.getenv("CAD_SESSION_SECRET") or os.getenv("SESSION_SECRET") or ""
        self.secrets_key = os.getenv("CAD_SECRETS_KEY", "")
        self.code_pepper = os.getenv("CAD_CODE_PEPPER", "")
        self.require_dm_verify = os.getenv("CAD_REQUIRE_DM_VERIFY", "1").strip() not in {
            "0",
            "false",
            "False",
            "no",
        }
        self.session_hours = 12
        self.session_max_days = 7
        self._load_guilds()

    def _yaml_fallback(self) -> dict:
        try:
            from utils.core import load_config

            return load_config()
        except Exception:
            return {}

    def _load_guilds(self) -> None:
        cfg = self._yaml_fallback()
        servers = cfg.get("servers") or {}
        roles = cfg.get("roles") or {}
        depts = roles.get("departments") or {}
        owners = cfg.get("owners") or []
        apps = cfg.get("applications") or {}

        self.main_guild_id = _int_env("CAD_MAIN_GUILD_ID", int(servers.get("main", {}).get("id") or cfg.get("guild", {}).get("id") or 0))
        self.fd_guild_id = _int_env("CAD_FD_GUILD_ID", int(servers.get("fd", {}).get("id") or 0))
        self.pd_guild_id = _int_env("CAD_PD_GUILD_ID", int(servers.get("pd", {}).get("id") or 0))
        self.owners = _csv_ints("CAD_OWNER_IDS") or [int(x) for x in owners if x]
        self.department_roles: dict[str, int] = {}
        for key in ("fire1", "fire2", "fire3", "police1", "police2", "ems", "dot", "dispatch"):
            env_name = f"CAD_ROLE_{key.upper()}"
            self.department_roles[key] = _int_env(env_name, int(depts.get(key) or 0))
        reviewer = apps.get("reviewer_role_ids") or []
        extra = apps.get("reviewer_role_id")
        ids = list(reviewer)
        if extra:
            ids.append(extra)
        self.reviewer_role_ids = _csv_ints("CAD_REVIEWER_ROLE_IDS") or [int(x) for x in ids if x]
        cad_cfg = cfg.get("cad") or {}
        if cad_cfg.get("public_url") and os.getenv("CAD_PUBLIC_URL") is None:
            self.public_url = str(cad_cfg["public_url"]).rstrip("/")
        if "require_dm_verify" in cad_cfg and os.getenv("CAD_REQUIRE_DM_VERIFY") is None:
            self.require_dm_verify = bool(cad_cfg["require_dm_verify"])

    def oauth_configured(self) -> bool:
        return bool(self.discord_client_id and self.discord_client_secret and self.main_guild_id)

    def health_errors(self) -> list[str]:
        errors: list[str] = []
        if not self.database_url:
            errors.append("DATABASE_URL is missing")
        if not self.main_guild_id:
            errors.append("CAD_MAIN_GUILD_ID (or config servers.main.id) is missing")
        return errors


@lru_cache
def get_settings() -> CadSettings:
    return CadSettings()
