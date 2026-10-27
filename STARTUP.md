# Host on WispByte (GitHub pull + auto-restart)

Run this bot on a **Python egg** (WispByte, Pterodactyl, or similar). Each start downloads the latest `main` tarball from GitHub. After the bot is online it checks GitHub about once a minute. When you push a new commit, the bot exits code **42**, the startup script pulls the new files, and the bot starts again. You do not need to click Restart for a normal code deploy.

Do **not** put `git clone` in the startup command. Panels hang on that.

## 1. Create the server

1. WispByte → create a server with a **Python** egg (3.11+ if you can choose).
2. The image needs `python`, `pip`, `curl`, `bash`, and `tar`.
3. Working directory is `/home/container`.

## 2. Panel variables

Set these under **Variables**. The GitHub pull runs **before** Python reads `.env`, so `GITHUB_TOKEN` must live in the panel.

| Variable | Required | Notes |
|----------|----------|--------|
| `DISCORD_TOKEN` | Yes | Bot token |
| `GITHUB_TOKEN` | Yes if the repo is private | PAT with `repo` (or `contents:read`). Also used for the JSON database. |
| `GITHUB_REPO` | No | Default `hamburger445/VSRP-Bot` |
| `GITHUB_BRANCH` | No | Default `main` |
| `BOT_DATABASE` | No | Default `github` |
| `GITHUB_DB_PATH` | No | Default `data/cad/db.json` |
| `GITHUB_DB_BRANCH` | No | Default `cad-data` (bot records, not code) |
| `DEPLOY_WATCH` | No | Auto-on when `.deploy-sha` exists; set `0` to disable |
| `DEPLOY_POLL_SECONDS` | No | Default `60` |
| `PYTHON` | No | Default `/usr/local/bin/python` |

Upload **`config.yaml`** and **`.env`** once via SFTP. Restarts keep those two files. New keys from `config.example.yaml` are merged in; existing values are not overwritten.

If `config.yaml` is missing, the script copies `config.example.yaml`.

## 3. Startup command

**Startup** field (use this after the first successful boot):

```bash
cd /home/container && bash panel-start.sh
```

**First boot only** — the disk is empty, so `panel-start.sh` is not there yet. Paste this, start once, then switch Startup back to the line above.

Private repo:

```bash
cd /home/container && curl -fSL --connect-timeout 20 --max-time 300 -H "Authorization: Bearer ${GITHUB_TOKEN}" -L "https://api.github.com/repos/hamburger445/VSRP-Bot/tarball/main" -o /tmp/v.tar.gz && tar -xzf /tmp/v.tar.gz -C /tmp && cp -a "$(find /tmp -mindepth 1 -maxdepth 1 -type d | head -1)"/panel-start.sh . && bash panel-start.sh
```

Public repo:

```bash
cd /home/container && curl -fSL --connect-timeout 20 --max-time 300 -L "https://github.com/hamburger445/VSRP-Bot/archive/refs/heads/main.tar.gz" -o /tmp/v.tar.gz && tar -xzf /tmp/v.tar.gz -C /tmp && cp -a "$(find /tmp -mindepth 1 -maxdepth 1 -type d | head -1)"/panel-start.sh . && bash panel-start.sh
```

## 4. How a deploy works

1. You push to `main` on GitHub.
2. Within about a minute the running bot sees a new commit SHA.
3. Console shows `New GitHub commit … — exiting 42`.
4. `panel-start.sh` is still running: it downloads the new tarball, runs `pip install`, and starts `bot.py` again.
5. The WispByte server stays **online** through that reload.

A crash (any exit code other than 42) stops the server so you can read the log. Enable the panel **restart on crash** option if you also want those to come back by themselves.

Local `python bot.py` does not auto-reload unless you set `DEPLOY_WATCH=1`.

## 5. 401 from GitHub

1. Put `GITHUB_TOKEN` in **panel Variables**.
2. Or make the repo public for the code tarball (you still need a token for the JSON database).
3. Regenerate the PAT if it was revoked.

## Why not `git clone`

A private repo without a panel token waits for a password forever. `panel-start.sh` uses **curl + tarball** with timeouts.
