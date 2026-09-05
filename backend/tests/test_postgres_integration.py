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
from app.config.settings import Settings
from app.jobs import worker
from app.main import app
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
    assert extension == "vector"
    assert embedding_type == "vector(1024)"
    assert {"available_at", "locked_at", "locked_by", "idempotency_key"} <= job_columns

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
                source_message_id=first_message.id,
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
            replayed = await client.get(
                f"/v1/runs/{completed['run_id']}/events",
                params={"user_key": external_key, "after_sequence": 1},
            )

        assert saved.status_code == 200
        assert recalled.status_code == 200
        assert event_types == [
            "run.started",
            "memory.retrieved",
            "model.completed",
            "memory.extraction_queued",
            "run.completed",
        ]
        assert completed["payload"]["memory_count"] == 1
        assert "用户的学习方式是通过实例学习" in model.prompts[-1]
        assert model_factory_calls == 1
        assert replayed.status_code == 200
        assert [event["sequence"] for event in replayed.json()] == [2, 3, 4, 5]
    finally:
        app.dependency_overrides.clear()
        async with postgres_factory() as session, session.begin():
            await session.execute(delete(User).where(User.external_key == external_key))
