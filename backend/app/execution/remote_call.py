"""Legacy remote calls use the same durable approval lifecycle as Agent calls."""

import asyncio
from uuid import uuid4

from app.audit.sink import CallbackAuditSink
from app.execution.lifecycle import RunLifecycle
from app.mcp.transport import MCPCallResponse, build_registry
from app.persistence.models import RunStatus
from app.persistence.repositories import ConversationRepository
from app.protocol.tools import ToolCall, ToolExecutionContext, ToolStatus
from app.tools.executor import ToolExecutor
from app.tools.request import request_permissions
from app.workspace.service import project_directory


async def remote_call(request, session, user, conversation, business, manager, controller, broker):
    lifecycle = RunLifecycle(session, controller, None)
    try:
        directory = await project_directory(session, user.id, conversation.project_id)
        lifecycle.reserve(user.id, conversation.id, directory)
        repository = ConversationRepository(session)
        run = await repository.create_run(conversation.id, id=lifecycle.run_id, project_id=conversation.project_id,
                                          execution_directory=directory, execution_mode="original")
        await lifecycle.emit("run.started", {"conversation_id": str(conversation.id)})
        registry = build_registry(session, user_id=user.id, conversation_id=conversation.id,
                                  source_message_id=request.source_message_id or uuid4(), business=business,
                                  emit=lifecycle.emit, manager=manager)

        async def authorize(tool, call, context):
            return await broker.authorize(tool, call, context, lifecycle, business.local.approval_timeout_seconds)

        context = ToolExecutionContext(str(run.id), "legacy-http", user_id=str(user.id), conversation_id=str(conversation.id),
                                       project_id=str(conversation.project_id) if conversation.project_id else None,
                                       execution_directory=directory, granted_permissions=request_permissions(),
                                       authorize=authorize, audit_sink=CallbackAuditSink(lifecycle.emit))
        result = await ToolExecutor(registry).execute(ToolCall(str(uuid4()), request.tool, request.arguments), context)
        await repository.finish_run(run, status=RunStatus.COMPLETED if result.status is ToolStatus.SUCCESS else RunStatus.FAILED,
                                     error_message=result.error.message if result.error else None)
        await lifecycle.emit("mcp.call.completed", {"tool": request.tool, "ok": result.status is ToolStatus.SUCCESS})
        await lifecycle.emit("run.completed" if result.status is ToolStatus.SUCCESS else "run.failed", {"message": "工具调用已结束"})
        return MCPCallResponse.from_result(run.id, result, request.tool)
    except asyncio.CancelledError as error:
        if lifecycle.registered:
            await lifecycle.terminate(RunStatus.CANCELLED, error)
        raise
    except Exception as error:
        if lifecycle.registered:
            await lifecycle.terminate(RunStatus.FAILED, error)
        raise
    finally:
        controller.release(lifecycle.run_id)
