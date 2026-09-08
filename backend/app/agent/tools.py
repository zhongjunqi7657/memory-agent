"""User-scoped memory tools exposed to the LangGraph model loop."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Literal
from uuid import UUID

from langchain_core.tools import BaseTool, tool
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.business import BusinessConfig
from app.memory.service import MemoryService
from app.persistence.models import MemoryKind
from app.persistence.repositories import MemoryRepository
from app.security.redaction import redact_secrets

ToolEventSink = Callable[[str, dict[str, object]], Awaitable[None]]


def create_memory_tools(
    session: AsyncSession,
    *,
    user_id: UUID,
    conversation_id: UUID,
    source_message_id: UUID,
    business: BusinessConfig,
    embedding_model=None,
    emit: ToolEventSink | None = None,
) -> list[BaseTool]:
    async def publish(event_type: str, payload: dict[str, object]) -> None:
        if emit:
            await emit(event_type, payload)

    @tool("search_memory")
    async def search_memory(query: str, limit: int = 5) -> str:
        """Search deeper user memory when the automatically injected context is insufficient."""

        safe_query = redact_secrets(query).text
        bounded_limit = max(1, min(limit, business.memory.tool_retrieve_limit))
        await publish(
            "tool.started",
            {"tool": "search_memory", "query_length": len(safe_query)},
        )
        query_embedding = None
        if embedding_model:
            try:
                query_embedding = await embedding_model.aembed_query(safe_query)
            except Exception:  # noqa: BLE001 - keyword retrieval remains available
                await publish(
                    "memory.embedding_fallback", {"source": "search_memory"}
                )
        matches = await MemoryRepository(session).search_hybrid(
            user_id,
            safe_query,
            query_embedding=query_embedding,
            limit=bounded_limit,
            vector_weight=business.memory.vector_weight,
            keyword_weight=business.memory.keyword_weight,
            recency_weight=business.memory.recency_weight,
            importance_weight=business.memory.importance_weight,
            type_weight=business.memory.type_weight,
            recency_half_life_days=business.memory.recency_half_life_days,
            min_relevance_score=business.memory.min_relevance_score,
        )
        payload = [
            {
                "id": str(item.memory.id),
                "content": item.memory.content,
                "kind": item.memory.kind.value,
                "score": round(item.score, 4),
            }
            for item in matches
        ]
        await publish(
            "tool.completed",
            {"tool": "search_memory", "result_count": len(payload)},
        )
        return json.dumps(payload, ensure_ascii=False)

    @tool("get_user_timeline")
    async def get_user_timeline(limit: int = 20) -> str:
        """Return active goals and experiences in reverse chronological order."""

        bounded_limit = max(1, min(limit, business.memory.tool_retrieve_limit * 2))
        await publish("tool.started", {"tool": "get_user_timeline"})
        memories = await MemoryRepository(session).list_timeline(
            user_id, limit=bounded_limit
        )
        payload = [
            {
                "id": str(memory.id),
                "content": memory.content,
                "kind": memory.kind.value,
                "valid_from": memory.valid_from.isoformat()
                if memory.valid_from
                else None,
            }
            for memory in memories
        ]
        await publish(
            "tool.completed",
            {"tool": "get_user_timeline", "result_count": len(payload)},
        )
        return json.dumps(payload, ensure_ascii=False)

    @tool("propose_memory_update")
    async def propose_memory_update(
        content: str,
        kind: Literal["semantic", "episodic"] = "semantic",
    ) -> str:
        """Propose a memory change for user confirmation; never activate it directly."""

        await publish("tool.started", {"tool": "propose_memory_update"})
        memory = await MemoryService(session, business=business).propose_update(
            user_id=user_id,
            content=content,
            source_message_id=source_message_id,
            conversation_id=conversation_id,
            kind=MemoryKind(kind),
        )
        payload = {
            "created": memory is not None,
            "memory_id": str(memory.id) if memory else None,
            "status": memory.status.value if memory else "rejected",
        }
        await publish("tool.completed", {"tool": "propose_memory_update", **payload})
        return json.dumps(payload, ensure_ascii=False)

    return [search_memory, get_user_timeline, propose_memory_update]
