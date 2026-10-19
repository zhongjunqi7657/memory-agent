import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.audit.sink import CallbackAuditSink
from app.protocol.tools import (
    ToolCall,
    ToolErrorCategory,
    ToolExecutionContext,
    ToolResult,
    ToolStatus,
)
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry


@dataclass
class RecordingTool:
    id: str = "builtin.test.record"
    model_name: str = "builtin__test__record"
    remote_name: str | None = None
    description: str = "Record a value"
    input_schema: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
            "additionalProperties": False,
        }
    )
    required_permissions: frozenset[str] = frozenset({"memory.read"})
    timeout_seconds: float = 1.0
    calls: int = 0
    delay: float = 0
    raises: bool = False

    async def execute(self, arguments, context) -> ToolResult:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raises:
            raise RuntimeError("provider detail")
        return ToolResult.success(arguments["value"])


def context(**kwargs) -> ToolExecutionContext:
    values = {
        "run_id": "run-1",
        "agent_id": "agent-1",
        "granted_permissions": frozenset({"memory.read"}),
    }
    values.update(kwargs)
    return ToolExecutionContext(**values)


@pytest.mark.asyncio
async def test_invalid_arguments_do_not_execute_tool() -> None:
    tool = RecordingTool()
    result = await ToolExecutor(ToolRegistry([tool])).execute(
        ToolCall("call-1", tool.model_name, {"value": "wrong"}), context()
    )

    assert result.error and result.error.category is ToolErrorCategory.VALIDATION
    assert tool.calls == 0


@pytest.mark.asyncio
async def test_denied_call_does_not_execute_and_is_audited() -> None:
    tool = RecordingTool()
    events: list[str] = []

    async def emit(event_type: str, payload: dict[str, object]) -> None:
        events.append(event_type)

    result = await ToolExecutor(
        ToolRegistry([tool]), audit_sink=CallbackAuditSink(emit)
    ).execute(
        ToolCall("call-1", tool.model_name, {"value": 1}),
        context(granted_permissions=frozenset()),
    )

    assert result.status is ToolStatus.DENIED
    assert tool.calls == 0
    assert events == ["tool.execution.started", "tool.execution.denied"]


@pytest.mark.asyncio
async def test_success_has_exactly_one_start_and_terminal_event() -> None:
    tool = RecordingTool()
    events: list[tuple[str, dict[str, object]]] = []

    async def emit(event_type: str, payload: dict[str, object]) -> None:
        events.append((event_type, payload))

    result = await ToolExecutor(
        ToolRegistry([tool]), audit_sink=CallbackAuditSink(emit)
    ).execute(ToolCall("call-1", tool.model_name, {"value": 7}), context())

    assert result == ToolResult.success(7)
    assert [event[0] for event in events] == [
        "tool.execution.started",
        "tool.execution.completed",
    ]
    assert events[0][1]["argument_count"] == 1
    assert "arguments" not in events[0][1]


@pytest.mark.asyncio
async def test_timeout_and_internal_exception_are_normalized() -> None:
    timeout_tool = RecordingTool(delay=0.05, timeout_seconds=0.001)
    timeout = await ToolExecutor(ToolRegistry([timeout_tool])).execute(
        ToolCall("call-1", timeout_tool.id, {"value": 1}), context()
    )
    failure_tool = RecordingTool(id="builtin.test.fail", raises=True)
    failure = await ToolExecutor(ToolRegistry([failure_tool])).execute(
        ToolCall("call-2", failure_tool.id, {"value": 1}), context()
    )

    assert timeout.error and timeout.error.category is ToolErrorCategory.TIMEOUT
    assert failure.error and failure.error.category is ToolErrorCategory.INTERNAL
    assert "provider detail" not in failure.error.message


@pytest.mark.asyncio
async def test_cancellation_signal_stops_execution() -> None:
    tool = RecordingTool(delay=1)
    cancellation = asyncio.Event()
    cancellation.set()

    result = await ToolExecutor(ToolRegistry([tool])).execute(
        ToolCall("call-1", tool.id, {"value": 1}),
        context(cancellation=cancellation),
    )

    assert result.status is ToolStatus.CANCELLED
    assert tool.calls == 0


@pytest.mark.asyncio
async def test_external_cancellation_reaps_child_and_audits_once() -> None:
    entered, stopped = asyncio.Event(), asyncio.Event()
    tool = RecordingTool()

    async def execute(arguments, execution_context):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    tool.execute = execute
    events = []

    async def emit(event_type, payload):
        events.append(event_type)

    task = asyncio.create_task(ToolExecutor(
        ToolRegistry([tool]), audit_sink=CallbackAuditSink(emit)
    ).execute(ToolCall("cancel", tool.id, {"value": 1}), context()))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set()
    assert events == ["tool.execution.started", "tool.execution.cancelled"]


@pytest.mark.asyncio
async def test_rejected_calls_and_policy_exception_fail_closed() -> None:
    tool = RecordingTool()
    events = []

    async def emit(event_type, payload):
        events.append((event_type, payload))

    class BrokenPolicy:
        def decide(self, *args):
            raise RuntimeError("policy unavailable")

    executor = ToolExecutor(ToolRegistry([tool]), BrokenPolicy(), CallbackAuditSink(emit))
    for call in [ToolCall("unknown", "missing", {}),
                 ToolCall("invalid", tool.id, {"value": "secret-token"}),
                 ToolCall("denied", tool.id, {"value": 1})]:
        result = await executor.execute(call, context())
        assert result.status is not ToolStatus.SUCCESS
    assert tool.calls == 0
    assert [event[0] for event in events] == [
        "tool.execution.started", "tool.execution.failed",
        "tool.execution.started", "tool.execution.failed",
        "tool.execution.started", "tool.execution.denied",
    ]
    assert "secret-token" not in str(events)


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled,code", [(False, "tools_disabled"), (True, "permission_denied")])
async def test_tool_configuration_gates_apply_to_direct_calls(enabled, code):
    tool = RecordingTool()
    registry = ToolRegistry([tool], enabled=enabled, enabled_tool_ids=frozenset())
    result = await ToolExecutor(registry).execute(ToolCall("call", tool.id, {"value": 1}), context())
    assert result.status is ToolStatus.DENIED
    assert result.error.code == code
    assert tool.calls == 0


@pytest.mark.asyncio
async def test_cancellation_event_during_call_waits_for_cleanup():
    entered, stopped, cancellation = asyncio.Event(), asyncio.Event(), asyncio.Event()
    tool = RecordingTool()

    async def execute(arguments, execution_context):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    tool.execute = execute
    task = asyncio.create_task(ToolExecutor(ToolRegistry([tool])).execute(
        ToolCall("cancel", tool.id, {"value": 1}), context(cancellation=cancellation)))
    await entered.wait()
    cancellation.set()
    result = await task
    assert result.status is ToolStatus.CANCELLED and stopped.is_set()
