"""On-demand period review built from governed memory records."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.business import BusinessConfig, get_business_config
from app.models.qwen import create_chat_model
from app.persistence.models import MemoryKind, MemoryStatus
from app.persistence.repositories import MemoryRepository

REVIEW_PROMPT = """你是个人成长与学习伙伴。根据给出的、已经过治理的长期记忆生成中文周期回顾。
内容依次包括：讨论与经历、目标变化、记忆变化、下一阶段建议。只使用输入事实，不虚构进展。
语气简洁自然，不输出系统说明。"""


class ReviewService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        model=None,
        business: BusinessConfig | None = None,
    ):
        self.session = session
        self.business = business or get_business_config()
        self.model = model

    async def generate(self, *, user_id: UUID, period_days: int) -> str:
        memories = await MemoryRepository(self.session).list_for_user(
            user_id, limit=200
        )
        cutoff = datetime.now(timezone.utc) - timedelta(days=period_days)
        relevant = [
            memory
            for memory in memories
            if memory.status is MemoryStatus.ACTIVE
            and (
                memory.kind is MemoryKind.SEMANTIC
                or (memory.valid_from or memory.created_at) >= cutoff
            )
        ]
        changes = [
            memory
            for memory in memories
            if memory.updated_at >= cutoff
            and memory.status
            in {MemoryStatus.SUPERSEDED, MemoryStatus.DELETED, MemoryStatus.REJECTED}
        ]
        if not relevant and not changes:
            return "这段时间还没有足够的长期记忆可用于生成回顾。"

        facts = "\n".join(
            f"- [{memory.kind.value}/{memory.status.value}] {memory.content}"
            for memory in [*relevant, *changes]
        )
        model = self.model or create_chat_model(config=self.business)
        response = await model.ainvoke(
            [
                SystemMessage(content=REVIEW_PROMPT),
                HumanMessage(content=f"回顾周期：最近 {period_days} 天\n\n记忆：\n{facts}"),
            ]
        )
        return str(response.content)
