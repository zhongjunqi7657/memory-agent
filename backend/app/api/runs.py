"""Replayable run event endpoint used after an SSE reconnect."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.db import get_session
from app.persistence.repositories import ConversationRepository

router = APIRouter(prefix="/v1", tags=["runs"])


class RunEventResponse(BaseModel):
    run_id: UUID
    sequence: int
    event_type: str
    payload: dict


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
