#!/usr/bin/env bash
# Container startup — download code from GitHub (curl tarball), install deps, run bot.
# Avoids "git clone" hanging when the panel has no TTY or blocks git.
set -euo pipefail

cd /home/container

GITHUB_REPO="${GITHUB_REPO:-hamburger445/VSRP-Bot}"
GITHUB_BRANCH="${GITHUB_BRANCH:-main}"
REQUIREMENTS_FILE="${REQUIREMENTS_FILE:-requirements.txt}"
PYTHON="${PYTHON:-/usr/local/bin/python}"
SYNC_TIMEOUT="${SYNC_TIMEOUT:-300}"

export GIT_TERMINAL_PROMPT=0

_preserve=(.env config.yaml)

_should_preserve() {
  local name="$1"
  for p in "${_preserve[@]}"; do
    [[ "$name" == "$p" ]] && return 0
  done
  return 1
}

_download_archive() {
  local dest="$1"
  local repo="$2"
  local branch="$3"

  if command -v curl >/dev/null 2>&1; then
    if [[ -n "${GITHUB_TOKEN:-}" ]]; then
      echo "[GitHub] Downloading (authenticated) ${repo} @ ${branch}..."
      curl -fSL --connect-timeout 20 --max-time "${SYNC_TIMEOUT}" \
        -H "Authorization: Bearer ${GITHUB_TOKEN}" \
        -H "Accept: application/vnd.github+json" \
        -H "X-GitHub-Api-Version: 2022-11-28" \
        -L "https://api.github.com/repos/${repo}/tarball/${branch}" \
        -o "${dest}"
      return 0
    fi
    echo "[GitHub] Downloading (public) ${repo} @ ${branch}..."
    curl -fSL --connect-timeout 20 --max-time "${SYNC_TIMEOUT}" \
      -L "https://github.com/${repo}/archive/refs/heads/${branch}.tar.gz" \
      -o "${dest}"
    return 0
  fi

  if command -v wget >/dev/null 2>&1; then
    if [[ -n "${GITHUB_TOKEN:-}" ]]; then
      echo "[GitHub] Downloading via wget (authenticated)..."
      wget --timeout=20 --tries=2 --header="Authorization: Bearer ${GITHUB_TOKEN}" \
        -O "${dest}" "https://api.github.com/repos/${repo}/tarball/${branch}"
      return 0
    fi
    wget --timeout=20 --tries=2 -O "${dest}" \
      "https://github.com/${repo}/archive/refs/heads/${branch}.tar.gz"
    return 0
  fi

  echo "[GitHub] ERROR: curl or wget required."
  return 1
}

_sync_from_github() {
  local tmp archive src name

  tmp="$(mktemp -d)"
  archive="${tmp}/repo.tar.gz"

  if ! _download_archive "${archive}" "${GITHUB_REPO}" "${GITHUB_BRANCH}"; then
    rm -rf "${tmp}"
    echo "[GitHub] Download failed."
    echo "  - If the repo is private, set GITHUB_TOKEN in the panel (not only in .env)."
    echo "  - Check outbound HTTPS to github.com from this host."
    exit 1
  fi

  echo "[GitHub] Extracting..."
  tar -xzf "${archive}" -C "${tmp}"
  src="$(find "${tmp}" -mindepth 1 -maxdepth 1 -type d | head -1)"
  if [[ -z "${src}" || ! -d "${src}" ]]; then
    echo "[GitHub] ERROR: archive layout unexpected."
    rm -rf "${tmp}"
    exit 1
  fi

  shopt -s dotglob nullglob
  for item in "${src}"/*; do
    name="$(basename "${item}")"
    if _should_preserve "${name}" && [[ -e "/home/container/${name}" ]]; then
      echo "[GitHub] Keeping existing ${name}"
      continue
    fi
    rm -rf "${name}" 2>/dev/null || true
    cp -a "${item}" .
  done
  shopt -u dotglob nullglob 2>/dev/null || true
  rm -rf "${tmp}"
  echo "[GitHub] Sync complete."
}

_git_sync() {
  local url
  if [[ -n "${GITHUB_TOKEN:-}" ]]; then
    url="https://hamburger445:${GITHUB_TOKEN}@github.com/${GITHUB_REPO}.git"
  else
    url="https://github.com/${GITHUB_REPO}.git"
  fi
  echo "[GitHub] git fetch (fallback)..."
  if command -v timeout >/dev/null 2>&1; then
    timeout 90 git fetch --depth 1 origin "${GITHUB_BRANCH}"
    git reset --hard "origin/${GITHUB_BRANCH}"
  else
    git fetch --depth 1 origin "${GITHUB_BRANCH}"
    git reset --hard "origin/${GITHUB_BRANCH}"
  fi
}

if [[ "${USE_GIT_SYNC:-0}" == "1" ]] && [[ -d .git ]]; then
  _git_sync
else
  _sync_from_github
fi

if [[ -f "/home/container/${REQUIREMENTS_FILE}" ]]; then
  echo "[Python] Installing requirements..."
  pip install -U --user -r "/home/container/${REQUIREMENTS_FILE}"
fi

if [[ ! -f /home/container/bot.py ]]; then
  echo "[Bot] ERROR: bot.py missing after sync."
  exit 1
fi

if [[ ! -f /home/container/config.yaml ]]; then
  echo "[Bot] WARNING: config.yaml missing — upload it or copy from config.example.yaml."
fi

echo "[Bot] Starting (slash commands sync automatically on startup)..."
exec "${PYTHON}" /home/container/bot.py
