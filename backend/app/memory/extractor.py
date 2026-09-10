"""Structured Qwen extraction and deterministic memory persistence."""

from __future__ import annotations

from uuid import UUID

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.business import BusinessConfig, get_business_config
from app.config.settings import get_settings
from app.memory.conflicts import ConflictCandidateSelector
from app.memory.policy import MemoryCandidate
from app.memory.service import MemoryService
from app.models.qwen import create_chat_model, create_embedding_model
from app.persistence.models import (
    Memory,
    MemoryKind,
    MemorySensitivity,
    MemoryStatus,
    Message,
)


class ExtractedMemory(BaseModel):
    content: str = Field(min_length=1, max_length=500)
    kind: MemoryKind
    confidence: float = Field(ge=0, le=1)
    sensitivity: MemorySensitivity = MemorySensitivity.NORMAL
    explicit: bool = True
    canonical_key: str | None = Field(default=None, max_length=200)
    importance: float = Field(default=0.5, ge=0, le=1)
    contradicts_memory_ids: list[UUID] = Field(default_factory=list, max_length=20)


class ExtractionResult(BaseModel):
    memories: list[ExtractedMemory] = Field(default_factory=list, max_length=10)


EXTRACTION_PROMPT = """你负责从用户消息中提取可长期复用的事实。
只提取用户明确表达或有清晰证据的内容，不提取助手内容、一次性闲聊和任何密码、API key、token。
content 用简洁中文陈述；kind 只能是 semantic（稳定偏好、目标、自我描述）或 episodic（带时间的经历）。
不确定或敏感内容保留候选并降低 confidence，由代码决定是否需要用户确认。
importance 表示未来复用价值，范围 0 到 1。若新事实与给出的已有记忆冲突，将其 ID 放入 contradicts_memory_ids；
同一类稳定事实使用相同 canonical_key。“优先冲突候选”只表示语义相关，不等于事实冲突；
只有新旧稳定事实不能同时成立或同一属性已经改变时才标记旧 ID，补充事实和无关事实不得标记。没有候选时返回空 memories。"""
REPAIR_PROMPT = """上一次结构化结果未通过 Schema 校验。请重新从原始用户消息提取，
只返回符合指定 Schema 的结果；不要补充用户没有表达的事实。"""
_AUTO_EMBEDDING = object()


class MemoryExtractor:
    def __init__(
        self,
        session: AsyncSession,
        *,
        model=None,
        embedding_model=_AUTO_EMBEDDING,
        business: BusinessConfig | None = None,
    ):
        self.session = session
        self.business = business or get_business_config()
        base_model = model or create_chat_model(config=self.business)
        self.model = base_model.with_structured_output(ExtractionResult)
        self.repair_model = base_model.with_structured_output(ExtractionResult)
        self.embedding_model = None
        if embedding_model is _AUTO_EMBEDDING and get_settings().dashscope_api_key:
            self.embedding_model = create_embedding_model(config=self.business)
        elif embedding_model is not _AUTO_EMBEDDING:
            self.embedding_model = embedding_model

    async def extract_and_store(
        self, *, user_id: UUID, message: Message
    ) -> list[Memory]:
        active_result = await self.session.scalars(
            select(Memory)
            .where(Memory.user_id == user_id, Memory.status == MemoryStatus.ACTIVE)
            .order_by(Memory.updated_at.desc())
            .limit(50)
        )
        active_memories = list(active_result)
        conflict_candidates = []
        if active_memories:
            conflict_candidates = await ConflictCandidateSelector(
                self.session,
                embedding_model=self.embedding_model,
                business=self.business,
            ).select(user_id=user_id, query=message.content)
        priority_memories = [item.memory for item in conflict_candidates]
        priority_ids = {memory.id for memory in priority_memories}
        other_memories = [
            memory for memory in active_memories if memory.id not in priority_ids
        ]
        prompt = EXTRACTION_PROMPT
        if priority_memories:
            prompt = (
                f"{prompt}\n\n优先冲突候选：\n"
                f"{_format_inventory(priority_memories)}"
            )
        if other_memories:
            prompt = (
                f"{prompt}\n\n其他已有 active 记忆：\n"
                f"{_format_inventory(other_memories)}"
            )
        messages = [
            SystemMessage(content=prompt),
            HumanMessage(content=message.content),
        ]
        try:
            raw_result = await self.model.ainvoke(messages)
            result = ExtractionResult.model_validate(raw_result)
        except (OutputParserException, ValidationError, ValueError, TypeError):
            repaired = await self.repair_model.ainvoke(
                [SystemMessage(content=REPAIR_PROMPT), *messages]
            )
            result = ExtractionResult.model_validate(repaired)
        stored = []
        memory_service = MemoryService(
            self.session,
            business=self.business,
            embedding_model=self.embedding_model,
        )
        for item in result.memories:
            memory = await memory_service.add_candidate(
                user_id=user_id,
                candidate=MemoryCandidate(
                    content=item.content,
                    kind=item.kind,
                    confidence=item.confidence,
                    sensitivity=item.sensitivity,
                    explicit=item.explicit,
                    canonical_key=item.canonical_key,
                    importance=item.importance,
                    contradicts_memory_ids=tuple(item.contradicts_memory_ids),
                ),
                source_message_id=message.id,
                conversation_id=getattr(message, "conversation_id", None),
            )
            if memory:
                stored.append(memory)
        return stored


def _format_inventory(memories: list[Memory]) -> str:
    return "\n".join(
        f"- id={memory.id}; canonical_key={memory.canonical_key or '-'}; "
        f"content={memory.content}"
        for memory in memories
    )
