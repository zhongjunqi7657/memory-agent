from collections.abc import AsyncIterator
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

import app.api.memories as memories_api
from app.main import app
from app.persistence.db import get_session
from app.persistence.models import (
    Memory,
    MemoryKind,
    MemorySensitivity,
    MemoryStatus,
)

USER_ID = UUID("00000000-0000-0000-0000-000000000001")


class FakeSession:
    async def scalars(self, _statement):
        return []

    async def flush(self):
        return None

    async def commit(self):
        return None


class StubConversationRepository:
    def __init__(self, _session):
        pass

    async def get_user(self, _user_key):
        return type("UserRef", (), {"id": USER_ID})()


class StubMemoryRepository:
    memory: Memory

    def __init__(self, _session):
        pass

    async def get_for_user(self, memory_id, user_id):
        if memory_id == self.memory.id and user_id == USER_ID:
            return self.memory
        return None


def _session_override() -> AsyncIterator[FakeSession]:
    async def dependency():
        yield FakeSession()

    return dependency


def test_memory_can_be_edited_and_undone(monkeypatch) -> None:
    memory = Memory(
        id=uuid4(),
        user_id=USER_ID,
        kind=MemoryKind.SEMANTIC,
        status=MemoryStatus.ACTIVE,
        sensitivity=MemorySensitivity.NORMAL,
        content="用户喜欢看示例",
        confidence=Decimal("0.950"),
        importance=Decimal("0.500"),
        source_message_ids=[],
        metadata_={},
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    StubMemoryRepository.memory = memory
    app.dependency_overrides[get_session] = _session_override()
    monkeypatch.setattr(
        memories_api, "ConversationRepository", StubConversationRepository
    )
    monkeypatch.setattr(memories_api, "MemoryRepository", StubMemoryRepository)
    try:
        client = TestClient(app)
        edited = client.patch(
            f"/v1/memories/{memory.id}",
            json={"content": "用户喜欢先理解原理", "importance": 0.9},
        )
        undone = client.post(f"/v1/memories/{memory.id}/undo")
    finally:
        app.dependency_overrides.clear()

    assert edited.status_code == 200
    assert edited.json()["content"] == "用户喜欢先理解原理"
    assert edited.json()["importance"] == 0.9
    assert undone.status_code == 200
    assert undone.json()["content"] == "用户喜欢看示例"
    assert undone.json()["importance"] == 0.5
