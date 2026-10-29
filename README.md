# WCRP Discord Bot

Python Discord bot for urban roleplay servers. Uses a GitHub JSON database by default and interactive dashboards.

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
   - `GITHUB_TOKEN` (required for the JSON database and permission mirrors)
   - `BOT_DATABASE=github` (default)
3. `pip install -r requirements.txt`
4. `python bot.py`

## GitHub

Code lives at [hamburger445/VSRP-Bot](https://github.com/hamburger445/VSRP-Bot).

When `GITHUB_TOKEN` is set, permission changes from `/setup-permissions` and `-role` are also saved to JSON files in the repo (`data/guild_permissions/`).

**Never commit `.env` or tokens to GitHub.**

## Database

With `BOT_DATABASE=github` and `GITHUB_TOKEN`, the bot stores records as JSON in the repo (`data/cad/db.json` on the `cad-data` branch by default) so Neon/Postgres is not required. Guild permission mirrors still use `data/guild_permissions/` on the configured GitHub branch.

`/citation`, `/warrant`, and `/serve-warrant` are filled in Discord. Citations go on the record immediately; warrants attach a PDF for LEO/staff review.

## WispByte / panel hosting

See [STARTUP.md](STARTUP.md). The server runs `bash panel-start.sh`, which pulls GitHub on boot and restarts the bot when you push to `main`.

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
