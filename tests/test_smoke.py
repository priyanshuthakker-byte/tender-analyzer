from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from app.main import app, settings


def test_health():
    with TemporaryDirectory() as d:
        settings.database_path = Path(d) / "t.db"
        client = TestClient(app)
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert "ai_configured" in data


def test_root():
    with TemporaryDirectory() as d:
        settings.database_path = Path(d) / "t.db"
        client = TestClient(app)
        r = client.get("/")
        assert r.status_code == 200
        assert "Tender" in r.text or "tender" in r.text.lower()
