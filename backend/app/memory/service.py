"""Single entry point for governed memory writes and lifecycle changes."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

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
        conversation_id: UUID | None = None,
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
        matching_query = select(Memory).where(
            Memory.user_id == user_id,
            Memory.status == MemoryStatus.ACTIVE,
            Memory.content == assessment.content,
        )
        if candidate.canonical_key:
            matching_query = matching_query.where(
                Memory.canonical_key == candidate.canonical_key
            )
        matching = await self.session.scalar(matching_query)
        if matching:
            sources = list(matching.source_message_ids or [])
            if source_message_id and str(source_message_id) not in sources:
                sources.append(str(source_message_id))
            matching.source_message_ids = sources
            matching.confidence = max(
                Decimal(str(matching.confidence)),
                Decimal(str(assessment.confidence)),
            )
            matching.importance = max(
                Decimal(str(matching.importance)),
                Decimal(str(max(0.0, min(1.0, candidate.importance)))),
            )
            metadata = dict(matching.metadata_ or {})
            metadata["evidence_count"] = len(sources)
            matching.metadata_ = metadata
            await self.session.flush()
            return matching
        conflicts = await self._find_conflicts(user_id, candidate)
        status = (
            MemoryStatus.ACTIVE
            if assessment.decision is MemoryDecision.ACTIVE and not conflicts
            else MemoryStatus.PENDING
        )
        reason = assessment.reason
        if conflicts:
            reason = "与已有有效记忆冲突，等待用户确认后再替换旧版本"
        source_message_ids = [str(source_message_id)] if source_message_id else []
        memory = Memory(
            id=uuid4(),
            user_id=user_id,
            kind=candidate.kind,
            status=status,
            sensitivity=assessment.sensitivity,
            content=assessment.content,
            canonical_key=candidate.canonical_key,
            confidence=Decimal(str(assessment.confidence)),
            importance=Decimal(str(max(0.0, min(1.0, candidate.importance)))),
            source_message_id=source_message_id,
            source_message_ids=source_message_ids,
            conversation_id=conversation_id,
            valid_from=datetime.now(timezone.utc),
            metadata_={
                "reason": reason,
                "explicit": candidate.explicit,
                "conflicts_with": [str(item.id) for item in conflicts],
            },
        )
        if self.embedding_model:
            try:
                embedding = await self.embedding_model.aembed_query(assessment.content)
                if len(embedding) == self.business.models.embedding_dimensions:
                    memory.embedding = embedding
                    memory.metadata_["embedding_status"] = "ready"
                else:
                    memory.metadata_["embedding_status"] = "pending"
            except Exception:  # noqa: BLE001 - text memory remains usable without vectors
                memory.embedding = None
                memory.metadata_["embedding_status"] = "pending"
        self.session.add(memory)
        await self.session.flush()
        if status is MemoryStatus.ACTIVE and candidate.canonical_key:
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
        conversation_id: UUID | None = None,
        importance: float = 0.7,
        contradicts_memory_ids: tuple[UUID, ...] = (),
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
                importance=importance,
                contradicts_memory_ids=contradicts_memory_ids,
            ),
            source_message_id=source_message_id,
            conversation_id=conversation_id,
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
        conversation_id: UUID | None = None,
    ) -> tuple[Memory | None, list[Memory]]:
        repository = MemoryRepository(self.session)
        matches = await repository.search_hybrid(user_id, old_query, limit=10)
        new_memory = await self.add_explicit(
            user_id=user_id,
            content=replacement,
            source_message_id=source_message_id,
            conversation_id=conversation_id,
            contradicts_memory_ids=tuple(item.memory.id for item in matches),
        )
        superseded: list[Memory] = []
        if new_memory:
            if not matches and new_memory.status is MemoryStatus.ACTIVE:
                superseded = []
            await self.session.flush()
        return new_memory, superseded

    async def propose_update(
        self,
        *,
        user_id: UUID,
        content: str,
        source_message_id: UUID | None = None,
        conversation_id: UUID | None = None,
        kind: MemoryKind = MemoryKind.SEMANTIC,
    ) -> Memory | None:
        memory = await self.add_candidate(
            user_id=user_id,
            candidate=MemoryCandidate(
                content=content,
                kind=kind,
                confidence=1.0,
                explicit=False,
                importance=0.5,
            ),
            source_message_id=source_message_id,
            conversation_id=conversation_id,
        )
        if memory and memory.status is MemoryStatus.PENDING:
            metadata = dict(memory.metadata_)
            metadata["proposal"] = True
            metadata["reason"] = "Agent 工具提出的变更，等待用户确认"
            memory.metadata_ = metadata
            await self.session.flush()
        return memory

    async def update_memory(
        self,
        memory: Memory,
        *,
        status: MemoryStatus | None = None,
        content: str | None = None,
        importance: float | None = None,
    ) -> Memory:
        metadata = dict(memory.metadata_)
        metadata["undo"] = {
            "status": memory.status.value,
            "content": memory.content,
            "importance": float(memory.importance),
        }
        if content is not None and content.strip() != memory.content:
            assessment = assess_candidate(
                MemoryCandidate(
                    content=content.strip(),
                    kind=memory.kind,
                    confidence=float(memory.confidence),
                    sensitivity=memory.sensitivity,
                    explicit=True,
                    importance=float(memory.importance),
                ),
                config=self.business.memory,
            )
            if assessment.decision is MemoryDecision.REJECT:
                raise ValueError("包含私密凭据的内容不能保存为长期记忆")
            memory.content = assessment.content
            memory.embedding = None
            metadata["embedding_status"] = "pending"
        if importance is not None:
            memory.importance = Decimal(str(max(0.0, min(1.0, importance))))
        memory.metadata_ = metadata
        if status is MemoryStatus.ACTIVE and memory.status is MemoryStatus.PENDING:
            await self._activate_pending(memory)
        elif status is not None:
            memory.status = status
        await self.session.flush()
        return memory

    async def undo(self, memory: Memory) -> Memory:
        metadata = dict(memory.metadata_)
        snapshot = metadata.pop("undo", None)
        if not isinstance(snapshot, dict):
            raise TypeError("这条记忆没有可撤销的最近修改")
        memory.status = MemoryStatus(str(snapshot["status"]))
        restored_content = str(snapshot["content"])
        if restored_content != memory.content:
            memory.embedding = None
            metadata["embedding_status"] = "pending"
        memory.content = restored_content
        memory.importance = Decimal(str(snapshot["importance"]))
        memory.metadata_ = metadata
        if memory.status is MemoryStatus.PENDING:
            superseded = await self.session.scalars(
                select(Memory).where(
                    Memory.user_id == memory.user_id,
                    Memory.superseded_by_id == memory.id,
                    Memory.status == MemoryStatus.SUPERSEDED,
                )
            )
            for previous in superseded:
                previous.status = MemoryStatus.ACTIVE
                previous.superseded_by_id = None
        await self.session.flush()
        return memory

    async def _activate_pending(self, memory: Memory) -> None:
        raw_ids = memory.metadata_.get("conflicts_with", [])
        conflict_ids: list[UUID] = []
        for raw_id in raw_ids if isinstance(raw_ids, list) else []:
            try:
                conflict_ids.append(UUID(str(raw_id)))
            except ValueError:
                continue
        if conflict_ids:
            conflicts = await self.session.scalars(
                select(Memory).where(
                    Memory.user_id == memory.user_id,
                    Memory.id.in_(conflict_ids),
                    Memory.status == MemoryStatus.ACTIVE,
                )
            )
            for previous in conflicts:
                previous.status = MemoryStatus.SUPERSEDED
                previous.superseded_by_id = memory.id
        if memory.canonical_key:
            await self._supersede_previous(
                memory.user_id,
                memory.canonical_key,
                superseded_by_id=memory.id,
            )
        memory.status = MemoryStatus.ACTIVE

    async def _find_conflicts(
        self, user_id: UUID, candidate: MemoryCandidate
    ) -> list[Memory]:
        conditions = []
        if candidate.contradicts_memory_ids:
            conditions.append(Memory.id.in_(candidate.contradicts_memory_ids))
        if candidate.canonical_key:
            conditions.append(Memory.canonical_key == candidate.canonical_key)
        if not conditions:
            return []
        from sqlalchemy import or_

        matches = await self.session.scalars(
            select(Memory).where(
                Memory.user_id == user_id,
                Memory.status == MemoryStatus.ACTIVE,
                or_(*conditions),
            )
        )
        return [memory for memory in matches if memory.content != candidate.content]

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
