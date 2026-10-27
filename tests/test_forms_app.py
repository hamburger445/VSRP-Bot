import asyncio
import os

os.environ.setdefault("FORM_SECRET", "unit-test-secret")

from aiohttp.test_utils import TestClient, TestServer

from forms_app import make_app
from utils.form_tokens import sign_form_token


def test_editor_route_returns_html_quickly():
    async def go():
        token = sign_form_token(kind="citation", guild_id=1, officer_id=2, subject_id=3)
        app = make_app()
        async with TestClient(TestServer(app)) as client:
            resp = await asyncio.wait_for(client.get(f"/e/citation/{token}"), timeout=3)
            assert resp.status == 200
            text = await resp.text()
            assert "DOCUMENT EDITOR" in text
            assert "form-pane" in text
            assert "preview-pane" in text
            health = await client.get("/health")
            assert health.status == 200
            body = await health.json()
            assert body.get("ok") is True

    asyncio.run(go())


def test_invalid_token_is_not_a_spinner():
    async def go():
        app = make_app()
        async with TestClient(TestServer(app)) as client:
            resp = await asyncio.wait_for(client.get("/e/citation/not-a-token"), timeout=3)
            assert resp.status == 400

    asyncio.run(go())
