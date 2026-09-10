"""Semantic candidate selection for governed memory conflict checks."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from app.config.business import BusinessConfig, get_business_config
from app.memory.retrieval import ScoredMemory, rank_memories
from app.persistence.models import MemoryStatus
from app.persistence.repositories import MemoryRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.persistence.models import Memory


def rank_conflict_candidates(
    memories: list[Memory],
    query: str,
    *,
    query_embedding: list[float] | None = None,
    limit: int | None = None,
    business: BusinessConfig | None = None,
) -> list[ScoredMemory]:
    """Rank possible old versions without deciding whether they conflict."""

    config = (business or get_business_config()).memory
    return rank_memories(
        memories,
        query,
        query_embedding=query_embedding,
        vector_weight=config.vector_weight,
        keyword_weight=config.keyword_weight,
        recency_weight=config.recency_weight,
        importance_weight=config.importance_weight,
        type_weight=config.type_weight,
        recency_half_life_days=config.recency_half_life_days,
        min_relevance_score=config.min_relevance_score,
        limit=limit or config.auto_retrieve_limit,
    )


class ConflictCandidateSelector:
    """Select active memories that the extraction model should compare."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        embedding_model=None,
        business: BusinessConfig | None = None,
    ) -> None:
        self.repository = MemoryRepository(session)
        self.embedding_model = embedding_model
        self.business = business or get_business_config()

    async def select(
        self,
        *,
        user_id: UUID,
        query: str,
        limit: int | None = None,
    ) -> list[ScoredMemory]:
        normalized = query.strip()
        if not normalized:
            return []
        result_limit = limit or self.business.memory.auto_retrieve_limit
        query_embedding = await self._embed_query(normalized)
        memories = await self.repository.list_active_search_candidates(
            user_id,
            query_embedding=query_embedding,
            limit=result_limit,
        )
        ranked = rank_conflict_candidates(
            memories,
            normalized,
            query_embedding=query_embedding,
            limit=result_limit,
            business=self.business,
        )
        return [
            item
            for item in ranked
            if item.memory.user_id == user_id
            and item.memory.status == MemoryStatus.ACTIVE
        ]

    async def _embed_query(self, query: str) -> list[float] | None:
        if self.embedding_model is None:
            return None
        try:
            embedding = await self.embedding_model.aembed_query(query)
        except Exception:  # noqa: BLE001 - keyword ranking remains available
            return None
        if len(embedding) != self.business.models.embedding_dimensions:
            return None
        return embedding
