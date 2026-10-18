from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient

from app.config.business import BusinessConfig, ToolConfig
from app.config.local import LocalExecutionConfig
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


@pytest.mark.parametrize("local_enabled,tools_enabled", [(True, True), (False, True), (True, False)])
def test_config_reports_project_file_access_switches(monkeypatch, local_enabled, tools_enabled):
    import app.api.system as system_api

    business = BusinessConfig(local=LocalExecutionConfig(enabled=local_enabled), tools=ToolConfig(enabled=tools_enabled))
    monkeypatch.setattr(system_api, "get_business_config", lambda: business)
    config = TestClient(app).get("/config").json()
    assert config["local"]["enabled"] is local_enabled
    assert config["tools"]["enabled"] is tools_enabled
