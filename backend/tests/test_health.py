from collections.abc import AsyncIterator

from fastapi.testclient import TestClient

from app.main import app
from app.persistence.db import get_session


class HealthySession:
    async def scalar(self, _statement):
        return 1


class UnavailableSession:
    async def scalar(self, _statement):
        raise OSError("database unavailable")


def _override(session) -> AsyncIterator[object]:
    async def dependency():
        yield session

    return dependency


def test_health_endpoint_checks_database() -> None:
    app.dependency_overrides[get_session] = _override(HealthySession())
    try:
        response = TestClient(app).get("/health")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["database"] == "available"


def test_health_endpoint_reports_database_failure() -> None:
    app.dependency_overrides[get_session] = _override(UnavailableSession())
    try:
        response = TestClient(app).get("/health")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["database"] == "unavailable"


def test_config_endpoint_never_exposes_secrets() -> None:
    response = TestClient(app).get("/config")

    assert response.status_code == 200
    assert "dashscope_api_key" not in response.text
    assert response.json()["models"]["embedding_dimensions"] == 1024
