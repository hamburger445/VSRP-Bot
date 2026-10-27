#!/usr/bin/env bash
# WispByte / Pterodactyl startup:
#   cd /home/container && bash panel-start.sh
# Pulls GitHub on each boot. If the bot exits 42 (new commit), pull again and restart.
set -euo pipefail
cd /home/container

export GIT_TERMINAL_PROMPT=0
R="${GITHUB_REPO:-hamburger445/VSRP-Bot}"
B="${GITHUB_BRANCH:-main}"
REQ="${REQUIREMENTS_FILE:-requirements.txt}"
PY="${PYTHON:-/usr/local/bin/python}"
RESTART_CODE="${DEPLOY_EXIT_CODE:-42}"

T="$(mktemp -d)"
A="$T/a.tar.gz"
cleanup_tmp() { rm -rf "$T"; }
trap cleanup_tmp EXIT

_download_auth() {
  echo "[GitHub] Downloading ${R} (${B}) with token..."
  curl -fSL --connect-timeout 20 --max-time 300 \
    -H "Authorization: Bearer ${GITHUB_TOKEN}" \
    -H "Accept: application/vnd.github+json" \
    -L "https://api.github.com/repos/${R}/tarball/${B}" \
    -o "$A"
}

_download_public() {
  echo "[GitHub] Downloading ${R} (${B}) (public archive)..."
  curl -fSL --connect-timeout 20 --max-time 300 \
    -L "https://github.com/${R}/archive/refs/heads/${B}.tar.gz" \
    -o "$A"
}

_fetch_sha() {
  "$PY" - <<'PY' || true
import json, os, urllib.request
repo = os.environ.get("GITHUB_REPO", "hamburger445/VSRP-Bot")
branch = os.environ.get("GITHUB_BRANCH", "main")
token = os.environ.get("GITHUB_TOKEN", "")
url = f"https://api.github.com/repos/{repo}/commits/{branch}"
headers = {"Accept": "application/vnd.github+json", "User-Agent": "vsrp-panel-start"}
if token:
    headers["Authorization"] = f"Bearer {token}"
req = urllib.request.Request(url, headers=headers)
try:
    with urllib.request.urlopen(req, timeout=20) as resp:
        sha = json.load(resp).get("sha") or ""
except Exception as exc:
    print("[GitHub] Could not read commit SHA:", exc)
    sha = ""
open(".deploy-sha", "w", encoding="utf-8").write(sha)
print("[GitHub] Running commit", sha[:12] if sha else "(unknown)")
PY
}

_sync() {
  rm -f "$A"
  if _download_public; then
    echo "[GitHub] Public archive OK."
  elif [[ -n "${GITHUB_TOKEN:-}" ]]; then
    echo "[GitHub] Public archive failed — trying token download..."
    _download_auth
  else
    echo "[GitHub] ERROR: public download failed and GITHUB_TOKEN is empty."
    echo "         Do not send Authorization: Bearer with an empty token (GitHub returns 401)."
    echo "         Add a GitHub PAT in panel Variables, or make the repo public."
    exit 1
  fi

  echo "[GitHub] Extracting..."
  tar -xzf "$A" -C "$T"
  local S
  S="$(find "$T" -mindepth 1 -maxdepth 1 -type d | head -1)"
  if [[ -z "$S" || ! -d "$S" ]]; then
    echo "[GitHub] ERROR: bad archive."
    exit 1
  fi

  echo "[GitHub] Installing files (keeping .env and config.yaml if present)..."
  shopt -s dotglob nullglob
  local f n
  for f in "$S"/*; do
    n="$(basename "$f")"
    if [[ "$n" == ".env" || "$n" == "config.yaml" ]] && [[ -e "$n" ]]; then
      echo "  skip $n"
      continue
    fi
    rm -rf "$n" 2>/dev/null || true
    cp -a "$f" .
  done
  shopt -u dotglob nullglob 2>/dev/null || true

  if [[ ! -f "$REQ" ]]; then
    echo "[Python] ERROR: ${REQ} not found."
    exit 1
  fi

  echo "[Python] pip install -r ${REQ}..."
  pip install -U --user -r "$REQ"

  if [[ ! -f bot.py ]]; then
    echo "[Bot] ERROR: bot.py missing."
    exit 1
  fi

  if [[ ! -f config.yaml ]] && [[ -f config.example.yaml ]]; then
    cp config.example.yaml config.yaml
    echo "[Config] Created config.yaml from config.example.yaml"
  fi

  _fetch_sha
}

_load_dotenv() {
  if [[ ! -f /home/container/.env ]]; then
    return 0
  fi
  echo "[Env] Loading /home/container/.env"
  set -a
  # shellcheck disable=SC1091
  source /home/container/.env || true
  set +a
}

_require_discord_token() {
  if [[ -n "${DISCORD_TOKEN:-}" && "${DISCORD_TOKEN}" != "your_bot_token_here" ]]; then
    return 0
  fi
  if [[ -n "${BOT_TOKEN:-}" ]]; then
    export DISCORD_TOKEN="$BOT_TOKEN"
    return 0
  fi
  echo "[Bot] ERROR: DISCORD_TOKEN is missing."
  echo "  WispByte → your server → Startup / Variables → create DISCORD_TOKEN"
  echo "  Paste the Discord bot token (Developer Portal → Bot → Reset Token)."
  echo "  Or File Manager → New File named .env with a line: DISCORD_TOKEN=your_token"
  echo "  Then click Start. Do not put the Discord token in GITHUB_TOKEN."
  exit 1
}

export GITHUB_REPO="$R"
export GITHUB_BRANCH="$B"

while true; do
  T="$(mktemp -d)"
  A="$T/a.tar.gz"
  _sync
  _load_dotenv
  _require_discord_token
  echo "[Bot] Starting (slash commands sync on startup; deploy watch polls GitHub)..."
  set +e
  "$PY" /home/container/bot.py
  code=$?
  set -e
  rm -rf "$T"
  if [[ "$code" -eq "$RESTART_CODE" ]]; then
    echo "[Deploy] GitHub changed — pulling latest and restarting bot..."
    sleep 2
    continue
  fi
  echo "[Bot] Exited with code ${code}"
  exit "$code"
done
