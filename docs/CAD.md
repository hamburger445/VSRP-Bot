# VSRP CAD / MDT

Browser CAD sharing the bot’s PostgreSQL database. The Discord bot on the game panel remains the **only** gateway process.

## Local development

```powershell
pip install -r requirements.txt -r requirements-cad.txt
uvicorn cad.app:app --reload --port 8000
```

```powershell
cd web
npm install
npm run dev
```

```powershell
python bot.py
```

- CAD UI (Vite): http://127.0.0.1:5173 (proxies `/api` to port 8000)
- API docs: http://127.0.0.1:8000/api/docs
- Health: http://127.0.0.1:8000/api/health

```powershell
pytest
```

## Discord application (same app as the bot)

1. Developer Portal → OAuth2 → add redirect `https://YOUR-CAD/api/auth/discord/callback` (and `http://127.0.0.1:8000/api/auth/discord/callback` for local).
2. Create OAuth2 client secret.
3. Scopes used: `identify`, `guilds.members.read`.
4. Bot stays in the main / FD / PD guilds. Do not create a second bot.

## Environment

See `.env.example`. Required for CAD:

- `DATABASE_URL` — same Neon/Postgres URL as the bot
- `CAD_DATABASE_DIRECT_URL` — Neon **direct** (non-pooler) URL for LISTEN/NOTIFY
- `DISCORD_CLIENT_ID` / `DISCORD_CLIENT_SECRET`
- `CAD_PUBLIC_URL` — public origin of the CAD (no trailing slash)
- `CAD_SESSION_SECRET`, `CAD_SECRETS_KEY`, `CAD_CODE_PEPPER`
- `CAD_MAIN_GUILD_ID` (and FD/PD) if `config.yaml` is not on the host

`DISCORD_TOKEN` stays on the **panel bot**, not on Render.

Generate a Fernet key:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Render

**Do not put pip and uvicorn in the same Start Command.** `--reload` is local-only. Render ran this and crashed:

```
pip install -r requirements.txt -r requirements-cad.txt uvicorn cad.app:app --reload --port 8000
```

### If the service is native Python (current log)

Dashboard → Settings:

| Field | Value |
|-------|--------|
| **Build Command** | `pip install -r requirements.txt -r requirements-cad.txt` |
| **Start Command** | `uvicorn cad.app:app --host 0.0.0.0 --port $PORT` |
| Health check | `/api/health` |

No `--reload`. No `pip install` in Start Command. `$PORT` is required (do not hardcode `8000`).

The UI will be missing on a Python service (no Node/npm). Use Docker for the full website.

### Docker (UI + API)

Settings → Runtime **Docker**, Dockerfile `./Dockerfile`. The image already runs uvicorn on `$PORT`.

Required env: `DATABASE_URL` (same Postgres as the bot). Do **not** run `python bot.py` on this service while the panel bot is online.

## Discord commands (after `/sync`)

- `/cad` — website link
- `/cad-status` — your unit
- `/cad-panic` — panic flag + channel alert

Citations and warrants created in CAD write the same `citation_tickets` / `warrants` rows as Discord. Channel posts and DMs wait in `cad_outbox` until the bot delivers them.

## Limitations

- No live GPS (manual map positions only)
- No real radio
- Shift start/stop and application approve remain Discord
- Vehicle registration remains `/registervehicle`
- If the panel bot is down, CAD still writes the database; Discord notifications queue
