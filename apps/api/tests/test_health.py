import logging

from fastapi.testclient import TestClient

from app.core.logging import JsonRedactingFormatter, redact
from app.main import create_app


def test_health_ok() -> None:
    client = TestClient(create_app())
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_version() -> None:
    client = TestClient(create_app())
    r = client.get("/api/v1/system/version")
    assert r.status_code == 200
    assert "api" in r.json()


def test_redaction_of_credentials() -> None:
    assert "secret123" not in redact("rtsp://admin:secret123@10.0.0.5:554/Streams/Channels/101")
    assert "abc.def" not in redact("Authorization: Bearer abc.def.ghi")
    assert "hunter2" not in redact("password=hunter2")
    rec = logging.LogRecord(
        "t", logging.INFO, __file__, 1, "postgres_password: hunter2", None, None
    )
    assert "hunter2" not in JsonRedactingFormatter().format(rec)
