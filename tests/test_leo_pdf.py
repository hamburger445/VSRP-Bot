from utils.leo_pdf import served_warrant_pdf_bytes, warrant_pdf_bytes


def test_warrant_pdf_contains_fields():
    data = warrant_pdf_bytes(
        {
            "warrant_type": "Arrest",
            "charge_class": "Felony",
            "jurisdiction": "County",
            "county": "WYTHE",
            "va_code": "18.2-57",
            "code_description": "Assault",
            "accused": "Doe, Jane, A",
            "sex": "F",
            "race": "White",
            "eyes": "Brown",
            "hair": "Black",
            "issued_datetime": "10/26/2026 07:00 PM",
            "courts": {"general_district": True, "criminal": True},
        },
        case_no="0000",
        subject_label="jane#0001",
        issuer_label="Officer Smith",
    )
    assert data.startswith(b"%PDF")
    assert len(data) > 400


def test_served_warrant_pdf():
    data = served_warrant_pdf_bytes(
        {"accused": "Doe, Jane, A", "issued_datetime": "10/01/2026 01:00 PM"},
        {
            "arresting_officer": "Smith",
            "badge_agency_jurisdiction": "12, WPD, Wythe",
            "served_datetime": "10/26/2026 07:00 PM",
        },
        case_no="0000",
    )
    assert data.startswith(b"%PDF")
