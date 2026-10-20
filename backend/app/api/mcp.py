"""Protected legacy Tool HTTP API (not the standard MCP protocol)."""

from __future__ import annotations

import asyncio
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.approvals import approval_broker
from app.api.execution import run_controller
from app.audit.sink import CallbackAuditSink
from app.config.business import get_business_config
from app.execution.controller import RunConflict
from app.execution.remote_call import remote_call
from app.mcp.transport import (
    MCPCallRequest,
    MCPCallResponse,
    MCPToolsResponse,
    build_registry,
    discovery_specs,
)
from app.persistence.db import get_session
from app.persistence.models import MessageRole, RunStatus
from app.persistence.repositories import ConversationRepository
from app.protocol.tools import ToolCall, ToolExecutionContext, ToolStatus
from app.security.auth import require_demo_auth
from app.tools.builtin.memory import LEGACY_TOOL_IDS
from app.tools.executor import ToolExecutor
from app.tools.request import request_permissions
from app.workspace.paths import DirectoryError

router = APIRouter(
    prefix="/v1/mcp",
    tags=["mcp"],
    dependencies=[Depends(require_demo_auth)],
)


@router.get("/status")
async def mcp_status(http_request: Request) -> dict:
    manager = getattr(http_request.app.state, "mcp_manager", None)
    return {"servers": manager.status() if manager else []}


@router.get("/tools", response_model=MCPToolsResponse)
async def list_tools(
    session: Annotated[AsyncSession, Depends(get_session)],
    http_request: Request,
) -> MCPToolsResponse:
    business = get_business_config()
    return MCPToolsResponse(
        tools=[spec.as_dict() for spec in discovery_specs(session, business=business,
               manager=getattr(http_request.app.state, "mcp_manager", None))]
    )


@router.post("/call", response_model=MCPCallResponse)
async def call_tool(
    request: MCPCallRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    http_request: Request = None,
) -> MCPCallResponse:
    repository = ConversationRepository(session)
    manager = getattr(http_request.app.state, "mcp_manager", None) if http_request else None
    user = await repository.get_user(request.user_key)
    conversation = (
        await repository.get_conversation(request.conversation_id, user.id)
        if user
        else None
    )
    if user is None or conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    source_message_id = request.source_message_id or UUID(int=0)
    tool_id = LEGACY_TOOL_IDS.get(request.tool, request.tool)
    if (tool_id in {"builtin.memory.propose_update", "builtin__memory__propose_update"}
            and request.source_message_id is None):
        raise HTTPException(status_code=400, detail="写入工具需要来源消息")
    if request.source_message_id is not None:
        source_message = await repository.get_message(
            request.source_message_id, conversation.id
        )
        if source_message is None or source_message.role is not MessageRole.USER:
            raise HTTPException(status_code=400, detail="来源消息不存在或不可用")

    if tool_id.startswith(("mcp.", "mcp__")):
        try:
            return await remote_call(request, session, user, conversation, get_business_config(), manager,
                                     run_controller(http_request), approval_broker(http_request))
        except (RunConflict, DirectoryError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    run = await repository.create_run(conversation.id)
    event_sequence = 0

    async def emit(event_type: str, payload: dict[str, object]) -> None:
        nonlocal event_sequence
        event_sequence += 1
        await repository.add_run_event(
            run.id,
            sequence=event_sequence,
            event_type=event_type,
            payload=payload,
        )

    try:
        registry = build_registry(
            session,
            user_id=user.id,
            conversation_id=conversation.id,
            source_message_id=source_message_id,
            business=get_business_config(),
            emit=emit,
            manager=manager,
            project_id=getattr(conversation, "project_id", None),
        )
        context = ToolExecutionContext(
            run_id=str(run.id), agent_id="legacy-http",
            user_id=str(user.id), conversation_id=str(conversation.id),
            source_message_id=str(source_message_id), granted_permissions=request_permissions(manager),
            audit_sink=CallbackAuditSink(emit),
            project_id=str(conversation.project_id) if getattr(conversation, "project_id", None) else None,
        )
        result = await ToolExecutor(registry).execute(
            ToolCall(str(uuid4()), tool_id, request.arguments), context
        )
        await emit(
            "mcp.call.completed",
            {
                "tool": request.tool,
                "ok": result.status is ToolStatus.SUCCESS,
                "error_code": result.error.code if result.error else None,
            },
        )
        await repository.finish_run(
            run,
            status=RunStatus.COMPLETED if result.status is ToolStatus.SUCCESS else RunStatus.FAILED,
            error_message=result.error.message if result.error else None,
        )
        await session.commit()
        return MCPCallResponse.from_result(run.id, result, request.tool)
    except asyncio.CancelledError:
        await repository.finish_run(
            run, status=RunStatus.FAILED, error_message="工具调用已取消"
        )
        await session.commit()
        raise
    except Exception:
        await repository.finish_run(run, status=RunStatus.FAILED)
        await session.commit()
        raise
