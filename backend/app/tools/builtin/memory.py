"""User-scoped memory tools using the existing memory domain services."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.business import BusinessConfig
from app.memory.commands import MemoryCommandType, parse_memory_command
from app.memory.embeddings import embed_text, embedding_fallback_payload
from app.memory.service import MemoryService
from app.persistence.models import MemoryKind, Message, MessageRole
from app.persistence.repositories import MemoryRepository
from app.protocol.tools import ToolExecutionContext, ToolResult
from app.security.redaction import redact_secrets

LEGACY_TOOL_IDS = {
    "search_memory": "builtin.memory.search",
    "get_user_timeline": "builtin.memory.timeline",
    "propose_memory_update": "builtin.memory.propose_update",
}
MEMORY_PERMISSIONS = frozenset({"memory.read", "memory.write.propose"})
ToolEventSink = Callable[[str, dict[str, object]], Awaitable[None]]


class MemoryArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchArguments(MemoryArguments):
    query: str
    limit: int = 5


class TimelineArguments(MemoryArguments):
    limit: int = 20


class ProposeArguments(MemoryArguments):
    content: str
    kind: Literal["semantic", "episodic"] = "semantic"


@dataclass(frozen=True)
class MemoryTool:
    id: str
    model_name: str
    description: str
    input_schema: Mapping[str, Any]
    required_permissions: frozenset[str]
    timeout_seconds: float
    _execute: Callable[[Mapping[str, Any]], Awaitable[ToolResult]]
    remote_name: str | None = None

    async def execute(
        self, arguments: Mapping[str, Any], context: ToolExecutionContext
    ) -> ToolResult:
        return await self._execute(arguments)


def create_memory_tools(
    session: AsyncSession,
    *,
    user_id: UUID,
    conversation_id: UUID,
    source_message_id: UUID,
    business: BusinessConfig,
    embedding_model=None,
    project_id: UUID | None = None,
    emit: ToolEventSink | None = None,
    timeout_seconds: float = 8.0,
) -> list[MemoryTool]:
    async def search(arguments: Mapping[str, Any]) -> ToolResult:
        args = SearchArguments.model_validate(arguments)
        safe_query = redact_secrets(args.query).text
        attempt = await embed_text(
            embedding_model, safe_query,
            expected_dimensions=business.models.embedding_dimensions,
        )
        if not attempt.ready and emit:
            await emit("memory.embedding_fallback", embedding_fallback_payload(
                attempt, expected_dimensions=business.models.embedding_dimensions,
                source="search_memory",
            ))
        matches = await MemoryRepository(session, project_id=project_id, include_personal=True).search_hybrid(
            user_id, safe_query, query_embedding=attempt.vector,
            limit=max(1, min(args.limit, business.memory.tool_retrieve_limit)),
            vector_weight=business.memory.vector_weight,
            keyword_weight=business.memory.keyword_weight,
            recency_weight=business.memory.recency_weight,
            importance_weight=business.memory.importance_weight,
            type_weight=business.memory.type_weight,
            recency_half_life_days=business.memory.recency_half_life_days,
            min_relevance_score=business.memory.min_relevance_score,
            embedding_model_name=business.models.embedding_model,
            embedding_dimensions=business.models.embedding_dimensions,
        )
        return ToolResult.success([
            {"id": str(item.memory.id), "content": item.memory.content,
             "project_id": str(item.memory.project_id) if item.memory.project_id else None,
             "kind": item.memory.kind.value, "score": round(item.score, 4)}
            for item in matches
        ])

    async def timeline(arguments: Mapping[str, Any]) -> ToolResult:
        args = TimelineArguments.model_validate(arguments)
        memories = await MemoryRepository(session, project_id=project_id, include_personal=True).list_timeline(
            user_id, limit=max(1, min(args.limit, business.memory.tool_retrieve_limit * 2))
        )
        return ToolResult.success([
            {"id": str(memory.id), "content": memory.content,
             "kind": memory.kind.value,
             "project_id": str(memory.project_id) if memory.project_id else None,
             "valid_from": memory.valid_from.isoformat() if memory.valid_from else None}
            for memory in memories
        ])

    async def propose(arguments: Mapping[str, Any]) -> ToolResult:
        args = ProposeArguments.model_validate(arguments)
        if project_id is not None:
            source = await session.scalar(select(Message).where(
                Message.id == source_message_id, Message.conversation_id == conversation_id,
                Message.role == MessageRole.USER,
            ))
            command = parse_memory_command(source.content) if source and not source.is_redacted else None
            if not command or command.type is not MemoryCommandType.SAVE or command.target != args.content.strip():
                return ToolResult.success({"created": False, "status": "rejected", "reason": "项目工具日志和临时尝试不进入长期记忆；请用户明确使用记住指令"})
        memory = await MemoryService(session, business=business, project_id=project_id).propose_update(
            user_id=user_id, content=args.content,
            source_message_id=source_message_id, conversation_id=conversation_id,
            kind=MemoryKind(args.kind),
        )
        return ToolResult.success({
            "created": memory is not None,
            "memory_id": str(memory.id) if memory else None,
            "status": memory.status.value if memory else "rejected",
        })

    return [
        MemoryTool(
            "builtin.memory.search", "builtin__memory__search",
            "Search deeper user memory when the injected context is insufficient.",
            SearchArguments.model_json_schema(), frozenset({"memory.read"}),
            timeout_seconds, search,
        ),
        MemoryTool(
            "builtin.memory.timeline", "builtin__memory__timeline",
            "Return active goals and experiences in reverse chronological order.",
            TimelineArguments.model_json_schema(), frozenset({"memory.read"}),
            timeout_seconds, timeline,
        ),
        MemoryTool(
            "builtin.memory.propose_update", "builtin__memory__propose_update",
            "Propose a memory change for confirmation; never activate it directly.",
            ProposeArguments.model_json_schema(), frozenset({"memory.write.propose"}),
            timeout_seconds, propose,
        ),
    ]
