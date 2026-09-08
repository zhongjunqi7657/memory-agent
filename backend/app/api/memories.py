"""Memory inspection and explicit user controls."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from app.memory.service import MemoryService
from app.persistence.db import get_session
from app.persistence.models import MemoryKind, MemorySensitivity, MemoryStatus
from app.persistence.repositories import ConversationRepository, MemoryRepository
from app.security.auth import require_demo_auth

router = APIRouter(
    prefix="/v1", tags=["memory"], dependencies=[Depends(require_demo_auth)]
)


async def _queue_embedding_if_needed(
    session: AsyncSession, memory
) -> None:
    if (
        memory.metadata_.get("embedding_status") == "pending"
        and memory.source_message_id
        and memory.conversation_id
        and get_settings().dashscope_api_key
    ):
        await ConversationRepository(session).queue_embedding_job(
            memory=memory,
            message_id=memory.source_message_id,
            conversation_id=memory.conversation_id,
        )


class MemoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: MemoryKind
    status: MemoryStatus
    sensitivity: MemorySensitivity
    content: str
    confidence: float
    importance: float = 0.5
    canonical_key: str | None
    metadata: dict[str, object] = Field(validation_alias="metadata_")
    source_message_id: UUID | None
    source_message_ids: list[str] = Field(default_factory=list)
    conversation_id: UUID | None = None


class MemoryUpdateRequest(BaseModel):
    status: MemoryStatus | None = Field(
        default=None, description="active、pending、rejected 或 deleted"
    )
    content: str | None = Field(default=None, min_length=1, max_length=500)
    importance: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def require_change(self) -> "MemoryUpdateRequest":
        if self.status is None and self.content is None and self.importance is None:
            raise ValueError("至少提供一项修改")
        return self


@router.get("/memories", response_model=list[MemoryResponse])
async def list_memories(
    user_key: Annotated[str, Query(min_length=1, max_length=128)] = "demo-user",
    memory_status: Annotated[MemoryStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    session: Annotated[AsyncSession, Depends(get_session)] = None,
) -> list[MemoryResponse]:
    user = await ConversationRepository(session).get_user(user_key)
    if user is None:
        return []
    memories = await MemoryRepository(session).list_for_user(
        user.id, status=memory_status, limit=limit
    )
    return [MemoryResponse.model_validate(memory) for memory in memories]


@router.patch("/memories/{memory_id}", response_model=MemoryResponse)
async def update_memory(
    memory_id: UUID,
    request: MemoryUpdateRequest,
    user_key: Annotated[str, Query(min_length=1, max_length=128)] = "demo-user",
    session: Annotated[AsyncSession, Depends(get_session)] = None,
) -> MemoryResponse:
    if request.status is not None and request.status not in {
        MemoryStatus.ACTIVE,
        MemoryStatus.PENDING,
        MemoryStatus.DELETED,
        MemoryStatus.REJECTED,
    }:
        raise HTTPException(status_code=400, detail="不支持的记忆状态")
    user = await ConversationRepository(session).get_user(user_key)
    memory = (
        await MemoryRepository(session).get_for_user(memory_id, user.id)
        if user
        else None
    )
    if memory is None:
        raise HTTPException(status_code=404, detail="记忆不存在")
    try:
        await MemoryService(session).update_memory(
            memory,
            status=request.status,
            content=request.content,
            importance=request.importance,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    await _queue_embedding_if_needed(session, memory)
    await session.commit()
    return MemoryResponse.model_validate(memory)


@router.post("/memories/{memory_id}/undo", response_model=MemoryResponse)
async def undo_memory_update(
    memory_id: UUID,
    user_key: Annotated[str, Query(min_length=1, max_length=128)] = "demo-user",
    session: Annotated[AsyncSession, Depends(get_session)] = None,
) -> MemoryResponse:
    user = await ConversationRepository(session).get_user(user_key)
    memory = (
        await MemoryRepository(session).get_for_user(memory_id, user.id)
        if user
        else None
    )
    if memory is None:
        raise HTTPException(status_code=404, detail="记忆不存在")
    try:
        await MemoryService(session).undo(memory)
    except TypeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    await _queue_embedding_if_needed(session, memory)
    await session.commit()
    return MemoryResponse.model_validate(memory)
