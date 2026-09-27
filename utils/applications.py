import json
from datetime import datetime, timezone

import discord

from utils.core import load_config
from utils.embeds import build_embed


def application_panel_embed() -> discord.Embed:
    return build_embed(
        title="Civilian Applications",
        description=(
            "Interested in joining one of our civilian organizations?\n\n"
            "Select an application below to begin.\n\n"
            "Applications are completed entirely through direct messages.\n"
            "Please answer honestly and provide detailed responses."
        ),
        footer="WCRP Civilian Application System",
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


def applications_channel_id() -> int:
    return int(load_config().get("applications", {}).get("submission_channel_id", 1513940631779807232))


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
