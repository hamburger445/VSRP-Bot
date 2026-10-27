import os

from utils.form_tokens import parse_form_token, sign_form_token
from utils.form_urls import public_form_base
from utils.leo_pdf import citation_pdf_bytes


def test_form_token_roundtrip(monkeypatch):
    monkeypatch.setenv("FORM_SECRET", "unit-test-secret")
    token = sign_form_token(kind="citation", guild_id=1, officer_id=2, subject_id=3)
    payload = parse_form_token(token)
    assert payload["t"] == "citation"
    assert payload["g"] == 1
    assert payload["o"] == 2
    assert payload["s"] == 3
    assert payload["j"]


def test_form_token_rejects_tamper(monkeypatch):
    monkeypatch.setenv("FORM_SECRET", "unit-test-secret")
    token = sign_form_token(kind="warrant", guild_id=1, officer_id=2, subject_id=3)
    try:
        parse_form_token(token[:-2] + "xx")
        raise AssertionError("expected invalid token")
    except ValueError:
        pass


def test_public_form_base(monkeypatch):
    monkeypatch.delenv("SERVER_IP", raising=False)
    monkeypatch.delenv("CONNECT_IP", raising=False)
    monkeypatch.setenv("PUBLIC_FORM_URL", "http://example.test:8080/")
    assert public_form_base() == "http://example.test:8080"


def test_public_form_base_rejects_localhost(monkeypatch):
    monkeypatch.setenv("PUBLIC_FORM_URL", "http://127.0.0.1:8080")
    assert public_form_base() == ""


def test_citation_pdf():
    data = citation_pdf_bytes(
        {"agency": "WPD", "charge_description": "Speeding", "va_code": "46.2-862"},
        case_no="0000",
        fine=50,
        subject_label="driver",
        issuer_label="officer",
    )
    assert data.startswith(b"%PDF")


def test_citation_html_placeholders():
    root = os.path.join(os.path.dirname(__file__), "..", "webforms", "citation.html")
    text = open(root, encoding="utf-8").read()
    assert "{{TOKEN}}" in text
    assert 'action="/f/citation"' in text
