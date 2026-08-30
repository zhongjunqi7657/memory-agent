"""Synchronous and SSE chat endpoints for the first vertical slice."""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

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
    memory_command: str | None = None


def format_sse(event: dict[str, object]) -> str:
    """Encode one structured run event without exposing model reasoning traces."""

    event_type = str(event.get("event_type", "message"))
    return f"event: {event_type}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


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


@router.post("/chat/stream")
async def chat_stream(
    request: ChatRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StreamingResponse:
    async def event_generator() -> AsyncIterator[str]:
        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()

        async def publish(event: dict[str, object]) -> None:
            await queue.put(event)

        task = asyncio.create_task(
            AgentService(session).chat(
                user_key=request.user_key,
                content=request.content,
                conversation_id=request.conversation_id,
                event_sink=publish,
            )
        )
        try:
            while True:
                if task.done() and queue.empty():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield format_sse(event)
            if task.exception():
                yield format_sse(
                    {"event_type": "error", "payload": {"message": "请求处理失败"}}
                )
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
