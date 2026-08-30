"""Structured Qwen extraction and deterministic memory persistence."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.business import BusinessConfig, get_business_config
from app.config.settings import get_settings
from app.memory.policy import MemoryCandidate, MemoryDecision, assess_candidate
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


class ExtractionResult(BaseModel):
    memories: list[ExtractedMemory] = Field(default_factory=list, max_length=10)


EXTRACTION_PROMPT = """你负责从用户消息中提取可长期复用的事实。
只提取用户明确表达或有清晰证据的内容，不提取助手内容、一次性闲聊和任何密码、API key、token。
content 用简洁中文陈述；kind 只能是 semantic（稳定偏好、目标、自我描述）或 episodic（带时间的经历）。
不确定或敏感内容保留候选并降低 confidence，由代码决定是否需要用户确认；没有候选时返回空 memories。"""


class MemoryExtractor:
    def __init__(
        self,
        session: AsyncSession,
        *,
        model=None,
        embedding_model=None,
        business: BusinessConfig | None = None,
    ):
        self.session = session
        self.business = business or get_business_config()
        base_model = model or create_chat_model(config=self.business)
        self.model = base_model.with_structured_output(ExtractionResult)
        self.embedding_model = embedding_model
        if self.embedding_model is None and get_settings().dashscope_api_key:
            self.embedding_model = create_embedding_model(config=self.business)

    async def extract_and_store(
        self, *, user_id: UUID, message: Message
    ) -> list[Memory]:
        result = await self.model.ainvoke(
            [
                SystemMessage(content=EXTRACTION_PROMPT),
                HumanMessage(content=message.content),
            ]
        )
        stored: list[Memory] = []
        for item in result.memories:
            assessment = assess_candidate(
                MemoryCandidate(
                    content=item.content,
                    kind=item.kind,
                    confidence=item.confidence,
                    sensitivity=item.sensitivity,
                    explicit=item.explicit,
                    canonical_key=item.canonical_key,
                ),
                config=self.business.memory,
            )
            if assessment.decision is MemoryDecision.REJECT:
                continue
            if assessment.decision is MemoryDecision.ACTIVE and item.canonical_key:
                await self._supersede_previous(user_id, item.canonical_key)
            memory = Memory(
                user_id=user_id,
                kind=item.kind,
                status=(
                    MemoryStatus.ACTIVE
                    if assessment.decision is MemoryDecision.ACTIVE
                    else MemoryStatus.PENDING
                ),
                sensitivity=assessment.sensitivity,
                content=assessment.content,
                canonical_key=item.canonical_key,
                confidence=Decimal(str(assessment.confidence)),
                source_message_id=message.id,
                valid_from=datetime.now(timezone.utc),
                metadata_={"reason": assessment.reason},
            )
            if self.embedding_model:
                try:
                    memory.embedding = await self.embedding_model.aembed_query(
                        assessment.content
                    )
                except Exception:  # noqa: BLE001 - keep extraction usable without vectors
                    memory.embedding = None
            self.session.add(memory)
            stored.append(memory)
        await self.session.flush()
        return stored

    async def _supersede_previous(self, user_id: UUID, canonical_key: str) -> None:
        previous = await self.session.scalars(
            select(Memory).where(
                Memory.user_id == user_id,
                Memory.canonical_key == canonical_key,
                Memory.status == MemoryStatus.ACTIVE,
            )
        )
        for memory in previous:
            memory.status = MemoryStatus.SUPERSEDED
