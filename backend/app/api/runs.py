"""Replayable run event endpoint used after an SSE reconnect."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.execution import run_controller
from app.api.schemas import RunEventResponse
from app.execution.lifecycle import ACTIVE_STATUSES
from app.persistence.db import get_session
from app.persistence.models import Conversation, Run, RunStatus, User
from app.persistence.repositories import ConversationRepository
from app.security.auth import require_demo_auth

router = APIRouter(
    prefix="/v1", tags=["runs"], dependencies=[Depends(require_demo_auth)]
)


class RunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    run_id: UUID = Field(validation_alias="id")
    conversation_id: UUID
    project_id: UUID | None
    execution_mode: str
    execution_directory: str | None
    worktree_id: UUID | None
    status: RunStatus
    started_at: datetime | None
    finished_at: datetime | None


@router.get("/conversations/{conversation_id}/runs", response_model=list[RunResponse])
async def conversation_runs(
    conversation_id: UUID, session: Annotated[AsyncSession, Depends(get_session)],
    user_key: Annotated[str, Query(min_length=1, max_length=128)] = "demo-user",
):
    conversation = await session.scalar(select(Conversation).join(User, User.id == Conversation.user_id)
                                       .where(Conversation.id == conversation_id, User.external_key == user_key))
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    runs = await session.scalars(select(Run).where(Run.conversation_id == conversation_id)
                                .order_by(Run.created_at.desc()).limit(50))
    return [RunResponse.model_validate(run) for run in runs]


async def owned_run(session: AsyncSession, run_id: UUID, user_key: str) -> Run:
    run = await session.scalar(select(Run).join(Conversation, Conversation.id == Run.conversation_id)
                               .join(User, User.id == Conversation.user_id)
                               .where(Run.id == run_id, User.external_key == user_key))
    if run is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return run


@router.get("/runs/{run_id}", response_model=RunResponse)
async def get_run(
    run_id: UUID, session: Annotated[AsyncSession, Depends(get_session)],
    user_key: Annotated[str, Query(min_length=1, max_length=128)] = "demo-user",
):
    return RunResponse.model_validate(await owned_run(session, run_id, user_key))


@router.post("/runs/{run_id}/cancel")
async def cancel_run(
    run_id: UUID, http_request: Request, session: Annotated[AsyncSession, Depends(get_session)],
    user_key: Annotated[str, Query(min_length=1, max_length=128)] = "demo-user",
):
    run = await owned_run(session, run_id, user_key)
    user_id = await session.scalar(select(Conversation.user_id).where(Conversation.id == run.conversation_id))
    requested = False
    if run.status in ACTIVE_STATUSES:
        requested = run_controller(http_request).cancel(run_id, user_id)
        if not requested:
            await session.refresh(run)
            if run.status in ACTIVE_STATUSES:
                raise HTTPException(status_code=409, detail="任务执行句柄不可用，请检查运行实例")
    return {"run_id": run_id, "status": run.status, "cancellation_requested": requested}


@router.get("/runs/{run_id}/events", response_model=list[RunEventResponse])
async def replay_run_events(
    run_id: UUID,
    user_key: Annotated[str, Query(min_length=1, max_length=128)] = "demo-user",
    after_sequence: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    session: Annotated[AsyncSession, Depends(get_session)] = None,
) -> list[RunEventResponse]:
    user = await ConversationRepository(session).get_user(user_key)
    if user is None:
        return []
    events = await ConversationRepository(session).list_run_events(
        run_id,
        user.id,
        after_sequence=after_sequence,
        limit=limit,
    )
    return [
        RunEventResponse.model_validate(event, from_attributes=True) for event in events
    ]
