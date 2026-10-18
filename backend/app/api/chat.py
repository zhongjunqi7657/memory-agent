"""Synchronous and SSE chat endpoints for the first vertical slice."""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.responses import StreamingResponse

from app.agent.service import AgentInputError, AgentService
from app.api.approvals import approval_broker
from app.api.execution import run_controller
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
    project_id: UUID | None = None
    execution_mode: Literal["original", "worktree"] = "original"


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
    http_request: Request = None,
) -> ChatResponse:
    enforce_chat_limits(
        request.user_key, request.content, get_business_config().security
    )
    try:
        result = await AgentService(session, controller=run_controller(http_request), broker=approval_broker(http_request), mcp_manager=getattr(http_request.app.state, "mcp_manager", None) if http_request else None).chat(
            user_key=request.user_key,
            content=request.content,
            conversation_id=request.conversation_id,
            project_id=request.project_id, execution_mode=request.execution_mode,
        )
    except asyncio.CancelledError as error:
        raise HTTPException(status_code=409, detail="任务已取消") from error
    except AgentInputError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return ChatResponse.model_validate(result)


@router.post("/chat/stream")
async def chat_stream(
    request: ChatRequest,
    session_factory: Annotated[
        async_sessionmaker[AsyncSession], Depends(get_session_factory)
    ],
    http_request: Request = None,
) -> StreamingResponse:
    enforce_chat_limits(
        request.user_key, request.content, get_business_config().security
    )
    async def event_generator() -> AsyncIterator[str]:
        queue: asyncio.Queue[dict[str, object] | None] = asyncio.Queue()
        connected = True

        async def publish(event: dict[str, object]) -> None:
            if connected:
                queue.put_nowait(event)

        async def run_agent() -> None:
            try:
                async with session_factory() as session:
                    await AgentService(session, controller=run_controller(http_request), broker=approval_broker(http_request), mcp_manager=getattr(http_request.app.state, "mcp_manager", None) if http_request else None).chat(
                        user_key=request.user_key,
                        content=request.content,
                        conversation_id=request.conversation_id,
                        project_id=request.project_id, execution_mode=request.execution_mode,
                        event_sink=publish,
                    )
            except asyncio.CancelledError:
                pass  # The lifecycle has persisted and published run.cancelled.
            except AgentInputError as error:
                await publish({"event_type": "error", "payload": {"message": str(error)}})
            except Exception:  # noqa: BLE001 - never expose provider details
                await publish(
                    {"event_type": "error", "payload": {"message": "请求处理失败"}}
                )
            finally:
                if connected:
                    queue.put_nowait(None)

        task = asyncio.create_task(run_agent())
        _running_stream_tasks.add(task)
        task.add_done_callback(_running_stream_tasks.discard)
        # The task is intentionally independent from this response iterator. A
        # disconnected client can replay its persisted events without a second run.
        try:
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
        finally:
            connected = False
            # Disconnection stops buffering, not the independent task.
            while not queue.empty():
                queue.get_nowait()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
