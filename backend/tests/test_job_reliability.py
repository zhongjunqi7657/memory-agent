from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.jobs.worker import (
    mark_job_failure,
    record_extraction_completed,
    record_extraction_failed,
)
from app.persistence.models import (
    ExtractionJobStatus,
    Memory,
    MemorySensitivity,
    MemoryStatus,
)
from app.persistence.repositories import ConversationRepository


def job(*, attempts: int):
    return SimpleNamespace(
        attempts=attempts,
        status=ExtractionJobStatus.RUNNING,
        available_at=None,
        locked_at=datetime.now(timezone.utc),
        locked_by="worker-a",
        last_error=None,
        finished_at=None,
    )


def test_failed_job_is_requeued_with_backoff_before_retry_limit() -> None:
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    claimed = job(attempts=1)

    mark_job_failure(
        claimed, RuntimeError("temporary"), now=timestamp, max_retries=3
    )

    assert claimed.status is ExtractionJobStatus.QUEUED
    assert claimed.available_at == datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc)
    assert claimed.locked_at is None
    assert claimed.last_error == "temporary"


def test_failed_job_becomes_terminal_after_retry_limit() -> None:
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    claimed = job(attempts=3)

    mark_job_failure(claimed, ValueError("bad payload"), now=timestamp, max_retries=3)

    assert claimed.status is ExtractionJobStatus.FAILED
    assert claimed.finished_at == timestamp
    assert claimed.locked_by is None


def test_failed_job_redacts_secrets_before_persistence() -> None:
    claimed = job(attempts=3)

    mark_job_failure(
        claimed,
        ValueError("provider api_key=sk-example-value-123456"),
        now=datetime(2026, 1, 1, tzinfo=timezone.utc),
        max_retries=3,
    )

    assert "sk-example" not in claimed.last_error
    assert "[REDACTED]" in claimed.last_error


class FakeEventRepository:
    def __init__(self) -> None:
        self.events = []

    async def next_run_event_sequence(self, _run_id) -> int:
        return len(self.events) + 7

    async def add_run_event(self, run_id, **event) -> None:
        self.events.append({"run_id": run_id, **event})


@pytest.mark.asyncio
async def test_completed_extraction_describes_created_and_conflicting_memories() -> None:
    repository = FakeEventRepository()
    message_id = uuid4()
    run_id = uuid4()
    previous_id = uuid4()
    created = Memory(
        id=uuid4(),
        user_id=uuid4(),
        content="用户喜欢先理解原理",
        status=MemoryStatus.ACTIVE,
        sensitivity=MemorySensitivity.NORMAL,
        confidence=0.95,
        source_message_id=message_id,
        metadata_={},
    )
    conflict = Memory(
        id=uuid4(),
        user_id=created.user_id,
        content="用户当前目标是参加秋招",
        status=MemoryStatus.PENDING,
        sensitivity=MemorySensitivity.NORMAL,
        confidence=0.96,
        source_message_id=message_id,
        metadata_={"conflicts_with": [str(previous_id)]},
    )
    extraction_job = SimpleNamespace(
        payload={"run_id": str(run_id)}, message_id=message_id
    )

    await record_extraction_completed(
        repository, extraction_job, [created, conflict]
    )

    event = repository.events[0]
    assert event["event_type"] == "memory.extraction_completed"
    assert [change["action"] for change in event["payload"]["changes"]] == [
        "created",
        "conflict_pending",
    ]
    assert event["payload"]["changes"][1]["conflicts_with"] == [str(previous_id)]


@pytest.mark.asyncio
async def test_terminal_extraction_failure_records_replayable_event() -> None:
    repository = FakeEventRepository()
    run_id = uuid4()
    extraction_job = SimpleNamespace(
        payload={"run_id": str(run_id)}, attempts=3
    )

    await record_extraction_failed(repository, extraction_job)

    assert repository.events == [
        {
            "run_id": run_id,
            "sequence": 7,
            "event_type": "memory.extraction_failed",
            "payload": {"message": "记忆提取失败，请稍后重试", "attempts": 3},
        }
    ]


class FakeQueueSession:
    def __init__(self, existing=None):
        self.added = []
        self.existing = existing
        self.flush_count = 0

    async def scalar(self, _statement):
        return self.existing

    def add(self, item):
        self.added.append(item)

    async def flush(self):
        self.flush_count += 1


@pytest.mark.asyncio
async def test_embedding_compensation_uses_durable_job_queue() -> None:
    session = FakeQueueSession()
    memory = Memory(id=uuid4(), user_id=uuid4(), content="用户偏好先理解原理")

    queued = await ConversationRepository(session).queue_embedding_job(
        memory=memory,
        message_id=uuid4(),
        conversation_id=uuid4(),
    )

    assert queued.idempotency_key.startswith(f"embedding:{memory.id}:")
    assert queued.payload == {
        "job_type": "embedding_backfill",
        "memory_id": str(memory.id),
        "run_id": None,
    }
    assert session.added == [queued]


@pytest.mark.asyncio
async def test_completed_embedding_job_is_requeued_when_vector_is_missing() -> None:
    existing = SimpleNamespace(
        status=ExtractionJobStatus.COMPLETED,
        attempts=3,
        available_at=None,
        locked_at=datetime.now(timezone.utc),
        locked_by="worker-a",
        finished_at=datetime.now(timezone.utc),
        last_error="previous failure",
    )
    session = FakeQueueSession(existing)
    memory = Memory(
        id=uuid4(), user_id=uuid4(), content="用户偏好先理解原理", embedding=None
    )

    queued = await ConversationRepository(session).queue_embedding_job(
        memory=memory,
        message_id=uuid4(),
        conversation_id=uuid4(),
    )

    assert queued is existing
    assert existing.status is ExtractionJobStatus.QUEUED
    assert existing.attempts == 0
    assert existing.locked_at is None
    assert existing.locked_by is None
    assert existing.finished_at is None
    assert existing.last_error is None
    assert session.flush_count == 1
