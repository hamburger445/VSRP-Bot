# VSRP Discord Bot

Python Discord bot for urban roleplay servers. Uses PostgreSQL (Neon) and interactive dashboards.

## Main Commands

| Command | Description |
|---------|-------------|
| `-ban` `-kick` `-mute` `-unmute` `-unban` `-warn` `-purge` | Moderation (staff, prefix `-`) |
| `/profile [user]` | Public profile dashboard with buttons |
| `/balance` `/daily` `/work` `/pay` `/leaderboard` | Economy |
| `/blackjack` `/shop` `/shopbuy` | Games and shop |
| `/ticket` `/payticket` `/warrant` `/removewarrant` | Citations and warrants |
| `/registervehicle` `/registertrailer` | Staff registrations |
| `/economyadmin` | Staff economy tools |
| `/owner-panel` | Owner management panel |
| `/help` | Command list by your rank (dropdown) |
| `/startup` `/over` `/session-status` | Sessions (staff) |
| `/ticket-panel` `/info-panel` `/dept-ping-panel` | Staff setup panels |

## Profile Dashboard

`/profile` posts a **public** embed with buttons:

- **Registrations** — vehicle dropdown with full details
- **Tickets** — citation tickets, fines, payment status
- **Trailers** — trailer dropdown with details
- **Warrants** — active and cleared warrants
- **Economy** — balances, net worth, transaction history

## Ticket Enforcement

- **5 unpaid tickets** — registration suspended + DM
- **7 unpaid tickets** — automatic warrant + DM

## Setup

1. Copy `config.example.yaml` to `config.yaml` and fill in your server IDs (or use your existing `config.yaml`)
2. Copy `.env.example` to `.env` and set:
   - `DISCORD_TOKEN`
   - `DATABASE_URL` (PostgreSQL / Neon)
   - `GITHUB_TOKEN` (optional — mirrors guild permissions to the repo under `data/`)
3. `pip install -r requirements.txt`
4. `python bot.py`

## GitHub

Code lives at [hamburger445/VSRP-Bot](https://github.com/hamburger445/VSRP-Bot).

When `GITHUB_TOKEN` is set, permission changes from `/setup-permissions` and `-role` are also saved to JSON files in the repo (`data/guild_permissions/`). PostgreSQL remains the primary database for all other bot data.

**Never commit `.env` or tokens to GitHub.**

## Container hosting (GitHub pull on start)

See [STARTUP.md](STARTUP.md) for the panel startup command and env vars (`GITHUB_TOKEN`, `GITHUB_REPO`, etc.).

## Config Notes

- `emojis: {}` — no emojis used unless you add them here
- `categories.tickets` — Discord category ID for support ticket channels
- `support_tickets.support_roles` — roles that can see support tickets
- `channels.logs` — audit log destinations per action type
- Any **staff** member can host sessions (no separate session_host role needed)

## Permissions

- **Owners** (`owners` in config) — full god mode
- **Staff** — admin commands, sessions, panels
- All checks are server-side
