"""Validation, authorization, timeout, cancellation and audit pipeline."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Mapping
from typing import Any

from jsonschema.validators import validator_for

from app.audit.sink import AuditSink, NullAuditSink
from app.permission.policy import PermissionPolicy
from app.protocol.tools import (
    ToolCall,
    ToolError,
    ToolErrorCategory,
    ToolExecutionContext,
    ToolResult,
    ToolStatus,
)
from app.tools.base import Tool
from app.tools.registry import ToolRegistry


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        permission_policy: PermissionPolicy | None = None,
        audit_sink: AuditSink | None = None,
    ) -> None:
        self._registry = registry
        self._permission_policy = permission_policy or PermissionPolicy()
        self._audit_sink = audit_sink or NullAuditSink()

    async def execute(
        self, call: ToolCall, context: ToolExecutionContext
    ) -> ToolResult:
        tool = self._registry.get(call.tool_name)
        started = time.perf_counter()
        result = self._prepare(call, tool, context)
        sink = context.audit_sink or self._audit_sink
        await sink.emit(
            "tool.execution.started",
            self._audit_payload(call, tool, context, None, 0),
        )
        try:
            if result is None:
                assert tool is not None
                result = await self._run_tool(tool, call.arguments, context)
                if not isinstance(result, ToolResult) or not isinstance(result.status, ToolStatus) or (
                    result.status is ToolStatus.SUCCESS and result.error is not None
                ) or (result.status is not ToolStatus.SUCCESS and result.error is None):
                    raise ValueError("Invalid tool result")
        except asyncio.CancelledError:
            result = self._cancelled_result()
            await self._audit_terminal(
                "tool.execution.cancelled", call, tool, context, result,
                self._duration_ms(started),
            )
            raise
        except Exception:  # noqa: BLE001 - provider details must be normalized
            result = ToolResult.failure(
                code="internal_error", category=ToolErrorCategory.INTERNAL,
                message="工具执行失败",
            )
        event_type = f"tool.execution.{result.status.value}"
        if result.status is ToolStatus.SUCCESS:
            event_type = "tool.execution.completed"
        await self._audit_terminal(
            event_type, call, tool, context, result, self._duration_ms(started)
        )
        return result

    def _prepare(
        self, call: ToolCall, tool: Tool | None, context: ToolExecutionContext
    ) -> ToolResult | None:
        if tool is None:
            return ToolResult.failure(
                code="unknown_tool",
                category=ToolErrorCategory.VALIDATION,
                message="未找到请求的工具",
            )

        validation_error = self._validate_arguments(tool, call.arguments)
        if validation_error:
            return ToolResult.failure(
                code="invalid_arguments",
                category=ToolErrorCategory.VALIDATION,
                message=validation_error,
            )

        if not self._registry.is_enabled(tool):
            return ToolResult(
                status=ToolStatus.DENIED,
                error=ToolError("tools_disabled" if not self._registry.enabled else "permission_denied",
                                ToolErrorCategory.PERMISSION, "工具调用已禁用" if not self._registry.enabled
                                else "工具不在当前启用白名单中"),
            )

        try:
            decision = self._permission_policy.decide(
                tool.required_permissions, context.granted_permissions
            )
        except Exception:  # noqa: BLE001 - policy failures must fail closed
            decision = None
        if decision is None or not decision.allowed:
            missing = sorted(decision.missing_permissions) if decision else []
            result = ToolResult(
                status=ToolStatus.DENIED,
                error=ToolError(
                    "permission_denied",
                    ToolErrorCategory.PERMISSION,
                    "工具执行权限不足",
                ),
                metadata={"missing_permissions": missing},
            )
            return result
        return None

    async def _run_tool(
        self,
        tool: Tool,
        arguments: Mapping[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        if context.cancellation and context.cancellation.is_set():
            return self._cancelled_result()

        execution = asyncio.create_task(tool.execute(arguments, context))
        cancellation = (
            asyncio.create_task(context.cancellation.wait())
            if context.cancellation
            else None
        )
        waiting = {execution, *([cancellation] if cancellation else [])}
        try:
            done, _ = await asyncio.wait(
                waiting, timeout=tool.timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancellation in done:
                return self._cancelled_result()
            if execution in done:
                return await execution
            return ToolResult.failure(
                code="timeout", category=ToolErrorCategory.TIMEOUT,
                message="工具调用超时", retryable=False,
            )
        finally:
            for task in waiting:
                if not task.done():
                    task.cancel()
            for task in waiting:
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    @staticmethod
    def _cancelled_result() -> ToolResult:
        return ToolResult(
            status=ToolStatus.CANCELLED,
            error=ToolError("cancelled", ToolErrorCategory.INTERNAL, "工具调用已取消"),
        )

    @staticmethod
    def _validate_arguments(tool: Tool, arguments: Mapping[str, Any]) -> str | None:
        schema = dict(tool.input_schema)
        validator = validator_for(schema)(schema)
        try:
            error = next(iter(validator.iter_errors(dict(arguments))), None)
        except Exception:  # noqa: BLE001 - unresolved schema cannot authorize execution
            return "工具输入 Schema 无法校验"
        return "工具参数不符合输入 Schema" if error else None

    async def _audit_terminal(
        self,
        event_type: str,
        call: ToolCall,
        tool: Tool | None,
        context: ToolExecutionContext,
        result: ToolResult,
        duration_ms: float,
    ) -> None:
        sink = context.audit_sink or self._audit_sink
        await sink.emit(
            event_type,
            self._audit_payload(call, tool, context, result, duration_ms),
        )

    @staticmethod
    def _audit_payload(
        call: ToolCall,
        tool: Tool | None,
        context: ToolExecutionContext,
        result: ToolResult | None,
        duration_ms: float | None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "call_id": call.call_id,
            "tool_id": tool.id if tool else call.tool_name,
            "model_name": tool.model_name if tool else call.tool_name,
            "source": ("mcp" if tool.remote_name else "builtin") if tool else "unknown",
            "agent_id": context.agent_id,
            "run_id": context.run_id,
            "argument_count": len(call.arguments),
            "error_category": result.error.category.value if result and result.error else None,
            "error_code": result.error.code if result and result.error else None,
        }
        if duration_ms is not None:
            payload["duration_ms"] = duration_ms
        return payload

    @staticmethod
    def _duration_ms(started: float) -> float:
        return round((time.perf_counter() - started) * 1000, 2)
