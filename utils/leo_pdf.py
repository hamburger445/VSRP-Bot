"""Roleplay warrant PDFs (not official Commonwealth of Virginia forms)."""

from __future__ import annotations

import io
from typing import Any

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from utils.leo_case import format_case_number
from utils.leo_forms import parse_form_json


def _x(flag: Any) -> str:
    if flag is True or str(flag).lower() in {"1", "true", "yes", "y"}:
        return "X"
    return " "


def _safe(value: Any) -> str:
    if value is None:
        return "-"
    text = str(value).replace("\u2014", "-").replace("\u2013", "-")
    return text.encode("latin-1", "replace").decode("latin-1")


def _heading(pdf: FPDF, text: str) -> None:
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(pdf.epw, 8, _safe(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def _line(pdf: FPDF, label: str, value: Any) -> None:
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", "", 10)
    shown = "-" if value is None or str(value).strip() == "" else str(value)
    pdf.multi_cell(pdf.epw, 6, _safe(f"{label} {shown}"))


def _disclaimer(pdf: FPDF) -> None:
    pdf.ln(8)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(
        pdf.epw,
        4,
        "Roleplay record for WCRP. This document is not an official Commonwealth of Virginia court form.",
    )


def warrant_pdf_bytes(
    form: dict[str, Any] | None,
    *,
    case_no: str,
    subject_label: str = "",
    issuer_label: str = "",
    reason: str = "",
) -> bytes:
    data = dict(form or {})
    courts = data.get("courts") if isinstance(data.get("courts"), dict) else {}
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    title = (
        f"WARRANT OF ({data.get('warrant_type') or 'Arrest'}) - "
        f"({data.get('charge_class') or 'Misdemeanor'}) "
        f"({data.get('jurisdiction') or 'County'})"
    )
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(pdf.epw, 8, _safe(title), align="C")
    pdf.ln(4)
    _heading(pdf, "Jurisdiction and court")
    _line(pdf, "COUNTY OF:", data.get("county") or "WYTHE")
    _line(pdf, "GENERAL DISTRICT COURT:", f"[{_x(courts.get('general_district'))}]")
    _line(pdf, "CRIMINAL:", f"[{_x(courts.get('criminal'))}]")
    _line(pdf, "TRAFFIC:", f"[{_x(courts.get('traffic'))}]")
    _line(pdf, "JUVENILE AND DOMESTIC RELATIONS DISTRICT COURT:", f"[{_x(courts.get('jdr'))}]")
    pdf.ln(2)
    _heading(pdf, "Offense information")
    _line(pdf, "CODE OF VIRGINIA:", data.get("va_code"))
    _line(pdf, "CODE DESCRIPTION:", data.get("code_description") or reason)
    pdf.ln(2)
    _heading(pdf, "Accused information")
    _line(pdf, "ACCUSED:", data.get("accused"))
    _line(pdf, "SEX:", data.get("sex"))
    _line(pdf, "RACE:", data.get("race"))
    _line(pdf, "EYES:", data.get("eyes"))
    _line(pdf, "HAIR:", data.get("hair"))
    if subject_label:
        _line(pdf, "DISCORD SUBJECT:", subject_label)
    pdf.ln(2)
    _heading(pdf, "Issuance information")
    _line(pdf, "CASE NO.:", format_case_number(case_no))
    _line(pdf, "DATE & TIME ISSUED:", data.get("issued_datetime"))
    if issuer_label:
        _line(pdf, "ISSUED BY:", data.get("issued_by") or issuer_label)
    _disclaimer(pdf)
    return bytes(pdf.output())


def served_warrant_pdf_bytes(
    warrant_form: dict[str, Any] | None,
    serve_data: dict[str, Any] | None,
    *,
    case_no: str,
    subject_label: str = "",
) -> bytes:
    data = dict(warrant_form or {})
    serve = dict(serve_data or {})
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_x(pdf.l_margin)
    pdf.cell(pdf.epw, 10, "SERVED WARRANT", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
    pdf.ln(4)
    _heading(pdf, "Accused information")
    _line(pdf, "ACCUSED:", data.get("accused") or serve.get("accused"))
    _line(pdf, "CASE NO.:", format_case_number(case_no))
    _line(pdf, "DATE & TIME ISSUED:", data.get("issued_datetime") or serve.get("original_issued"))
    if subject_label:
        _line(pdf, "DISCORD SUBJECT:", subject_label)
    pdf.ln(2)
    _heading(pdf, "Service information")
    _line(pdf, "ARRESTING OFFICER:", serve.get("arresting_officer"))
    _line(pdf, "BADGE NO., AGENCY, & JURISDICTION:", serve.get("badge_agency_jurisdiction"))
    _line(pdf, "DATE & TIME SERVED:", serve.get("served_datetime"))
    _disclaimer(pdf)
    return bytes(pdf.output())


def citation_pdf_bytes(
    form: dict[str, Any] | None,
    *,
    case_no: str,
    fine: int = 0,
    subject_label: str = "",
    issuer_label: str = "",
) -> bytes:
    data = dict(form or {})
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_x(pdf.l_margin)
    pdf.cell(pdf.epw, 10, "VIRGINIA UNIFORM SUMMONS", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
    pdf.set_font("Helvetica", "I", 8)
    pdf.multi_cell(pdf.epw, 4, "Roleplay record for WCRP. Not an official Commonwealth document.")
    pdf.ln(2)
    _heading(pdf, "Agency")
    _line(pdf, "Agency:", data.get("agency"))
    _heading(pdf, "Court and jurisdiction")
    _line(pdf, "YOU ARE SUMMONED TO APPEAR IN THE COUNTY OF:", data.get("county") or "WYTHE")
    _line(pdf, "Court type:", data.get("court_type"))
    _heading(pdf, "Court appearance")
    _line(pdf, "ON:", data.get("court_date") or f"{data.get('court_month', '')} {data.get('court_day', '')}, {data.get('court_year', '')}")
    _line(pdf, "AT:", f"{data.get('court_time', '')} {data.get('am_pm', '')}")
    _heading(pdf, "Charge information")
    _line(pdf, "VA CODE:", data.get("va_code"))
    _line(pdf, "LOCAL CODE:", data.get("local_code"))
    _line(pdf, "DESCRIBE CHARGE:", data.get("charge_description") or data.get("describe_charge"))
    _line(pdf, "FINE:", f"${int(fine):,}")
    _heading(pdf, "Additional questions")
    _line(pdf, "COMMERCIAL MOTOR VEHICLE:", data.get("commercial_motor_vehicle"))
    _line(pdf, "HAZARDOUS MATERIALS:", data.get("hazardous_materials"))
    _line(pdf, "RESULTED IN FATALITY:", data.get("resulted_in_fatality"))
    _line(pdf, "HIGHWAY SAFETY CORRIDOR:", data.get("highway_safety_corridor"))
    _heading(pdf, "Issuing officer")
    _line(pdf, "ISSUED BY:", data.get("issued_by") or issuer_label)
    _line(pdf, "CASE NO.:", format_case_number(case_no))
    if subject_label:
        _line(pdf, "DISCORD SUBJECT:", subject_label)
    _disclaimer(pdf)
    return bytes(pdf.output())


def pdf_file(data: bytes, filename: str):
    import discord

    return discord.File(io.BytesIO(data), filename=filename)


def form_from_row(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    if isinstance(row.get("form"), dict):
        return row["form"]
    return parse_form_json(row.get("form_json"))
