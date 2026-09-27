# Container startup (GitHub deploy)

Many panels **hang forever on `git clone`** (no TTY, blocked git, or private repo without token). Use **`start.sh`**, which downloads a **tarball with curl** instead.

## Panel environment variables

| Variable | Required | Notes |
|----------|----------|--------|
| `DISCORD_TOKEN` | Yes | Bot token (or in `.env`) |
| `DATABASE_URL` | Yes | PostgreSQL URL (or in `.env`) |
| `GITHUB_TOKEN` | **Private repo** | PAT with `repo` scope — set in **panel**, not only `.env` (clone runs before `.env` exists) |
| `GITHUB_REPO` | No | Default `hamburger445/VSRP-Bot` |
| `GITHUB_BRANCH` | No | Default `main` |
| `REQUIREMENTS_FILE` | No | Default `requirements.txt` |

Upload **`config.yaml`** and **`.env`** once via SFTP. Restarts **keep** existing `.env` and `config.yaml` when updating code.

---

## Recommended startup command

Set the panel **Startup** to:

```bash
cd /home/container && bash panel-start.sh
```

**First boot:** the server has no `panel-start.sh` yet. Use this **once**, then switch to the line above:

```bash
cd /home/container && curl -fSL --connect-timeout 20 --max-time 300 -H "Authorization: Bearer ${GITHUB_TOKEN}" -L "https://api.github.com/repos/hamburger445/VSRP-Bot/tarball/main" -o /tmp/v.tar.gz && tar -xzf /tmp/v.tar.gz -C /tmp && cp -a $(find /tmp -mindepth 1 -maxdepth 1 -type d | head -1)/panel-start.sh . && bash panel-start.sh
```

Or paste the full one-liner (same as `panel-start.sh` logic):

```bash
cd /home/container && bash -c 'export GIT_TERMINAL_PROMPT=0; R="${GITHUB_REPO:-hamburger445/VSRP-Bot}"; B="${GITHUB_BRANCH:-main}"; T=$(mktemp -d); A="$T/a.tar.gz"; curl -fSL --connect-timeout 20 --max-time 300 -H "Authorization: Bearer ${GITHUB_TOKEN}" -L "https://api.github.com/repos/${R}/tarball/${B}" -o "$A" || exit 1; tar -xzf "$A" -C "$T"; S=$(find "$T" -mindepth 1 -maxdepth 1 -type d | head -1); for f in "$S"/*; do n=$(basename "$f"); [[ "$n" == ".env" || "$n" == "config.yaml" ]] && [[ -e "$n" ]] && continue; rm -rf "$n" 2>/dev/null; cp -a "$f" .; done; rm -rf "$T"; pip install -U --user -r "${REQUIREMENTS_FILE:-requirements.txt}"; exec /usr/local/bin/python /home/container/bot.py'
```

---

## Why `git clone` stuck

- **Private repo** without `GITHUB_TOKEN` in the **panel** → git waits for a password forever.
- **No `GIT_TERMINAL_PROMPT=0`** → same hang.
- Some hosts block or throttle **git://** or long git HTTPS.

`start.sh` uses **curl + tarball** (20s connect, 300s max by default) and prints a clear error if download fails.

---

## Optional: git updates

If you prefer git after the first curl sync, set `USE_GIT_SYNC=1` and keep a `.git` folder (advanced). Default is tarball every start.
