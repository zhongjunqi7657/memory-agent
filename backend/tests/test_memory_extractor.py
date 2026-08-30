from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.memory.extractor import ExtractedMemory, ExtractionResult, MemoryExtractor
from app.persistence.models import MemoryKind, MemorySensitivity, MemoryStatus


class FakeStructuredModel:
    def __init__(self, result: ExtractionResult):
        self.result = result

    def with_structured_output(self, _schema):
        return self

    async def ainvoke(self, _messages):
        return self.result


class FakeSession:
    def __init__(self):
        self.added = []

    async def scalars(self, _statement):
        return []

    def add(self, item):
        self.added.append(item)

    async def flush(self):
        return None


@pytest.mark.asyncio
async def test_extractor_persists_active_and_pending_candidates_without_embedding():
    session = FakeSession()
    result = ExtractionResult(
        memories=[
            ExtractedMemory(
                content="我计划两个月内完成 Agent 项目",
                kind=MemoryKind.SEMANTIC,
                confidence=0.95,
                canonical_key="current_goal",
            ),
            ExtractedMemory(
                content="用户可能偏好早晨学习",
                kind=MemoryKind.SEMANTIC,
                confidence=0.91,
                explicit=False,
            ),
            ExtractedMemory(
                content="api_key=should-not-be-stored",
                kind=MemoryKind.SEMANTIC,
                confidence=0.99,
                sensitivity=MemorySensitivity.SECRET,
            ),
        ]
    )
    extractor = MemoryExtractor(
        session,
        model=FakeStructuredModel(result),
        embedding_model=None,
    )
    message = SimpleNamespace(id=uuid4(), content="我有一些学习计划")

    memories = await extractor.extract_and_store(user_id=uuid4(), message=message)

    assert [memory.status for memory in memories] == [
        MemoryStatus.ACTIVE,
        MemoryStatus.PENDING,
    ]
    assert all(memory.embedding is None for memory in memories)
    assert len(session.added) == 2
