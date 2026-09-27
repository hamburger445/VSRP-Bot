#!/usr/bin/env bash
# Panel startup: cd /home/container && bash panel-start.sh
set -euo pipefail
cd /home/container

export GIT_TERMINAL_PROMPT=0
R="${GITHUB_REPO:-hamburger445/VSRP-Bot}"
B="${GITHUB_BRANCH:-main}"
REQ="${REQUIREMENTS_FILE:-requirements.txt}"
PY="${PYTHON:-/usr/local/bin/python}"

T="$(mktemp -d)"
A="$T/a.tar.gz"
trap 'rm -rf "$T"' EXIT

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

if [[ -n "${GITHUB_TOKEN:-}" ]]; then
  if ! _download_auth; then
    echo "[GitHub] Token download failed (401 = bad/expired token or wrong scopes)."
    echo "[GitHub] Trying public archive URL..."
    _download_public
  fi
else
  echo "[GitHub] GITHUB_TOKEN not set in panel — using public download."
  echo "         (Private repos: add GITHUB_TOKEN in panel Variables, not only .env)"
  _download_public
fi

echo "[GitHub] Extracting..."
tar -xzf "$A" -C "$T"
S="$(find "$T" -mindepth 1 -maxdepth 1 -type d | head -1)"
if [[ -z "$S" || ! -d "$S" ]]; then
  echo "[GitHub] ERROR: bad archive."
  exit 1
fi

echo "[GitHub] Installing files (keeping .env and config.yaml if present)..."
shopt -s dotglob nullglob
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

echo "[Bot] Starting (slash commands sync automatically on startup)..."
exec "$PY" /home/container/bot.py
