"""Structured Qwen extraction and deterministic memory persistence."""

from __future__ import annotations

from uuid import UUID

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.business import BusinessConfig, get_business_config
from app.config.settings import get_settings
from app.memory.policy import MemoryCandidate
from app.memory.service import MemoryService
from app.models.qwen import create_chat_model, create_embedding_model
from app.persistence.models import (
    Memory,
    MemoryKind,
    MemorySensitivity,
    Message,
)


class ExtractedMemory(BaseModel):
    content: str = Field(min_length=1, max_length=500)
    kind: MemoryKind
    confidence: float = Field(ge=0, le=1)
    sensitivity: MemorySensitivity = MemorySensitivity.NORMAL
    explicit: bool = True
    canonical_key: str | None = Field(default=None, max_length=200)


class ExtractionResult(BaseModel):
    memories: list[ExtractedMemory] = Field(default_factory=list, max_length=10)


EXTRACTION_PROMPT = """你负责从用户消息中提取可长期复用的事实。
只提取用户明确表达或有清晰证据的内容，不提取助手内容、一次性闲聊和任何密码、API key、token。
content 用简洁中文陈述；kind 只能是 semantic（稳定偏好、目标、自我描述）或 episodic（带时间的经历）。
不确定或敏感内容保留候选并降低 confidence，由代码决定是否需要用户确认；没有候选时返回空 memories。"""
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
        self.embedding_model = None
        if embedding_model is _AUTO_EMBEDDING and get_settings().dashscope_api_key:
            self.embedding_model = create_embedding_model(config=self.business)
        elif embedding_model is not _AUTO_EMBEDDING:
            self.embedding_model = embedding_model

    async def extract_and_store(
        self, *, user_id: UUID, message: Message
    ) -> list[Memory]:
        result = await self.model.ainvoke(
            [
                SystemMessage(content=EXTRACTION_PROMPT),
                HumanMessage(content=message.content),
            ]
        )
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
                ),
                source_message_id=message.id,
            )
            if memory:
                stored.append(memory)
        return stored
