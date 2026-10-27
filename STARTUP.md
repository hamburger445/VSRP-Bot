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
| `DEPLOY_WATCH` | No | Default **on** on WispByte (`1`). Set `0` to disable |
| `DEPLOY_POLL_SECONDS` | No | Default `20` — new GitHub commit exits 42 and the startup script re-pulls |
| `PYTHON` | No | Default `/usr/local/bin/python` |

You must give the bot a Discord token **on the panel**. The GitHub download does not include `.env`.

**Option A (easiest):** WispByte → server → **Startup** or **Variables** → add:

- `DISCORD_TOKEN` = your bot token from Discord Developer Portal → Bot

**Option B:** File Manager → New File `.env` in `/home/container` with:

```
DISCORD_TOKEN=paste_token_here
GITHUB_TOKEN=your_github_pat
BOT_DATABASE=github
```

Upload **`config.yaml`** the same way (or let the script copy `config.example.yaml` once, then edit IDs). Restarts keep `.env` and `config.yaml`.

If `config.yaml` is missing, the script copies `config.example.yaml`.

## 3. Startup command

Use this as the **Startup** command on every boot (including the first). It downloads from the **public** GitHub archive (no token header). That avoids GitHub **401**, which happens if you send `Authorization: Bearer` with an empty or invalid `GITHUB_TOKEN`.

```bash
cd /home/container && curl -fSL --connect-timeout 20 --max-time 300 -L "https://github.com/hamburger445/VSRP-Bot/archive/refs/heads/main.tar.gz" -o /tmp/v.tar.gz && tar -xzf /tmp/v.tar.gz -C /tmp && cp -a "$(find /tmp -mindepth 1 -maxdepth 1 -type d | head -1)"/panel-start.sh . && bash panel-start.sh
```

After the first successful start you *may* shorten it to `cd /home/container && bash panel-start.sh`. The longer command is fine to leave forever.

If the repo is **private**, do not send a Bearer header unless `GITHUB_TOKEN` is a real GitHub PAT in **panel Variables**. Empty `Bearer ` is always 401.

## 4. How a deploy works

1. You push to `main` on GitHub.
2. Within about 20 seconds the running bot sees a new commit SHA.
3. Console shows `New GitHub commit … — exiting 42`.
4. `panel-start.sh` is still running: it downloads the new tarball, runs `pip install`, and starts `bot.py` again.
5. The WispByte server stays **online** through that reload.

If the log says `DISCORD_TOKEN is missing`, the code pull succeeded. Add the token (section 2) and click **Start**. Wait 60 seconds if auto-restart aborted after a crash loop.

A crash (any exit code other than 42) stops the server so you can read the log. Enable the panel **restart on crash** option if you also want those to come back by themselves.

Local `python bot.py` does not auto-reload unless you set `DEPLOY_WATCH=1`.

## 5. 401 from GitHub

`curl: (22) The requested URL returned error: 401` on the API tarball means GitHub rejected the **Authorization** header.

- This repo is public. Use the **public** archive URL in section 3 (no `-H Authorization`).
- `Authorization: Bearer ${GITHUB_TOKEN}` with an empty variable still sends `Bearer ` and GitHub returns 401.
- A Discord bot token is not a GitHub PAT. Put a GitHub PAT in `GITHUB_TOKEN` only for the JSON database / private code.

## 6. Paper citation / warrant website

`/citation`, `/warrant`, and `/serve-warrant` send the officer a **Open paper form** link. They fill a PDF-style page in the browser. Submit saves the record and posts the filled PDF into the Discord citations/warrants channel.

WispByte must allocate an **HTTP port** the public can reach:

| Variable | Notes |
|----------|--------|
| `SERVER_PORT` or `FORM_PORT` | Port the bot binds (default `8080`) |
| `PUBLIC_FORM_URL` | Full URL officers open, e.g. `http://YOUR_ALLOCATION_IP:PORT` |
| `SERVER_IP` | Used only if `PUBLIC_FORM_URL` is empty |

Without `PUBLIC_FORM_URL` (or `SERVER_IP`), officers still fill the forms inside Discord.

Check `GET /health` on that URL after start. Discord Link buttons cannot use `localhost` unless the officer is on the same machine.

## Why not `git clone`

A private repo without a panel token waits for a password forever. `panel-start.sh` uses **curl + tarball** with timeouts.
