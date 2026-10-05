"""Virginia-style citation & warrant form text and embed builders."""

from __future__ import annotations

import json
from typing import Any

import discord

from utils.leo_case import format_case_number

CITATION_AGENCIES = (
    "Wytheville Police Department",
    "Department of State Police",
    "Wythe County Sheriffs Office",
    "Rural Retreat Police Department",
)

WARRANT_TYPES = ("Arrest", "Search")
CHARGE_CLASSES = ("Felony", "Misdemeanor")
JURISDICTIONS = ("State", "County")
YES_NO = ("Yes", "No")
SEX_OPTIONS = ("M", "F")
RACE_OPTIONS = ("Black", "White", "Hispanic", "Asian", "Native American", "Other")
COURT_TYPES = (
    "General District Court (Traffic)",
    "General District Court (Criminal)",
    "Juvenile & Domestic Relations District Court",
)


def _x_mark(yes: bool) -> str:
    return "X" if yes else " "


def warrant_form_to_reason(data: dict[str, Any]) -> str:
    """Compact summary stored in warrants.reason for legacy queries."""
    accused = data.get("accused", "Unknown")
    wtype = data.get("warrant_type", "Arrest")
    return f"{wtype} warrant — {accused} (VA {data.get('va_code', 'N/A')})"


def build_warrant_embed(
    data: dict[str, Any],
    *,
    case_no: str,
    subject: discord.Member | None = None,
    footer: str | None = None,
) -> discord.Embed:
    courts = data.get("courts", {})
    embed = discord.Embed(
        title=f"WARRANT OF {data.get('warrant_type', 'Arrest').upper()} — "
        f"{data.get('charge_class', 'Misdemeanor').upper()} ({data.get('jurisdiction', 'County')})",
        color=0x992D22,
    )
    body = (
        f"**COUNTY OF:** WYTHE\n\n"
        f"GENERAL DISTRICT COURT: `{_x_mark(courts.get('general_district'))}`\n"
        f"CRIMINAL: `{_x_mark(courts.get('criminal'))}`\n"
        f"TRAFFIC: `{_x_mark(courts.get('traffic'))}`\n"
        f"JUVENILE AND DOMESTIC RELATIONS DISTRICT COURT: `{_x_mark(courts.get('jdr'))}`\n\n"
        f"**CODE OF VIRGINIA:** {data.get('va_code', '—')}\n"
        f"**CODE DESCRIPTION:** {data.get('code_description', '—')}\n\n"
        f"**ACCUSED:** {data.get('accused', '—')}\n"
        f"**SEX:** {data.get('sex', '—')} | **RACE:** {data.get('race', '—')}\n"
        f"**EYES:** {data.get('eyes', '—')} | **HAIR:** {data.get('hair', '—')}\n\n"
        f"**CASE NO.:** {format_case_number(case_no)}\n"
        f"**DATE & TIME ISSUED:** {data.get('issued_datetime', '—')}"
    )
    embed.description = body
    if subject:
        embed.add_field(name="Discord Subject", value=subject.mention, inline=False)
    if footer:
        embed.set_footer(text=footer)
    return embed


def build_served_warrant_embed(
    warrant_data: dict[str, Any],
    serve_data: dict[str, Any],
    *,
    case_no: str,
    subject: discord.Member | None = None,
) -> discord.Embed:
    embed = discord.Embed(title="SERVED WARRANT", color=0x27AE60)
    embed.description = (
        f"**ACCUSED:** {warrant_data.get('accused', serve_data.get('accused', '—'))}\n"
        f"**CASE NO.:** {format_case_number(case_no)}\n"
        f"**DATE & TIME ISSUED:** {warrant_data.get('issued_datetime', '—')}\n\n"
        f"**ARRESTING OFFICER:** {serve_data.get('arresting_officer', '—')}\n"
        f"**BADGE NO., AGENCY, & JURISDICTION:** {serve_data.get('badge_agency_jurisdiction', '—')}\n"
        f"**DATE & TIME SERVED:** {serve_data.get('served_datetime', '—')}"
    )
    if subject:
        embed.add_field(name="Subject", value=subject.mention, inline=False)
    return embed


def citation_summary(data: dict[str, Any]) -> str:
    return data.get("charge_description") or data.get("describe_charge") or "Citation"


def build_citation_embed(
    data: dict[str, Any],
    *,
    case_no: str,
    subject: discord.Member | None = None,
    fine: int,
    footer: str | None = None,
) -> discord.Embed:
    court = data.get("court_type", "General District Court (Traffic)")
    embed = discord.Embed(title="VIRGINIA UNIFORM SUMMONS", color=0xE67E22)
    flags = (
        f"COMMERCIAL MOTOR VEHICLE: **{data.get('commercial_motor_vehicle', 'N')}**\n"
        f"HAZARDOUS MATERIALS: **{data.get('hazardous_materials', 'N')}**\n"
        f"RESULTED IN FATALITY: **{data.get('resulted_in_fatality', 'N')}**\n"
        f"HIGHWAY SAFETY CORRIDOR: **{data.get('highway_safety_corridor', 'N')}**"
    )
    embed.description = (
        f"**Agency:** {data.get('agency', '—')}\n\n"
        f"**YOU ARE SUMMONED TO APPEAR IN THE COUNTY OF:** WYTHE\n\n"
        f"**Court:** {court}\n"
        f"**ON** {data.get('court_date', '—')} **AT** {data.get('court_time', '—')} "
        f"**{data.get('am_pm', 'AM')}**\n\n"
        f"**VA CODE:** {data.get('va_code', '—')}\n"
        f"**LOCAL CODE:** {data.get('local_code', '—')}\n\n"
        f"**DESCRIBE CHARGE:** {data.get('charge_description', '—')}\n\n"
        f"{flags}\n\n"
        f"**ISSUED BY:** {data.get('issued_by', '—')}\n"
        f"**CASE NO.:** {format_case_number(case_no)}\n"
        f"**FINE:** ${fine:,}"
    )
    if subject:
        embed.add_field(name="Subject", value=subject.mention, inline=False)
    if footer:
        embed.set_footer(text=footer)
    return embed


def parse_form_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def warrant_status_emoji(active: int, status: str | None) -> str:
    st = (status or "").lower()
    if st == "served":
        return "🟢"
    if active and st in ("", "active"):
        return "🔴"
    if st == "cleared":
        return "⚪"
    return "🟡"


def ticket_status_emoji(payment_status: str | None) -> str:
    if (payment_status or "").lower() == "paid":
        return "🟢"
    return "🔴"
