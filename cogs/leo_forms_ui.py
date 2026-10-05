"""Discord UI flows for Virginia-style citations and warrants."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import discord

from utils.leo_forms import (
    CHARGE_CLASSES,
    CITATION_AGENCIES,
    COURT_TYPES,
    JURISDICTIONS,
    WARRANT_TYPES,
    YES_NO,
    build_citation_embed,
    build_served_warrant_embed,
    build_warrant_embed,
    citation_summary,
    warrant_form_to_reason,
)

if TYPE_CHECKING:
    from cogs.leo import LawEnforcement

_COURT_SELECT_KEYS = {
    "court_gd": "general_district",
    "court_criminal": "criminal",
    "court_traffic": "traffic",
    "court_jdr": "jdr",
}


def _store_court_select(data: dict[str, Any], custom_id: str, yes_no: str) -> None:
    key = _COURT_SELECT_KEYS.get(custom_id)
    if key:
        data.setdefault("courts", {})[key] = yes_no == "Yes"


class WarrantOptionsView(discord.ui.View):
    def __init__(self, cog: "LawEnforcement", member: discord.Member):
        super().__init__(timeout=600)
        self.cog = cog
        self.member = member
        self.data: dict[str, Any] = {"courts": {}}

        self.add_item(
            discord.ui.Select(
                placeholder="Warrant of…",
                options=[discord.SelectOption(label=x, value=x) for x in WARRANT_TYPES],
                custom_id="warrant_type",
            )
        )
        self.add_item(
            discord.ui.Select(
                placeholder="Felony or Misdemeanor",
                options=[discord.SelectOption(label=x, value=x) for x in CHARGE_CLASSES],
                custom_id="charge_class",
            )
        )
        self.add_item(
            discord.ui.Select(
                placeholder="State or County",
                options=[discord.SelectOption(label=x, value=x) for x in JURISDICTIONS],
                custom_id="jurisdiction",
            )
        )
        self.add_item(
            discord.ui.Select(
                placeholder="General District Court",
                options=[discord.SelectOption(label=x, value=x) for x in YES_NO],
                custom_id="court_gd",
            )
        )
        self.add_item(
            discord.ui.Select(
                placeholder="Criminal Division",
                options=[discord.SelectOption(label=x, value=x) for x in YES_NO],
                custom_id="court_criminal",
            )
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != getattr(self, "_author_id", None):
            await interaction.response.send_message("This form is not for you.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="More court options…", style=discord.ButtonStyle.secondary, row=2)
    async def more_courts(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = WarrantCourtsView(self.cog, self.member, self.data)
        view._author_id = interaction.user.id
        for item in self.children:
            if isinstance(item, discord.ui.Select) and item.values and item.custom_id:
                if item.custom_id.startswith("court_"):
                    _store_court_select(self.data, item.custom_id, item.values[0])
                else:
                    self.data[item.custom_id] = item.values[0]
        await interaction.response.send_message(
            "Select traffic / JDR court options, then continue.",
            view=view,
            ephemeral=True,
        )

    @discord.ui.button(label="Continue to details", style=discord.ButtonStyle.primary, row=2)
    async def continue_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            if isinstance(item, discord.ui.Select) and item.values and item.custom_id:
                if item.custom_id.startswith("court_"):
                    _store_court_select(self.data, item.custom_id, item.values[0])
                else:
                    self.data[item.custom_id] = item.values[0]
        if not self.data.get("warrant_type"):
            await interaction.response.send_message("Select warrant type first.", ephemeral=True)
            return
        modal = WarrantAccusedModal(self.cog, self.member, self.data)
        await interaction.response.send_modal(modal)


class WarrantCourtsView(discord.ui.View):
    def __init__(self, cog: "LawEnforcement", member: discord.Member, data: dict[str, Any]):
        super().__init__(timeout=600)
        self.cog = cog
        self.member = member
        self.data = data
        self.add_item(
            discord.ui.Select(
                placeholder="Traffic Division",
                options=[discord.SelectOption(label=x, value=x) for x in YES_NO],
                custom_id="court_traffic",
            )
        )
        self.add_item(
            discord.ui.Select(
                placeholder="Juvenile & Domestic Relations",
                options=[discord.SelectOption(label=x, value=x) for x in YES_NO],
                custom_id="court_jdr",
            )
        )

    @discord.ui.button(label="Continue to accused info", style=discord.ButtonStyle.primary)
    async def continue_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            if isinstance(item, discord.ui.Select) and item.values and item.custom_id:
                if item.custom_id.startswith("court_"):
                    _store_court_select(self.data, item.custom_id, item.values[0])
        modal = WarrantAccusedModal(self.cog, self.member, self.data)
        await interaction.response.send_modal(modal)


class WarrantAccusedModal(discord.ui.Modal, title="Warrant — Accused"):
    accused = discord.ui.TextInput(label="Accused (Last, First, Middle)", max_length=120)
    sex = discord.ui.TextInput(label="Sex (M or F)", max_length=1, placeholder="M")
    race = discord.ui.TextInput(label="Race", max_length=40, placeholder="White")
    eyes = discord.ui.TextInput(label="Eye color", max_length=30)
    hair = discord.ui.TextInput(label="Hair color", max_length=30)

    def __init__(self, cog: "LawEnforcement", member: discord.Member, data: dict[str, Any]):
        super().__init__()
        self.cog = cog
        self.member = member
        self.data = data

    async def on_submit(self, interaction: discord.Interaction):
        self.data["accused"] = self.accused.value.strip()
        self.data["sex"] = self.sex.value.strip().upper()[:1]
        self.data["race"] = self.race.value.strip()
        self.data["eyes"] = self.eyes.value.strip()
        self.data["hair"] = self.hair.value.strip()
        await interaction.response.send_modal(
            WarrantCodeModal(self.cog, self.member, self.data)
        )


class WarrantCodeModal(discord.ui.Modal, title="Warrant — Code & Issue Date"):
    va_code = discord.ui.TextInput(label="Code of Virginia #", max_length=80)
    code_description = discord.ui.TextInput(
        label="Code description",
        style=discord.TextStyle.paragraph,
        max_length=500,
    )
    issued_datetime = discord.ui.TextInput(
        label="Date & time issued",
        placeholder="Month DD, YYYY, HH:MM AM/PM",
        max_length=120,
    )

    def __init__(self, cog: "LawEnforcement", member: discord.Member, data: dict[str, Any]):
        super().__init__()
        self.cog = cog
        self.member = member
        self.data = data

    async def on_submit(self, interaction: discord.Interaction):
        self.data["va_code"] = self.va_code.value.strip()
        self.data["code_description"] = self.code_description.value.strip()
        self.data["issued_datetime"] = self.issued_datetime.value.strip()
        await self.cog._finalize_warrant(interaction, self.member, self.data)


class CitationOptionsView(discord.ui.View):
    def __init__(self, cog: "LawEnforcement", member: discord.Member):
        super().__init__(timeout=600)
        self.cog = cog
        self.member = member
        self.data: dict[str, Any] = {}

        self.add_item(
            discord.ui.Select(
                placeholder="Agency",
                options=[discord.SelectOption(label=a[:100], value=a) for a in CITATION_AGENCIES],
                custom_id="agency",
            )
        )
        self.add_item(
            discord.ui.Select(
                placeholder="Court appearance",
                options=[discord.SelectOption(label=c[:100], value=c) for c in COURT_TYPES],
                custom_id="court_type",
            )
        )
        self.add_item(
            discord.ui.Select(
                placeholder="AM or PM",
                options=[
                    discord.SelectOption(label="AM", value="AM"),
                    discord.SelectOption(label="PM", value="PM"),
                ],
                custom_id="am_pm",
            )
        )

    @discord.ui.button(label="More options…", style=discord.ButtonStyle.secondary, row=1)
    async def flags(self, interaction: discord.Interaction, button: discord.ui.Button):
        self._collect_selects()
        view = CitationFlagsView(self.cog, self.member, self.data)
        view._author_id = interaction.user.id
        await interaction.response.send_message("Select Y/N flags, then continue.", view=view, ephemeral=True)

    @discord.ui.button(label="Continue", style=discord.ButtonStyle.primary, row=1)
    async def continue_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self._collect_selects()
        if not self.data.get("agency"):
            await interaction.response.send_message("Select an agency.", ephemeral=True)
            return
        await interaction.response.send_modal(CitationDetailsModal(self.cog, self.member, self.data))

    def _collect_selects(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Select) and item.values:
                if item.custom_id:
                    self.data[item.custom_id] = item.values[0]


class CitationFlagsView(discord.ui.View):
    def __init__(self, cog: "LawEnforcement", member: discord.Member, data: dict[str, Any]):
        super().__init__(timeout=600)
        self.cog = cog
        self.member = member
        self.data = data
        for key, label in (
            ("commercial_motor_vehicle", "Commercial motor vehicle"),
            ("hazardous_materials", "Hazardous materials"),
            ("resulted_in_fatality", "Resulted in fatality"),
            ("highway_safety_corridor", "Highway safety corridor"),
        ):
            self.add_item(
                discord.ui.Select(
                    placeholder=label,
                    options=[discord.SelectOption(label=x, value=x) for x in YES_NO],
                    custom_id=key,
                )
            )

    @discord.ui.button(label="Continue to citation details", style=discord.ButtonStyle.primary)
    async def continue_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            if isinstance(item, discord.ui.Select) and item.values:
                val = item.values[0]
                key = item.custom_id or ""
                self.data[key] = "Y" if val == "Yes" else "N"
        await interaction.response.send_modal(CitationDetailsModal(self.cog, self.member, self.data))


class CitationDetailsModal(discord.ui.Modal, title="Virginia Uniform Summons"):
    court_date = discord.ui.TextInput(label="Court date (Month DD, 20XX)", max_length=60)
    court_time = discord.ui.TextInput(label="Court time", placeholder="10:30", max_length=20)
    va_code = discord.ui.TextInput(label="VA Code", max_length=80)
    local_code = discord.ui.TextInput(label="Local code", max_length=80, required=False)
    charge_description = discord.ui.TextInput(
        label="Describe charge",
        style=discord.TextStyle.paragraph,
        max_length=400,
    )

    def __init__(self, cog: "LawEnforcement", member: discord.Member, data: dict[str, Any]):
        super().__init__()
        self.cog = cog
        self.member = member
        self.data = data

    async def on_submit(self, interaction: discord.Interaction):
        self.data["court_date"] = self.court_date.value.strip()
        self.data["court_time"] = self.court_time.value.strip()
        self.data["va_code"] = self.va_code.value.strip()
        self.data["local_code"] = (self.local_code.value or "").strip()
        self.data["charge_description"] = self.charge_description.value.strip()
        await interaction.response.send_modal(
            CitationIssueModal(self.cog, self.member, self.data)
        )


class CitationIssueModal(discord.ui.Modal, title="Citation — Issue"):
    fine = discord.ui.TextInput(label="Fine amount ($)", placeholder="150", max_length=10)
    issued_by = discord.ui.TextInput(
        label="Issued by (Rank, Name, Callsign, Agency)",
        max_length=200,
    )

    def __init__(self, cog: "LawEnforcement", member: discord.Member, data: dict[str, Any]):
        super().__init__()
        self.cog = cog
        self.member = member
        self.data = data

    async def on_submit(self, interaction: discord.Interaction):
        try:
            fine = int(self.fine.value.replace(",", "").replace("$", "").strip())
        except ValueError:
            await interaction.response.send_message("Fine must be a number.", ephemeral=True)
            return
        self.data["issued_by"] = self.issued_by.value.strip()
        if not self.data.get("am_pm"):
            self.data["am_pm"] = "AM"
        for key in ("commercial_motor_vehicle", "hazardous_materials", "resulted_in_fatality", "highway_safety_corridor"):
            self.data.setdefault(key, "N")
        await self.cog._finalize_citation(interaction, self.member, self.data, fine)


class ServeWarrantModal(discord.ui.Modal, title="Served Warrant"):
    arresting_officer = discord.ui.TextInput(label="Arresting officer (name)", max_length=120)
    badge_agency = discord.ui.TextInput(
        label="Badge no., agency, jurisdiction",
        placeholder="1234, WPD, Town of Wytheville",
        max_length=200,
    )
    served_datetime = discord.ui.TextInput(
        label="Date & time served",
        placeholder="Month DD, YYYY, HH:MM AM/PM",
        max_length=120,
    )

    def __init__(self, cog: "LawEnforcement", warrant_id: int, warrant_row: dict):
        super().__init__()
        self.cog = cog
        self.warrant_id = warrant_id
        self.warrant_row = warrant_row

    async def on_submit(self, interaction: discord.Interaction):
        serve_data = {
            "arresting_officer": self.arresting_officer.value.strip(),
            "badge_agency_jurisdiction": self.badge_agency.value.strip(),
            "served_datetime": self.served_datetime.value.strip(),
        }
        await self.cog._finalize_serve_warrant(
            interaction,
            self.warrant_id,
            self.warrant_row,
            serve_data,
        )


class ActiveWarrantSelect(discord.ui.Select):
    def __init__(self, cog: "LawEnforcement", rows: list[dict]):
        self.cog = cog
        options = []
        for row in rows[:25]:
            label = f"#{row['id']} — {(row.get('reason') or '')[:80]}"
            options.append(discord.SelectOption(label=label[:100], value=str(row["id"])))
        super().__init__(placeholder="Select warrant to mark served…", options=options)

    async def callback(self, interaction: discord.Interaction):
        warrant_id = int(self.values[0])
        row = next((r for r in self.view.rows if r["id"] == warrant_id), None)
        if not row:
            await interaction.response.send_message("Warrant not found.", ephemeral=True)
            return
        await interaction.response.send_modal(ServeWarrantModal(self.cog, warrant_id, row))


class ServeWarrantView(discord.ui.View):
    def __init__(self, cog: "LawEnforcement", rows: list[dict]):
        super().__init__(timeout=300)
        self.rows = rows
        self.add_item(ActiveWarrantSelect(cog, rows))
