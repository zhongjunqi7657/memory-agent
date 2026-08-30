"""Small persistence helpers kept separate from HTTP and Agent orchestration."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

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

    async def create_conversation(self, user_id: UUID) -> Conversation:
        conversation = Conversation(user_id=user_id)
        self.session.add(conversation)
        await self.session.flush()
        return conversation

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
    ) -> Message:
        message = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            sequence=sequence,
            is_redacted=is_redacted,
        )
        self.session.add(message)
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
        vector_weight: float = 0.7,
        keyword_weight: float = 0.3,
    ) -> list[ScoredMemory]:
        statement = (
            select(Memory)
            .where(Memory.user_id == user_id, Memory.status == MemoryStatus.ACTIVE)
            .order_by(Memory.updated_at.desc())
            .limit(max(limit * 20, 100))
        )
        result = await self.session.scalars(statement)
        return rank_memories(
            list(result.all()),
            query,
            query_embedding=query_embedding,
            vector_weight=vector_weight,
            keyword_weight=keyword_weight,
            limit=limit,
        )

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
