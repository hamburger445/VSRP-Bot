import json
from datetime import datetime, timezone

import discord

from utils.core import load_config
from utils.embeds import build_embed


def application_panel_embed() -> discord.Embed:
    lines = [f"• **{department_label(key)}** — `/apply` and choose `{key}`" for key in application_departments()]
    return build_embed(
        title="Department Applications",
        description=(
            "Apply for a department or civilian membership.\n\n"
            "Use **`/apply`** and pick a department, or use the button below for the default civilian application.\n\n"
            "Track status anytime with **`/application-status`**.\n\n"
            + "\n".join(lines)
        ),
        footer="WCRP Application System",
    )


def application_submission_embed(user: discord.User, answers: list[tuple[str, str]], submitted_at: datetime) -> discord.Embed:
    embed = build_embed(
        title="Civilian Application Submitted",
        description=(
            "A new civilian application has been submitted and is ready for review."
        ),
        footer="WCRP Civilian Application System",
    )
    embed.add_field(name="Applicant", value=f"{user} ({user.mention})", inline=False)
    embed.add_field(name="Discord ID", value=str(user.id), inline=False)
    embed.add_field(name="Submission Time", value=submitted_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), inline=False)

    for index, (question, answer) in enumerate(answers, start=1):
        field_name = f"Q{index}: {question}"
        embed.add_field(name=field_name, value=answer or "No response provided.", inline=False)

    return embed


def application_departments() -> dict[str, dict]:
    cfg = load_config().get("applications", {})
    raw = cfg.get("departments")
    if isinstance(raw, dict) and raw:
        return raw
    return {
        "civilian": {"label": "Civilian / WCVA"},
        "wpd": {"label": "Wytheville Police Department"},
        "wcso": {"label": "Wythe County Sheriffs Office"},
        "wfd": {"label": "Wythe County Fire & Rescue"},
    }


def department_label(department_key: str) -> str:
    dept = application_departments().get(department_key, {})
    return str(dept.get("label") or department_key.replace("_", " ").title())


def applications_channel_id(department: str = "civilian") -> int:
    cfg = load_config().get("applications", {})
    dept_cfg = application_departments().get(department, {})
    if dept_cfg.get("submission_channel_id"):
        return int(dept_cfg["submission_channel_id"])
    return int(cfg.get("submission_channel_id", 1513940631779807232))


def application_questions_for(department: str) -> list[dict[str, str | list[str]]]:
    dept_cfg = application_departments().get(department, {})
    questions = dept_cfg.get("questions")
    if questions and isinstance(questions, list):
        if questions and isinstance(questions[0], dict):
            return questions
        return [{"question": str(q), "type": "text"} for q in questions]
    return application_questions()


def applications_panel_channel_id() -> int | None:
    panel_channel = load_config().get("applications", {}).get("panel_channel_id")
    return int(panel_channel) if panel_channel else None


def reviewer_role_ids() -> list[int]:
    cfg = load_config().get("applications", {})
    reviewer_ids: list[int] = []
    role_id = cfg.get("reviewer_role_id")
    if role_id:
        reviewer_ids.append(int(role_id))
    extra_ids = cfg.get("reviewer_role_ids")
    if isinstance(extra_ids, list):
        reviewer_ids.extend(int(value) for value in extra_ids if value)
    reviewer_ids.append(1279634474333634635)
    return list(dict.fromkeys(reviewer_ids))


def application_pending_role_id() -> int | None:
    role_id = load_config().get("applications", {}).get("pending_role_id")
    return int(role_id) if role_id else None


def application_approved_role_id() -> int | None:
    role_id = load_config().get("applications", {}).get("approved_role_id")
    return int(role_id) if role_id else None


def application_questions() -> list[dict[str, str | list[str]]]:
    questions = load_config().get("applications", {}).get("questions")
    if questions:
        if isinstance(questions, list) and questions and isinstance(questions[0], dict):
            return questions
        if isinstance(questions, list):
            return [{"question": str(question), "type": "text"} for question in questions]
    return [
        {"question": "Roblox Username & Profile Link", "type": "text"},
        {"question": "Discord Username & ID", "type": "text"},
        {"question": "Are you 13+?", "type": "select", "options": ["Yes", "No"]},
        {"question": "On a scale of 1 to 10, how mature and professional do you think you are?", "type": "select", "options": [str(i) for i in range(1, 11)]},
        {"question": "Why do you want to join WCVA? (2+ Sentences)", "type": "text"},
        {"question": "Do you understand strict roleplay servers?", "type": "select", "options": ["Yes", "No"]},
        {"question": "Have you ever been in a strict roleplay server?", "type": "select", "options": ["Yes", "No"]},
        {"question": "Do you plan on joining a department or becoming whitelisted in WCVA?", "type": "select", "options": ["Yes", "No"]},
        {"question": "Do you understand that you can be denied for any given reason?", "type": "select", "options": ["Yes", "No"]},
    ]


def application_status_label(status: str) -> str:
    return {
        "pending": "Pending",
        "accepted": "Accepted",
        "denied": "Denied",
    }.get(status, status.title())


def application_status_color(status: str) -> int:
    return {
        "pending": 0xF1C40F,
        "accepted": 0x2ECC71,
        "denied": 0xE74C3C,
    }.get(status, 0x2B2D31)


def application_confirm_embed() -> discord.Embed:
    return build_embed(
        title="Civilian Application Confirmation",
        description=(
            "You are about to begin your civilian application.\n\n"
            "Please answer each question carefully. Once you submit the application, staff will review it."
        ),
        footer="WCRP Civilian Application System",
    )
