"""User-scoped conversation history for the thin client."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import RunEventResponse
from app.persistence.db import get_session
from app.persistence.models import MessageRole
from app.persistence.repositories import ConversationRepository
from app.security.auth import require_demo_auth

router = APIRouter(
    prefix="/v1", tags=["conversations"], dependencies=[Depends(require_demo_auth)]
)


class ConversationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str | None
    created_at: datetime
    updated_at: datetime


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    role: MessageRole
    content: str
    sequence: int
    is_redacted: bool
    created_at: datetime
    run_id: UUID | None = None
    run_events: list[RunEventResponse] = Field(default_factory=list)


@router.get("/conversations", response_model=list[ConversationResponse])
async def list_conversations(
    user_key: Annotated[str, Query(min_length=1, max_length=128)] = "demo-user",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    session: Annotated[AsyncSession, Depends(get_session)] = None,
) -> list[ConversationResponse]:
    repository = ConversationRepository(session)
    user = await repository.get_user(user_key)
    if user is None:
        return []
    conversations = await repository.list_conversations(user.id, limit=limit)
    return [ConversationResponse.model_validate(item) for item in conversations]


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=list[MessageResponse],
)
async def list_conversation_messages(
    conversation_id: UUID,
    user_key: Annotated[str, Query(min_length=1, max_length=128)] = "demo-user",
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    session: Annotated[AsyncSession, Depends(get_session)] = None,
) -> list[MessageResponse]:
    repository = ConversationRepository(session)
    user = await repository.get_user(user_key)
    conversation = (
        await repository.get_conversation(conversation_id, user.id) if user else None
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    messages = await repository.list_messages(conversation.id, limit=limit)
    response = []
    for message in messages:
        run = getattr(message, "run", None)
        item = MessageResponse.model_validate(message)
        response.append(
            item.model_copy(
                update={
                    "run_id": getattr(message, "run_id", None),
                    "run_events": [
                        RunEventResponse.model_validate(event)
                        for event in (run.events if run else [])
                    ],
                }
            )
        )
    return response
