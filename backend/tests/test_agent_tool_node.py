import pytest
from langchain_core.messages import AIMessage

from app.agent.tool_node import UnifiedToolNode, tool_message
from app.protocol.tools import ToolExecutionContext, ToolResult
from app.tools.builtin.memory import MemoryTool
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_tool_node_pairs_ids_and_continues_after_failure():
    calls = []

    async def execute(arguments):
        calls.append(arguments["value"])
        return ToolResult.success({"value": arguments["value"]})

    registry = ToolRegistry([MemoryTool(
        "builtin.test.echo", "echo", "Echo", {"type": "object", "required": ["value"]},
        frozenset(), 1, execute,
    )])
    node = UnifiedToolNode(ToolExecutor(registry), ToolExecutionContext("run", "agent"))
    result = await node({"messages": [AIMessage(content="", tool_calls=[
        {"id": "first", "name": "missing", "args": {}},
        {"id": "second", "name": "echo", "args": {"value": 2}},
    ])]})
    assert [m.tool_call_id for m in result["messages"]] == ["first", "second"]
    assert [m.status for m in result["messages"]] == ["error", "success"]
    assert "unknown_tool" in result["messages"][0].content
    assert calls == [2]
    assert result["tool_rounds"] == 1


def test_structured_only_mcp_result_is_visible_to_model():
    message = tool_message("structured", "remote", ToolResult.success([], metadata={
        "structured_content": {"value": "structured-result"},
    }))
    assert "structured-result" in message.content
    assert message.status == "success"
