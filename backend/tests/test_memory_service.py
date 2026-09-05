from uuid import uuid4

import pytest

from app.memory.service import MemoryService
from app.persistence.models import Memory, MemoryStatus
from app.persistence.repositories import MemoryRepository


class FakeSession:
    def __init__(self):
        self.added = []

    async def scalar(self, _statement):
        return None

    async def scalars(self, _statement):
        return []

    def add(self, item):
        self.added.append(item)

    async def flush(self):
        return None


@pytest.mark.asyncio
async def test_explicit_sensitive_memory_waits_for_confirmation():
    session = FakeSession()

    memory = await MemoryService(session).add_explicit(
        user_id=uuid4(),
        content="我的健康状况需要保密",
        source_message_id=uuid4(),
    )

    assert memory is not None
    assert memory.status is MemoryStatus.PENDING
    assert memory.sensitivity.value == "sensitive"


@pytest.mark.asyncio
async def test_explicit_secret_is_not_persisted():
    session = FakeSession()

    memory = await MemoryService(session).add_explicit(
        user_id=uuid4(),
        content="我的 api_key=sk-demo-value-1234567890",
        source_message_id=uuid4(),
    )

    assert memory is None
    assert session.added == []


@pytest.mark.asyncio
async def test_pending_memory_can_be_rejected_without_becoming_retrievable():
    session = FakeSession()
    memory = Memory(
        user_id=uuid4(),
        content="用户可能偏好晨间学习",
        status=MemoryStatus.PENDING,
        kind="semantic",
        sensitivity="normal",
        confidence=0.9,
    )

    await MemoryRepository(session).set_status(memory, MemoryStatus.REJECTED)

    assert memory.status is MemoryStatus.REJECTED
