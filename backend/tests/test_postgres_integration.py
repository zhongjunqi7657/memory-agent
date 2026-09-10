"""Integration coverage for the migrated PostgreSQL and pgvector schema."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import ClassVar
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.language_models.fake_chat_models import FakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from sqlalchemy import delete, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import app.api.chat as chat_api
from app.agent import service as agent_service
from app.agent.service import AgentService
from app.config.business import get_business_config
from app.config.settings import Settings
from app.jobs import worker
from app.main import app
from app.memory.extractor import ExtractedMemory, ExtractionResult, MemoryExtractor
from app.memory.service import MemoryService
from app.persistence.db import get_session, get_session_factory
from app.persistence.models import (
    ExtractionJob,
    ExtractionJobStatus,
    Memory,
    MemoryKind,
    MemorySensitivity,
    MemoryStatus,
    MessageRole,
    User,
)
from app.persistence.repositories import ConversationRepository, MemoryRepository


class RecordingChatModel(FakeChatModel):
    prompts: ClassVar[list[str]] = []

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.prompts.append("\n".join(str(message.content) for message in messages))
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(content="可以先看示例，再自己改写一遍。")
                )
            ]
        )


@pytest.fixture
async def postgres_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    database_name = make_url(database_url).database or ""
    if not database_name.endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must target a database ending in '_test'")

    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except OSError as error:
        await engine.dispose()
        pytest.skip(f"PostgreSQL is unavailable: {error}")

    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.asyncio
async def test_migrated_schema_vector_io_and_worker_locking(
    postgres_factory: async_sessionmaker[AsyncSession], monkeypatch
) -> None:
    async with postgres_factory() as session:
        extension = await session.scalar(
            text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
        )
        embedding_type = await session.scalar(
            text(
                "SELECT format_type(a.atttypid, a.atttypmod) "
                "FROM pg_attribute a "
                "JOIN pg_class c ON c.oid = a.attrelid "
                "WHERE c.relname = 'memories' AND a.attname = 'embedding'"
            )
        )
        job_columns = set(
            (
                await session.scalars(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = 'public' "
                        "AND table_name = 'extraction_jobs'"
                    )
                )
            ).all()
        )
        memory_columns = set(
            (
                await session.scalars(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name = 'memories'"
                    )
                )
            ).all()
        )
        message_columns = set(
            (
                await session.scalars(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name = 'messages'"
                    )
                )
            ).all()
        )
    assert extension == "vector"
    assert embedding_type == "vector(1024)"
    assert {"available_at", "locked_at", "locked_by", "idempotency_key"} <= job_columns
    assert {"importance", "source_message_ids", "conversation_id"} <= memory_columns
    assert "run_id" in message_columns

    external_key = f"postgres-integration-{uuid4()}"
    vector = [1.0, *([0.0] * 1023)]
    try:
        async with postgres_factory() as session, session.begin():
            repository = ConversationRepository(session)
            user = await repository.get_or_create_user(external_key)
            conversation = await repository.create_conversation(user.id)
            first_message = await repository.add_message(
                conversation.id,
                role=MessageRole.USER,
                content="我喜欢通过实例学习",
                sequence=1,
            )
            second_message = await repository.add_message(
                conversation.id,
                role=MessageRole.USER,
                content="继续给我一个练习",
                sequence=2,
            )
            memory = Memory(
                user_id=user.id,
                kind=MemoryKind.SEMANTIC,
                status=MemoryStatus.ACTIVE,
                sensitivity=MemorySensitivity.NORMAL,
                content="用户喜欢通过实例学习",
                confidence=Decimal("1.000"),
                importance=Decimal("0.800"),
                source_message_id=first_message.id,
                source_message_ids=[str(first_message.id)],
                conversation_id=conversation.id,
                embedding=vector,
            )
            session.add(memory)
            await session.flush()
            first_job = await repository.queue_extraction_job(
                user.id, conversation.id, first_message.id
            )
            duplicate_job = await repository.queue_extraction_job(
                user.id, conversation.id, first_message.id
            )
            second_job = await repository.queue_extraction_job(
                user.id, conversation.id, second_message.id
            )
            user_id = user.id
            memory_id = memory.id
            first_job_id = first_job.id
            second_job_id = second_job.id
            assert duplicate_job.id == first_job.id

        async with postgres_factory() as session:
            loaded_memory = await session.get(Memory, memory_id)
            matches = await MemoryRepository(session).search_hybrid(
                user_id,
                "实例学习",
                query_embedding=vector,
                limit=5,
            )
            assert loaded_memory is not None
            assert list(loaded_memory.embedding) == vector
            assert [match.memory.id for match in matches] == [memory_id]

        monkeypatch.setattr(worker, "SessionFactory", postgres_factory)
        async with postgres_factory() as locking_session, locking_session.begin():
            await locking_session.scalar(
                select(ExtractionJob)
                .where(ExtractionJob.id == first_job_id)
                .with_for_update()
            )
            claimed_id = await worker.claim_next_job(worker_id="test-worker")
            assert claimed_id == second_job_id

        async with postgres_factory() as session:
            claimed_job = await session.get(ExtractionJob, second_job_id)
            assert claimed_job is not None
            assert claimed_job.status is ExtractionJobStatus.RUNNING
            assert claimed_job.locked_by == "test-worker"
    finally:
        async with postgres_factory() as session, session.begin():
            await session.execute(delete(User).where(User.external_key == external_key))


@pytest.mark.asyncio
async def test_postgres_ordinary_chat_conflict_lifecycle(
    postgres_factory: async_sessionmaker[AsyncSession],
) -> None:
    external_key = f"postgres-conflict-{uuid4()}"
    foreign_key = f"postgres-conflict-foreign-{uuid4()}"
    vector = [1.0, *([0.0] * 1023)]
    unrelated_vector = [0.0, 1.0, *([0.0] * 1022)]

    class ConflictModel:
        def __init__(self, previous_id) -> None:
            self.previous_id = previous_id
            self.prompts: list[str] = []

        def with_structured_output(self, _schema):
            return self

        async def ainvoke(self, messages):
            self.prompts.append("\n".join(str(item.content) for item in messages))
            return ExtractionResult(
                memories=[
                    ExtractedMemory(
                        content="用户目前住在上海",
                        kind=MemoryKind.SEMANTIC,
                        confidence=0.98,
                        contradicts_memory_ids=[self.previous_id],
                    )
                ]
            )

    class EmbeddingModel:
        async def aembed_query(self, _query):
            return vector

    try:
        async with postgres_factory() as session, session.begin():
            repository = ConversationRepository(session)
            user = await repository.get_or_create_user(external_key)
            foreign_user = await repository.get_or_create_user(foreign_key)
            conversation = await repository.create_conversation(user.id)
            message = await repository.add_message(
                conversation.id,
                role=MessageRole.USER,
                content="我已经搬到上海生活了",
                sequence=1,
            )
            previous = Memory(
                user_id=user.id,
                kind=MemoryKind.SEMANTIC,
                status=MemoryStatus.ACTIVE,
                sensitivity=MemorySensitivity.NORMAL,
                content="用户目前住在北京",
                confidence=Decimal("0.980"),
                importance=Decimal("0.900"),
                embedding=vector,
            )
            unrelated = Memory(
                user_id=user.id,
                kind=MemoryKind.SEMANTIC,
                status=MemoryStatus.ACTIVE,
                sensitivity=MemorySensitivity.NORMAL,
                content="用户喜欢读科幻小说",
                confidence=Decimal("0.950"),
                importance=Decimal("0.800"),
                embedding=unrelated_vector,
            )
            pending = Memory(
                user_id=user.id,
                kind=MemoryKind.SEMANTIC,
                status=MemoryStatus.PENDING,
                sensitivity=MemorySensitivity.NORMAL,
                content="用户可能住在杭州",
                confidence=Decimal("0.800"),
                importance=Decimal("0.500"),
                embedding=vector,
            )
            deleted = Memory(
                user_id=user.id,
                kind=MemoryKind.SEMANTIC,
                status=MemoryStatus.DELETED,
                sensitivity=MemorySensitivity.NORMAL,
                content="用户曾住在广州",
                confidence=Decimal("0.950"),
                importance=Decimal("0.500"),
                embedding=vector,
            )
            foreign = Memory(
                user_id=foreign_user.id,
                kind=MemoryKind.SEMANTIC,
                status=MemoryStatus.ACTIVE,
                sensitivity=MemorySensitivity.NORMAL,
                content="用户目前住在上海",
                confidence=Decimal("0.990"),
                importance=Decimal("0.900"),
                embedding=vector,
            )
            session.add_all([previous, unrelated, pending, deleted, foreign])
            await session.flush()

            model = ConflictModel(previous.id)
            extracted = await MemoryExtractor(
                session,
                model=model,
                embedding_model=EmbeddingModel(),
            ).extract_and_store(user_id=user.id, message=message)

            assert len(extracted) == 1
            replacement = extracted[0]
            assert replacement.status is MemoryStatus.PENDING
            assert replacement.metadata_["conflicts_with"] == [str(previous.id)]
            assert previous.status is MemoryStatus.ACTIVE
            assert model.prompts[0].count(str(previous.id)) == 1
            assert str(pending.id) not in model.prompts[0]
            assert str(deleted.id) not in model.prompts[0]
            assert str(foreign.id) not in model.prompts[0]

            await MemoryService(session).update_memory(
                replacement, status=MemoryStatus.ACTIVE
            )
            assert previous.status is MemoryStatus.SUPERSEDED
            assert previous.superseded_by_id == replacement.id

            await MemoryService(session).undo(replacement)
            assert replacement.status is MemoryStatus.PENDING
            assert previous.status is MemoryStatus.ACTIVE
            assert previous.superseded_by_id is None

            await MemoryService(session).update_memory(
                replacement, status=MemoryStatus.REJECTED
            )
            assert replacement.status is MemoryStatus.REJECTED
            assert previous.status is MemoryStatus.ACTIVE
    finally:
        async with postgres_factory() as session, session.begin():
            await session.execute(
                delete(User).where(User.external_key.in_([external_key, foreign_key]))
            )


@pytest.mark.asyncio
async def test_sse_chat_recalls_memory_across_conversations(
    postgres_factory: async_sessionmaker[AsyncSession], monkeypatch
) -> None:
    external_key = f"sse-integration-{uuid4()}"
    model = RecordingChatModel()
    settings = Settings(dashscope_api_key="")
    model_factory_calls = 0

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with postgres_factory() as session:
            yield session

    def create_agent(session: AsyncSession) -> AgentService:
        return AgentService(session, settings=settings)

    def create_model(**_kwargs):
        nonlocal model_factory_calls
        model_factory_calls += 1
        return model

    class EmptyMemoryExtractor:
        def __init__(self, _session) -> None:
            pass

        async def extract_and_store(self, **_kwargs):
            return []

    class FailingMemoryExtractor:
        def __init__(self, _session) -> None:
            pass

        async def extract_and_store(self, **_kwargs):
            raise RuntimeError("provider api_key=sk-example-value-123456")

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_session_factory] = lambda: postgres_factory
    monkeypatch.setattr(chat_api, "AgentService", create_agent)
    monkeypatch.setattr(agent_service, "create_chat_model", create_model)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            saved = await client.post(
                "/v1/chat/stream",
                json={
                    "user_key": external_key,
                    "content": "请记住我的学习方式是通过实例学习",
                },
            )
            recalled = await client.post(
                "/v1/chat/stream",
                json={"user_key": external_key, "content": "按照我的学习方式给个建议"},
            )

            events = [
                json.loads(line.removeprefix("data: "))
                for line in recalled.text.splitlines()
                if line.startswith("data: ")
            ]
            event_types = [event["event_type"] for event in events]
            completed = next(
                event for event in events if event["event_type"] == "run.completed"
            )
            monkeypatch.setattr(worker, "SessionFactory", postgres_factory)
            monkeypatch.setattr(worker, "MemoryExtractor", EmptyMemoryExtractor)
            assert await worker.process_one() is True
            replayed = await client.get(
                f"/v1/runs/{completed['run_id']}/events",
                params={"user_key": external_key, "after_sequence": 1},
            )
            restored = await client.get(
                f"/v1/conversations/{completed['payload']['conversation_id']}/messages",
                params={"user_key": external_key},
            )
            isolated = await client.get(
                f"/v1/runs/{completed['run_id']}/events",
                params={"user_key": "another-user"},
            )

            failed = await client.post(
                "/v1/chat/stream",
                json={
                    "user_key": external_key,
                    "conversation_id": completed["payload"]["conversation_id"],
                    "content": "今天继续学习",
                },
            )
            failed_events = [
                json.loads(line.removeprefix("data: "))
                for line in failed.text.splitlines()
                if line.startswith("data: ")
            ]
            failed_run = next(
                event
                for event in failed_events
                if event["event_type"] == "run.completed"
            )
            async with postgres_factory() as session, session.begin():
                failed_job = await session.scalar(
                    select(ExtractionJob).where(
                        ExtractionJob.payload["run_id"].astext
                        == failed_run["run_id"]
                    )
                )
                assert failed_job is not None
                failed_job.attempts = get_business_config().memory.max_retries - 1
            monkeypatch.setattr(worker, "MemoryExtractor", FailingMemoryExtractor)
            assert await worker.process_one() is True
            failed_replay = await client.get(
                f"/v1/runs/{failed_run['run_id']}/events",
                params={"user_key": external_key},
            )

        assert saved.status_code == 200
        assert recalled.status_code == 200
        assert event_types == [
            "run.started",
            "memory.retrieved",
            "session.loaded",
            "model.completed",
            "memory.extraction_queued",
            "run.completed",
        ]
        assert completed["payload"]["memory_count"] == 1
        assert "用户的学习方式是通过实例学习" in model.prompts[-1]
        assert model_factory_calls == 2
        assert replayed.status_code == 200
        assert [event["sequence"] for event in replayed.json()] == [2, 3, 4, 5, 6, 7]
        assert replayed.json()[-1] == {
            "run_id": completed["run_id"],
            "sequence": 7,
            "event_type": "memory.extraction_completed",
            "payload": {"changes": []},
        }
        assert restored.status_code == 200
        restored_assistant = [
            message for message in restored.json() if message["role"] == "assistant"
        ][-1]
        assert restored_assistant["run_id"] == completed["run_id"]
        assert restored_assistant["run_events"][-1]["event_type"] == (
            "memory.extraction_completed"
        )
        assert isolated.status_code == 200
        assert isolated.json() == []
        assert failed.status_code == 200
        assert failed_replay.status_code == 200
        terminal_failure = failed_replay.json()[-1]
        assert terminal_failure["event_type"] == "memory.extraction_failed"
        assert terminal_failure["payload"] == {
            "message": "记忆提取失败，请稍后重试",
            "attempts": get_business_config().memory.max_retries,
        }
    finally:
        app.dependency_overrides.clear()
        async with postgres_factory() as session, session.begin():
            await session.execute(delete(User).where(User.external_key == external_key))
