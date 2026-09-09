"""Small persistence helpers kept separate from HTTP and Agent orchestration."""

from __future__ import annotations

import hashlib
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.memory.retrieval import ScoredMemory, rank_memories
from app.persistence.models import (
    Conversation,
    ExtractionJob,
    ExtractionJobStatus,
    Memory,
    MemoryStatus,
    Message,
    MessageRole,
    Run,
    RunEvent,
    RunStatus,
    User,
)


class ConversationRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create_user(self, external_key: str) -> User:
        user = await self.session.scalar(
            select(User).where(User.external_key == external_key)
        )
        if user:
            return user
        user = User(external_key=external_key)
        self.session.add(user)
        await self.session.flush()
        return user

    async def get_user(self, external_key: str) -> User | None:
        return await self.session.scalar(
            select(User).where(User.external_key == external_key)
        )

    async def get_conversation(
        self, conversation_id: UUID, user_id: UUID
    ) -> Conversation | None:
        return await self.session.scalar(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.user_id == user_id,
            )
        )

    async def list_conversations(
        self, user_id: UUID, *, limit: int = 50
    ) -> list[Conversation]:
        result = await self.session.scalars(
            select(Conversation)
            .where(
                Conversation.user_id == user_id,
                Conversation.is_archived.is_(False),
            )
            .order_by(Conversation.updated_at.desc())
            .limit(limit)
        )
        return list(result.all())

    async def create_conversation(
        self, user_id: UUID, *, title: str | None = None
    ) -> Conversation:
        conversation = Conversation(user_id=user_id, title=title)
        self.session.add(conversation)
        await self.session.flush()
        return conversation

    async def list_messages(
        self, conversation_id: UUID, *, limit: int = 200
    ) -> list[Message]:
        result = await self.session.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .options(selectinload(Message.run).selectinload(Run.events))
            .order_by(Message.sequence.desc())
            .limit(limit)
        )
        return list(reversed(result.all()))

    async def list_recent_messages(
        self, conversation_id: UUID, limit: int
    ) -> list[Message]:
        result = await self.session.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.sequence.desc())
            .limit(limit)
        )
        return list(reversed(result.all()))

    async def next_message_sequence(self, conversation_id: UUID) -> int:
        last_sequence = await self.session.scalar(
            select(func.max(Message.sequence)).where(
                Message.conversation_id == conversation_id
            )
        )
        return (last_sequence or 0) + 1

    async def add_message(
        self,
        conversation_id: UUID,
        *,
        role: MessageRole,
        content: str,
        sequence: int,
        is_redacted: bool = False,
        run_id: UUID | None = None,
    ) -> Message:
        message = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            sequence=sequence,
            is_redacted=is_redacted,
            run_id=run_id,
        )
        self.session.add(message)
        await self.session.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(updated_at=func.now())
        )
        await self.session.flush()
        return message

    async def create_run(self, conversation_id: UUID) -> Run:
        from datetime import datetime, timezone

        run = Run(conversation_id=conversation_id, status=RunStatus.RUNNING)
        run.started_at = datetime.now(timezone.utc)
        self.session.add(run)
        await self.session.flush()
        return run

    async def finish_run(
        self, run: Run, *, status: RunStatus, error_message: str | None = None
    ) -> None:
        run.status = status
        run.error_message = error_message
        from datetime import datetime, timezone

        run.finished_at = datetime.now(timezone.utc)
        await self.session.flush()

    async def add_run_event(
        self,
        run_id: UUID,
        *,
        sequence: int,
        event_type: str,
        payload: dict,
    ) -> RunEvent:
        event = RunEvent(
            run_id=run_id,
            sequence=sequence,
            event_type=event_type,
            payload=payload,
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def next_run_event_sequence(self, run_id: UUID) -> int:
        last_sequence = await self.session.scalar(
            select(func.max(RunEvent.sequence)).where(RunEvent.run_id == run_id)
        )
        return (last_sequence or 0) + 1

    async def list_run_events(
        self,
        run_id: UUID,
        user_id: UUID,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> list[RunEvent]:
        result = await self.session.scalars(
            select(RunEvent)
            .join(Run, Run.id == RunEvent.run_id)
            .join(Conversation, Conversation.id == Run.conversation_id)
            .where(
                RunEvent.run_id == run_id,
                Conversation.user_id == user_id,
                RunEvent.sequence > after_sequence,
            )
            .order_by(RunEvent.sequence)
            .limit(limit)
        )
        return list(result.all())

    async def queue_extraction_job(
        self,
        user_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
        *,
        payload: dict | None = None,
    ) -> ExtractionJob:
        idempotency_key = f"message:{message_id}"
        existing = await self.session.scalar(
            select(ExtractionJob).where(
                ExtractionJob.idempotency_key == idempotency_key
            )
        )
        if existing:
            return existing
        job = ExtractionJob(
            user_id=user_id,
            conversation_id=conversation_id,
            message_id=message_id,
            status=ExtractionJobStatus.QUEUED,
            idempotency_key=idempotency_key,
            payload=payload or {},
        )
        self.session.add(job)
        await self.session.flush()
        return job

    async def queue_embedding_job(
        self,
        *,
        memory: Memory,
        message_id: UUID,
        conversation_id: UUID,
        run_id: str | None = None,
    ) -> ExtractionJob:
        content_version = hashlib.sha256(memory.content.encode("utf-8")).hexdigest()[:16]
        idempotency_key = f"embedding:{memory.id}:{content_version}"
        existing = await self.session.scalar(
            select(ExtractionJob).where(
                ExtractionJob.idempotency_key == idempotency_key
            )
        )
        if existing:
            if (
                memory.embedding is None
                and existing.status
                in {ExtractionJobStatus.COMPLETED, ExtractionJobStatus.FAILED}
            ):
                existing.status = ExtractionJobStatus.QUEUED
                existing.attempts = 0
                existing.available_at = func.now()
                existing.locked_at = None
                existing.locked_by = None
                existing.finished_at = None
                existing.last_error = None
                await self.session.flush()
            return existing
        job = ExtractionJob(
            user_id=memory.user_id,
            conversation_id=conversation_id,
            message_id=message_id,
            status=ExtractionJobStatus.QUEUED,
            idempotency_key=idempotency_key,
            payload={
                "job_type": "embedding_backfill",
                "memory_id": str(memory.id),
                "run_id": run_id,
            },
        )
        self.session.add(job)
        await self.session.flush()
        return job


class MemoryRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_for_user(
        self,
        user_id: UUID,
        *,
        status: MemoryStatus | None = None,
        limit: int = 50,
    ) -> list[Memory]:
        statement = (
            select(Memory)
            .where(Memory.user_id == user_id)
            .order_by(Memory.updated_at.desc())
            .limit(limit)
        )
        if status:
            statement = statement.where(Memory.status == status)
        result = await self.session.scalars(statement)
        return list(result.all())

    async def get_for_user(self, memory_id: UUID, user_id: UUID) -> Memory | None:
        return await self.session.scalar(
            select(Memory).where(Memory.id == memory_id, Memory.user_id == user_id)
        )

    async def set_status(self, memory: Memory, status: MemoryStatus) -> Memory:
        memory.status = status
        await self.session.flush()
        return memory

    async def search_keyword(
        self, user_id: UUID, query: str, *, limit: int
    ) -> list[Memory]:
        scored = await self.search_hybrid(user_id, query, limit=limit)
        return [item.memory for item in scored]

    async def search_hybrid(
        self,
        user_id: UUID,
        query: str,
        *,
        query_embedding: list[float] | None = None,
        limit: int,
        vector_weight: float = 0.55,
        keyword_weight: float = 0.20,
        recency_weight: float = 0.10,
        importance_weight: float = 0.10,
        type_weight: float = 0.05,
        recency_half_life_days: int = 30,
        min_relevance_score: float = 0.08,
    ) -> list[ScoredMemory]:
        candidate_limit = max(limit * 10, 50)
        active_filter = (
            Memory.user_id == user_id,
            Memory.status == MemoryStatus.ACTIVE,
        )
        recent_statement = (
            select(Memory)
            .where(*active_filter)
            .order_by(Memory.updated_at.desc())
            .limit(candidate_limit)
        )
        recent = list((await self.session.scalars(recent_statement)).all())
        candidates = {memory.id: memory for memory in recent}

        if query_embedding and self._supports_vector_search():
            distance = Memory.embedding.cosine_distance(query_embedding)
            vector_statement = (
                select(Memory)
                .where(*active_filter, Memory.embedding.is_not(None))
                .order_by(distance)
                .limit(candidate_limit)
            )
            vector_candidates = await self.session.scalars(vector_statement)
            candidates.update(
                (memory.id, memory) for memory in vector_candidates.all()
            )

        return rank_memories(
            list(candidates.values()),
            query,
            query_embedding=query_embedding,
            vector_weight=vector_weight,
            keyword_weight=keyword_weight,
            recency_weight=recency_weight,
            importance_weight=importance_weight,
            type_weight=type_weight,
            recency_half_life_days=recency_half_life_days,
            min_relevance_score=min_relevance_score,
            limit=limit,
        )

    async def list_timeline(self, user_id: UUID, *, limit: int = 100) -> list[Memory]:
        result = await self.session.scalars(
            select(Memory)
            .where(
                Memory.user_id == user_id,
                Memory.status == MemoryStatus.ACTIVE,
            )
            .order_by(Memory.valid_from.desc(), Memory.created_at.desc())
            .limit(limit)
        )
        return list(result.all())

    def _supports_vector_search(self) -> bool:
        try:
            return self.session.get_bind().dialect.name == "postgresql"
        except (AttributeError, RuntimeError):
            return False

    async def soft_delete_matching(
        self, user_id: UUID, query: str, *, limit: int = 10
    ) -> list[Memory]:
        """Soft-delete active memories matched by an explicit forget command."""

        matches = await self.search_hybrid(user_id, query, limit=limit)
        deleted: list[Memory] = []
        for item in matches:
            item.memory.status = MemoryStatus.DELETED
            deleted.append(item.memory)
        if deleted:
            await self.session.flush()
        return deleted
