from uuid import uuid4

import pytest

from app.memory.policy import MemoryCandidate
from app.memory.service import MemoryService
from app.persistence.models import Memory, MemoryKind, MemorySensitivity, MemoryStatus
from app.persistence.repositories import MemoryRepository


class FakeSession:
    def __init__(self, *, scalar_results=None, single_results=None):
        self.added = []
        self.scalar_results = scalar_results or []
        self.single_results = list(single_results or [])

    async def scalar(self, _statement):
        return self.single_results.pop(0) if self.single_results else None

    async def scalars(self, _statement):
        return list(self.scalar_results)

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


@pytest.mark.asyncio
async def test_ordinary_conflict_waits_for_confirmation_and_keeps_provenance():
    user_id = uuid4()
    conversation_id = uuid4()
    source_message_id = uuid4()
    previous = Memory(
        id=uuid4(),
        user_id=user_id,
        content="用户当前目标是准备考研",
        canonical_key="current_goal",
        status=MemoryStatus.ACTIVE,
        kind=MemoryKind.SEMANTIC,
        sensitivity=MemorySensitivity.NORMAL,
        confidence=0.95,
        importance=0.8,
    )
    session = FakeSession(scalar_results=[previous])

    candidate = await MemoryService(session).add_candidate(
        user_id=user_id,
        candidate=MemoryCandidate(
            content="用户当前目标是参加秋招",
            kind=MemoryKind.SEMANTIC,
            confidence=0.96,
            canonical_key="current_goal",
            importance=0.9,
        ),
        source_message_id=source_message_id,
        conversation_id=conversation_id,
    )

    assert candidate is not None
    assert candidate.status is MemoryStatus.PENDING
    assert candidate.metadata_["conflicts_with"] == [str(previous.id)]
    assert candidate.source_message_ids == [str(source_message_id)]
    assert candidate.conversation_id == conversation_id
    assert previous.status is MemoryStatus.ACTIVE


@pytest.mark.asyncio
async def test_confirming_conflict_supersedes_previous_version():
    user_id = uuid4()
    previous = Memory(
        id=uuid4(),
        user_id=user_id,
        content="用户当前目标是准备考研",
        status=MemoryStatus.ACTIVE,
        kind=MemoryKind.SEMANTIC,
        sensitivity=MemorySensitivity.NORMAL,
        confidence=0.95,
        importance=0.8,
    )
    pending = Memory(
        id=uuid4(),
        user_id=user_id,
        content="用户当前目标是参加秋招",
        status=MemoryStatus.PENDING,
        kind=MemoryKind.SEMANTIC,
        sensitivity=MemorySensitivity.NORMAL,
        confidence=0.96,
        importance=0.9,
        metadata_={"conflicts_with": [str(previous.id)]},
    )
    session = FakeSession(scalar_results=[previous])

    await MemoryService(session).update_memory(pending, status=MemoryStatus.ACTIVE)

    assert pending.status is MemoryStatus.ACTIVE
    assert previous.status is MemoryStatus.SUPERSEDED
    assert previous.superseded_by_id == pending.id

    session.scalar_results = [previous]
    await MemoryService(session).undo(pending)

    assert pending.status is MemoryStatus.PENDING
    assert previous.status is MemoryStatus.ACTIVE
    assert previous.superseded_by_id is None


@pytest.mark.asyncio
async def test_repeated_evidence_merges_source_ids_without_duplicate_memory():
    user_id = uuid4()
    first_source = uuid4()
    second_source = uuid4()
    existing = Memory(
        id=uuid4(),
        user_id=user_id,
        content="用户喜欢先理解原理",
        status=MemoryStatus.ACTIVE,
        kind=MemoryKind.SEMANTIC,
        sensitivity=MemorySensitivity.NORMAL,
        confidence=0.9,
        importance=0.5,
        source_message_id=first_source,
        source_message_ids=[str(first_source)],
        metadata_={},
    )
    session = FakeSession(single_results=[None, existing])

    merged = await MemoryService(session).add_candidate(
        user_id=user_id,
        candidate=MemoryCandidate(
            content="用户喜欢先理解原理",
            kind=MemoryKind.SEMANTIC,
            confidence=0.96,
            importance=0.8,
        ),
        source_message_id=second_source,
    )

    assert merged is existing
    assert merged.source_message_ids == [str(first_source), str(second_source)]
    assert merged.metadata_["evidence_count"] == 2
    assert session.added == []


@pytest.mark.asyncio
async def test_tool_proposal_does_not_demote_identical_active_memory():
    existing = Memory(
        id=uuid4(),
        user_id=uuid4(),
        content="用户喜欢先理解原理",
        status=MemoryStatus.ACTIVE,
        kind=MemoryKind.SEMANTIC,
        sensitivity=MemorySensitivity.NORMAL,
        confidence=0.95,
        importance=0.7,
        source_message_ids=[],
        metadata_={},
    )
    session = FakeSession(single_results=[None, existing])

    proposed = await MemoryService(session).propose_update(
        user_id=existing.user_id,
        content=existing.content,
        source_message_id=uuid4(),
    )

    assert proposed is existing
    assert existing.status is MemoryStatus.ACTIVE
    assert "proposal" not in existing.metadata_


@pytest.mark.asyncio
async def test_undo_content_change_invalidates_current_embedding():
    memory = Memory(
        id=uuid4(),
        user_id=uuid4(),
        content="用户喜欢直接看示例",
        status=MemoryStatus.ACTIVE,
        kind=MemoryKind.SEMANTIC,
        sensitivity=MemorySensitivity.NORMAL,
        confidence=0.95,
        importance=0.8,
        embedding=[0.2, 0.4],
        metadata_={
            "embedding_status": "ready",
            "undo": {
                "status": MemoryStatus.ACTIVE.value,
                "content": "用户喜欢先理解原理",
                "importance": 0.7,
            },
        },
    )

    await MemoryService(FakeSession()).undo(memory)

    assert memory.content == "用户喜欢先理解原理"
    assert memory.embedding is None
    assert memory.metadata_["embedding_status"] == "pending"
