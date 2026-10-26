import os

import pytest

pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="DATABASE_URL not set")


def test_health_endpoint_exists():
    from fastapi.testclient import TestClient

    from cad.app import app

    with TestClient(app) as client:
        res = client.get("/api/health")
    assert res.status_code in (200, 503)
    data = res.json()
    assert "status" in data
    assert "database" in data
