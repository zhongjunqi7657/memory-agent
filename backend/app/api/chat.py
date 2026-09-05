"""Synchronous and SSE chat endpoints for the first vertical slice."""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.responses import StreamingResponse

from app.agent.service import AgentInputError, AgentService
from app.config.business import get_business_config
from app.persistence.db import get_session, get_session_factory
from app.security.auth import require_demo_auth
from app.security.limits import enforce_chat_limits

router = APIRouter(
    prefix="/v1", tags=["chat"], dependencies=[Depends(require_demo_auth)]
)
_running_stream_tasks: set[asyncio.Task[None]] = set()


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
    enforce_chat_limits(
        request.user_key, request.content, get_business_config().security
    )
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
    session_factory: Annotated[
        async_sessionmaker[AsyncSession], Depends(get_session_factory)
    ],
) -> StreamingResponse:
    enforce_chat_limits(
        request.user_key, request.content, get_business_config().security
    )
    async def event_generator() -> AsyncIterator[str]:
        queue: asyncio.Queue[dict[str, object] | None] = asyncio.Queue()

        async def publish(event: dict[str, object]) -> None:
            await queue.put(event)

        async def run_agent() -> None:
            try:
                async with session_factory() as session:
                    await AgentService(session).chat(
                        user_key=request.user_key,
                        content=request.content,
                        conversation_id=request.conversation_id,
                        event_sink=publish,
                    )
            except Exception:  # noqa: BLE001 - never expose provider details
                await queue.put(
                    {"event_type": "error", "payload": {"message": "请求处理失败"}}
                )
            finally:
                await queue.put(None)

        task = asyncio.create_task(run_agent())
        _running_stream_tasks.add(task)
        task.add_done_callback(_running_stream_tasks.discard)
        # The task is intentionally independent from this response iterator. A
        # disconnected client can replay its persisted events without a second run.
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15)
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"
                continue
            if event is None:
                break
            yield format_sse(event)
        await task

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
