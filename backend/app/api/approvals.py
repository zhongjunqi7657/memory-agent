"""Reviewable pending approvals and compare-and-set single-use decisions."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.permission.approvals import ApprovalBroker, ApprovalError, default_broker
from app.persistence.approvals import Approval
from app.persistence.db import get_session
from app.persistence.models import User
from app.persistence.repositories import ConversationRepository
from app.security.auth import require_demo_auth

router = APIRouter(prefix="/v1/approvals", tags=["approvals"], dependencies=[Depends(require_demo_auth)])


def approval_broker(request: Request | None) -> ApprovalBroker:
    return getattr(request.app.state, "approval_broker", default_broker) if request else default_broker


class ApprovalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    run_id: UUID
    call_id: str
    tool_id: str
    fingerprint: str
    binding: dict
    status: str
    session_allowed: bool
    expires_at: datetime
    warning: str


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fingerprint: str = Field(min_length=64, max_length=64)
    decision: Literal["approve_once", "approve_session", "reject"]


class RevokeRequest(BaseModel):
    conversation_id: UUID


@router.post("/revoke")
async def revoke_session_grants(
    request: RevokeRequest, http_request: Request, session: Annotated[AsyncSession, Depends(get_session)],
    user_key: Annotated[str, Query(min_length=1, max_length=128)] = "demo-user",
):
    repository = ConversationRepository(session)
    user = await repository.get_user(user_key)
    conversation = await repository.get_conversation(request.conversation_id, user.id) if user else None
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    approval_broker(http_request).revoke(str(user.id), str(conversation.id))
    return {"conversation_id": conversation.id, "revoked": True}


@router.get("", response_model=list[ApprovalResponse])
async def pending_approvals(
    session: Annotated[AsyncSession, Depends(get_session)],
    user_key: Annotated[str, Query(min_length=1, max_length=128)] = "demo-user",
    run_id: UUID | None = None,
):
    statement = select(Approval).join(User, User.id == Approval.user_id).where(User.external_key == user_key, Approval.status == "pending")
    if run_id:
        statement = statement.where(Approval.run_id == run_id)
    return [ApprovalResponse.model_validate(row) for row in await session.scalars(statement.order_by(Approval.created_at).limit(100))]


@router.post("/{approval_id}/decision")
async def decide_approval(
    approval_id: UUID, request: DecisionRequest, http_request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    user_key: Annotated[str, Query(min_length=1, max_length=128)] = "demo-user",
):
    user = await ConversationRepository(session).get_user(user_key)
    if user is None:
        raise HTTPException(status_code=404, detail="审批不存在")
    try:
        await approval_broker(http_request).decide(session, approval_id, user.id, request.fingerprint, request.decision)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ApprovalError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"approval_id": approval_id, "decision": request.decision}
