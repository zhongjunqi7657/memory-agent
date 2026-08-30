"""Single entry point for governed memory writes and lifecycle changes."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.business import BusinessConfig, get_business_config
from app.memory.policy import MemoryCandidate, MemoryDecision, assess_candidate
from app.persistence.models import Memory, MemoryKind, MemoryStatus
from app.persistence.repositories import MemoryRepository


class MemoryService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        business: BusinessConfig | None = None,
        embedding_model=None,
    ):
        self.session = session
        self.business = business or get_business_config()
        self.embedding_model = embedding_model

    async def add_candidate(
        self,
        *,
        user_id: UUID,
        candidate: MemoryCandidate,
        source_message_id: UUID | None,
    ) -> Memory | None:
        assessment = assess_candidate(candidate, config=self.business.memory)
        if assessment.decision is MemoryDecision.REJECT:
            return None
        if source_message_id:
            existing_query = select(Memory).where(
                Memory.user_id == user_id,
                Memory.source_message_id == source_message_id,
                Memory.status != MemoryStatus.DELETED,
            )
            if candidate.canonical_key:
                existing_query = existing_query.where(
                    Memory.canonical_key == candidate.canonical_key
                )
            else:
                existing_query = existing_query.where(
                    Memory.content == assessment.content
                )
            existing = await self.session.scalar(existing_query)
            if existing:
                return existing
        memory = Memory(
            user_id=user_id,
            kind=candidate.kind,
            status=(
                MemoryStatus.ACTIVE
                if assessment.decision is MemoryDecision.ACTIVE
                else MemoryStatus.PENDING
            ),
            sensitivity=assessment.sensitivity,
            content=assessment.content,
            canonical_key=candidate.canonical_key,
            confidence=Decimal(str(assessment.confidence)),
            source_message_id=source_message_id,
            valid_from=datetime.now(timezone.utc),
            metadata_={"reason": assessment.reason, "explicit": candidate.explicit},
        )
        if self.embedding_model:
            try:
                embedding = await self.embedding_model.aembed_query(assessment.content)
                if len(embedding) == self.business.models.embedding_dimensions:
                    memory.embedding = embedding
            except Exception:  # noqa: BLE001 - text memory remains usable without vectors
                memory.embedding = None
        self.session.add(memory)
        await self.session.flush()
        if assessment.decision is MemoryDecision.ACTIVE and candidate.canonical_key:
            await self._supersede_previous(
                user_id, candidate.canonical_key, superseded_by_id=memory.id
            )
        return memory

    async def add_explicit(
        self,
        *,
        user_id: UUID,
        content: str,
        source_message_id: UUID | None,
        kind: MemoryKind = MemoryKind.SEMANTIC,
        canonical_key: str | None = None,
    ) -> Memory | None:
        normalized = content.strip()
        if not normalized:
            return None
        if normalized.startswith("我的"):
            normalized = f"用户的{normalized[2:]}"
        elif normalized.startswith("我"):
            normalized = f"用户{normalized[1:]}"
        elif not normalized.startswith("用户"):
            normalized = f"用户{normalized}"
        return await self.add_candidate(
            user_id=user_id,
            candidate=MemoryCandidate(
                content=normalized,
                kind=kind,
                confidence=1.0,
                explicit=True,
                canonical_key=canonical_key,
            ),
            source_message_id=source_message_id,
        )

    async def forget(self, *, user_id: UUID, query: str, limit: int = 10) -> list[Memory]:
        return await MemoryRepository(self.session).soft_delete_matching(
            user_id, query, limit=limit
        )

    async def list_active(self, *, user_id: UUID, limit: int = 10) -> list[Memory]:
        return await MemoryRepository(self.session).list_for_user(
            user_id, status=MemoryStatus.ACTIVE, limit=limit
        )

    async def correct(
        self,
        *,
        user_id: UUID,
        old_query: str,
        replacement: str,
        source_message_id: UUID | None,
    ) -> tuple[Memory | None, list[Memory]]:
        repository = MemoryRepository(self.session)
        matches = await repository.search_hybrid(user_id, old_query, limit=10)
        new_memory = await self.add_explicit(
            user_id=user_id,
            content=replacement,
            source_message_id=source_message_id,
        )
        superseded: list[Memory] = []
        if new_memory:
            if new_memory.status is MemoryStatus.ACTIVE:
                for item in matches:
                    item.memory.status = MemoryStatus.SUPERSEDED
                    item.memory.superseded_by_id = new_memory.id
                    superseded.append(item.memory)
            else:
                new_memory.metadata_["conflicts_with"] = [
                    str(item.memory.id) for item in matches
                ]
            await self.session.flush()
        return new_memory, superseded

    async def _supersede_previous(
        self, user_id: UUID, canonical_key: str, *, superseded_by_id: UUID
    ) -> None:
        previous = await self.session.scalars(
            select(Memory).where(
                Memory.user_id == user_id,
                Memory.canonical_key == canonical_key,
                Memory.status == MemoryStatus.ACTIVE,
                Memory.id != superseded_by_id,
            )
        )
        for memory in previous:
            memory.status = MemoryStatus.SUPERSEDED
            memory.superseded_by_id = superseded_by_id
