from collections.abc import AsyncIterator
from uuid import UUID

from fastapi.testclient import TestClient

import app.api.chat as chat_api
from app.config.settings import Settings, get_settings
from app.main import app
from app.persistence.db import get_session


def test_demo_password_protects_chat_routes(monkeypatch) -> None:
    async def override_session() -> AsyncIterator[object]:
        yield object()

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_settings] = lambda: Settings(
        dashscope_api_key="", demo_shared_password="demo-pass"
    )
    monkeypatch.setattr(chat_api, "enforce_chat_limits", lambda *_args: 1)

    class StubAgentService:
        def __init__(self, _session) -> None:
            pass

        async def chat(self, **_kwargs):
            return {
                "conversation_id": UUID("00000000-0000-0000-0000-000000000001"),
                "run_id": UUID("00000000-0000-0000-0000-000000000002"),
                "message": "ok",
                "redacted": False,
                "redaction_categories": (),
                "memory_count": 0,
            }

    monkeypatch.setattr(chat_api, "AgentService", StubAgentService)
    try:
        client = TestClient(app)
        unauthorized = client.post(
            "/v1/chat", json={"user_key": "demo-user", "content": "你好"}
        )
        authorized = client.post(
            "/v1/chat",
            auth=("demo", "demo-pass"),
            json={"user_key": "demo-user", "content": "你好"},
        )
    finally:
        app.dependency_overrides.clear()

    assert unauthorized.status_code == 401
    assert authorized.status_code != 401
