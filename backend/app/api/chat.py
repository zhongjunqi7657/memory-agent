"""Synchronous chat endpoint for the first vertical slice."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.service import AgentInputError, AgentService
from app.persistence.db import get_session

router = APIRouter(prefix="/v1", tags=["chat"])


class ChatRequest(BaseModel):
    user_key: str = Field(default="demo-user", min_length=1, max_length=128)
    content: str = Field(min_length=1)
    conversation_id: UUID | None = None


class ChatResponse(BaseModel):
    conversation_id: UUID
    run_id: UUID
    message: str
    redacted: bool
    redaction_categories: tuple[str, ...]
    memory_count: int


@router.post("/chat", response_model=ChatResponse, status_code=status.HTTP_200_OK)
async def chat(
    request: ChatRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ChatResponse:
    try:
        result = await AgentService(session).chat(
            user_key=request.user_key,
            content=request.content,
            conversation_id=request.conversation_id,
        )
    except AgentInputError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return ChatResponse.model_validate(result)
