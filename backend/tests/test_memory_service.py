from uuid import uuid4

import pytest

from app.memory.service import MemoryService
from app.persistence.models import MemoryStatus


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
