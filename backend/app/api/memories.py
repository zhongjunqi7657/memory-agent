"""Memory inspection and explicit user controls."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.db import get_session
from app.persistence.models import MemoryKind, MemorySensitivity, MemoryStatus
from app.persistence.repositories import ConversationRepository, MemoryRepository
from app.security.auth import require_demo_auth

router = APIRouter(
    prefix="/v1", tags=["memory"], dependencies=[Depends(require_demo_auth)]
)


class MemoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: MemoryKind
    status: MemoryStatus
    sensitivity: MemorySensitivity
    content: str
    confidence: float
    canonical_key: str | None
    metadata: dict[str, object] = Field(validation_alias="metadata_")
    source_message_id: UUID | None


class MemoryStatusRequest(BaseModel):
    status: MemoryStatus = Field(description="active、pending、rejected 或 deleted")


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
    request: MemoryStatusRequest,
    user_key: Annotated[str, Query(min_length=1, max_length=128)] = "demo-user",
    session: Annotated[AsyncSession, Depends(get_session)] = None,
) -> MemoryResponse:
    if request.status not in {
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
    await MemoryRepository(session).set_status(memory, request.status)
    await session.commit()
    return MemoryResponse.model_validate(memory)
