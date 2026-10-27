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
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    monkeypatch.delenv("RENDER_EXTERNAL_HOSTNAME", raising=False)
    monkeypatch.setenv("PUBLIC_FORM_URL", "https://wcrp-bot.onrender.com/")
    assert public_form_base() == "https://wcrp-bot.onrender.com"


def test_public_form_base_uses_render_url(monkeypatch):
    monkeypatch.delenv("PUBLIC_FORM_URL", raising=False)
    monkeypatch.delenv("FORM_PUBLIC_URL", raising=False)
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://wcrp-bot.onrender.com")
    assert public_form_base() == "https://wcrp-bot.onrender.com"


def test_public_form_base_rejects_localhost_and_http(monkeypatch):
    monkeypatch.setenv("PUBLIC_FORM_URL", "http://127.0.0.1:8080")
    assert public_form_base() == ""
    monkeypatch.setenv("PUBLIC_FORM_URL", "http://wcrp-bot.onrender.com")
    assert public_form_base() == ""
    monkeypatch.setenv("PUBLIC_FORM_URL", "https://1.2.3.4")
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


def test_editor_html_is_split_layout():
    root = os.path.join(os.path.dirname(__file__), "..", "webforms", "editor.html")
    text = open(root, encoding="utf-8").read()
    assert "form-pane" in text
    assert "preview-pane" in text
    assert "/api/submit" in text


def test_citation_html_placeholders():
    root = os.path.join(os.path.dirname(__file__), "..", "webforms", "citation.html")
    text = open(root, encoding="utf-8").read()
    assert "{{TOKEN}}" in text
    assert 'action="/f/citation"' in text
