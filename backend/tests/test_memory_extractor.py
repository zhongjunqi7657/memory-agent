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


class RepairingStructuredModel(FakeStructuredModel):
    def __init__(self, result: ExtractionResult):
        super().__init__(result)
        self.calls = 0

    async def ainvoke(self, _messages):
        self.calls += 1
        if self.calls == 1:
            raise ValueError("invalid structured output")
        return self.result


class FakeSession:
    def __init__(self, *, scalar_batches=None):
        self.added = []
        self.scalar_batches = list(scalar_batches or [])

    async def scalars(self, _statement):
        return self.scalar_batches.pop(0) if self.scalar_batches else []

    async def scalar(self, _statement):
        return None

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


@pytest.mark.asyncio
async def test_extractor_repairs_invalid_structured_output_once():
    session = FakeSession()
    model = RepairingStructuredModel(
        ExtractionResult(
            memories=[
                ExtractedMemory(
                    content="用户正在准备后端面试",
                    kind=MemoryKind.EPISODIC,
                    confidence=0.95,
                )
            ]
        )
    )
    extractor = MemoryExtractor(session, model=model, embedding_model=None)

    memories = await extractor.extract_and_store(
        user_id=uuid4(),
        message=SimpleNamespace(
            id=uuid4(), conversation_id=uuid4(), content="我正在准备后端面试"
        ),
    )

    assert model.calls == 2
    assert len(memories) == 1
    assert memories[0].status is MemoryStatus.ACTIVE


@pytest.mark.asyncio
async def test_ordinary_extraction_marks_declared_conflict_pending():
    user_id = uuid4()
    previous = SimpleNamespace(
        id=uuid4(),
        user_id=user_id,
        content="用户当前目标是准备考研",
        canonical_key="current_goal",
        status=MemoryStatus.ACTIVE,
        updated_at=None,
    )
    session = FakeSession(scalar_batches=[[previous], [previous]])
    model = FakeStructuredModel(
        ExtractionResult(
            memories=[
                ExtractedMemory(
                    content="用户当前目标是参加秋招",
                    kind=MemoryKind.SEMANTIC,
                    confidence=0.97,
                    canonical_key="current_goal",
                    contradicts_memory_ids=[previous.id],
                )
            ]
        )
    )

    memories = await MemoryExtractor(
        session, model=model, embedding_model=None
    ).extract_and_store(
        user_id=user_id,
        message=SimpleNamespace(
            id=uuid4(), conversation_id=uuid4(), content="我现在决定参加秋招"
        ),
    )

    assert memories[0].status is MemoryStatus.PENDING
    assert memories[0].metadata_["conflicts_with"] == [str(previous.id)]
