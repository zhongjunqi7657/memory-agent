from collections.abc import AsyncIterator
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

from fastapi.testclient import TestClient

import app.api.conversations as conversations_api
from app.main import app
from app.persistence.db import get_session
from app.persistence.models import MessageRole

USER_ID = UUID("00000000-0000-0000-0000-000000000001")
CONVERSATION_ID = UUID("00000000-0000-0000-0000-000000000002")
MESSAGE_ID = UUID("00000000-0000-0000-0000-000000000003")
NOW = datetime(2026, 9, 3, tzinfo=timezone.utc)


class StubConversationRepository:
    def __init__(self, _session) -> None:
        pass

    async def get_user(self, external_key: str):
        return SimpleNamespace(id=USER_ID) if external_key == "demo-user" else None

    async def list_conversations(self, user_id, *, limit: int):
        assert user_id == USER_ID
        assert limit == 50
        return [
            SimpleNamespace(
                id=CONVERSATION_ID,
                title="学习 LangGraph",
                created_at=NOW,
                updated_at=NOW,
            )
        ]

    async def get_conversation(self, conversation_id, user_id):
        if conversation_id == CONVERSATION_ID and user_id == USER_ID:
            return SimpleNamespace(id=CONVERSATION_ID)
        return None

    async def list_messages(self, conversation_id, *, limit: int):
        assert conversation_id == CONVERSATION_ID
        assert limit == 200
        return [
            SimpleNamespace(
                id=MESSAGE_ID,
                role=MessageRole.USER,
                content="我正在学习 LangGraph",
                sequence=1,
                is_redacted=False,
                created_at=NOW,
            )
        ]


def test_conversation_history_is_user_scoped(monkeypatch) -> None:
    async def override_session() -> AsyncIterator[object]:
        yield object()

    app.dependency_overrides[get_session] = override_session
    monkeypatch.setattr(
        conversations_api, "ConversationRepository", StubConversationRepository
    )
    try:
        client = TestClient(app)
        conversations = client.get("/v1/conversations")
        messages = client.get(f"/v1/conversations/{CONVERSATION_ID}/messages")
        missing = client.get(
            "/v1/conversations/00000000-0000-0000-0000-000000000099/messages"
        )
    finally:
        app.dependency_overrides.clear()

    assert conversations.status_code == 200
    assert conversations.json()[0]["title"] == "学习 LangGraph"
    assert messages.status_code == 200
    assert messages.json()[0]["content"] == "我正在学习 LangGraph"
    assert missing.status_code == 404
