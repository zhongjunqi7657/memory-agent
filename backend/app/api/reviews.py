"""Timeline and user-triggered period review endpoints."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.memory.review import ReviewService
from app.persistence.db import get_session
from app.persistence.models import MemoryKind, MemoryStatus
from app.persistence.repositories import ConversationRepository, MemoryRepository
from app.security.auth import require_demo_auth

router = APIRouter(
    prefix="/v1", tags=["reviews"], dependencies=[Depends(require_demo_auth)]
)


class TimelineItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    content: str
    kind: MemoryKind
    status: MemoryStatus
    importance: float
    conversation_id: UUID | None
    valid_from: datetime | None
    created_at: datetime


class ReviewRequest(BaseModel):
    user_key: str = Field(default="demo-user", min_length=1, max_length=128)
    period_days: int = Field(default=7, ge=1, le=365)


class ReviewResponse(BaseModel):
    period_days: int
    content: str


@router.get("/timeline", response_model=list[TimelineItem])
async def get_timeline(
    user_key: Annotated[str, Query(min_length=1, max_length=128)] = "demo-user",
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    session: Annotated[AsyncSession, Depends(get_session)] = None,
) -> list[TimelineItem]:
    user = await ConversationRepository(session).get_user(user_key)
    if user is None:
        return []
    memories = await MemoryRepository(session).list_timeline(user.id, limit=limit)
    return [TimelineItem.model_validate(memory) for memory in memories]


@router.post("/reviews", response_model=ReviewResponse)
async def generate_review(
    request: ReviewRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ReviewResponse:
    user = await ConversationRepository(session).get_user(request.user_key)
    if user is None:
        return ReviewResponse(
            period_days=request.period_days,
            content="这段时间还没有足够的长期记忆可用于生成回顾。",
        )
    try:
        content = await ReviewService(session).generate(
            user_id=user.id, period_days=request.period_days
        )
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail="模型服务尚未配置") from error
    return ReviewResponse(period_days=request.period_days, content=content)
