#!/usr/bin/env bash
# Paste in panel as: cd /home/container && bash panel-start.sh
set -euo pipefail
cd /home/container

export GIT_TERMINAL_PROMPT=0
R="${GITHUB_REPO:-hamburger445/VSRP-Bot}"
B="${GITHUB_BRANCH:-main}"
REQ="${REQUIREMENTS_FILE:-requirements.txt}"
PY="${PYTHON:-/usr/local/bin/python}"

if [[ -z "${GITHUB_TOKEN:-}" ]]; then
  echo "[GitHub] WARNING: GITHUB_TOKEN is empty — private repos will fail."
  echo "         Set GITHUB_TOKEN in the panel Variables tab."
fi

T="$(mktemp -d)"
A="$T/a.tar.gz"
trap 'rm -rf "$T"' EXIT

echo "[GitHub] Downloading ${R} (${B})..."
curl -fSL --connect-timeout 20 --max-time 300 \
  -H "Authorization: Bearer ${GITHUB_TOKEN:-}" \
  -H "Accept: application/vnd.github+json" \
  -L "https://api.github.com/repos/${R}/tarball/${B}" \
  -o "$A"

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

echo "[Python] pip install -r ${REQ}..."
pip install -U --user -r "$REQ"

echo "[Bot] Starting..."
exec "$PY" /home/container/bot.py
