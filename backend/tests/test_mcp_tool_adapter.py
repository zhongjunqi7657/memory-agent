from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp.shared.exceptions import McpError
from mcp.types import CallToolResult, ErrorData, TextContent

from app.mcp.client import MCPClientError, classify_sdk_error
from app.mcp.tool import MCPToolAdapter
from app.protocol.tools import ToolErrorCategory, ToolExecutionContext, ToolStatus


@pytest.mark.asyncio
async def test_adapter_calls_remote_original_name_and_preserves_structured_content():
    client = SimpleNamespace(call_tool=AsyncMock(return_value=CallToolResult(
        content=[TextContent(type="text", text="hello")], structuredContent={"value": 1}
    )))
    adapter = MCPToolAdapter("mcp.a.echo", "mcp__a__echo", "remote.echo", "Echo",
        {"type": "object"}, frozenset(), 1, client)
    result = await adapter.execute({"value": 1}, ToolExecutionContext("run", "agent"))
    client.call_tool.assert_awaited_once_with("remote.echo", {"value": 1})
    assert result.content == [{"type": "text", "text": "hello"}]
    assert result.metadata["structured_content"] == {"value": 1}
    client.call_tool.return_value = CallToolResult(content=[], isError=True)
    result = await adapter.execute({}, ToolExecutionContext("run", "agent"))
    assert result.status is ToolStatus.FAILED
    assert result.error.category is ToolErrorCategory.BUSINESS


@pytest.mark.asyncio
@pytest.mark.parametrize("category", [ToolErrorCategory.CONNECTION, ToolErrorCategory.PROTOCOL])
async def test_adapter_keeps_sdk_failure_categories(category):
    client = SimpleNamespace(call_tool=AsyncMock(side_effect=MCPClientError(category, "failure")))
    adapter = MCPToolAdapter("mcp.a.echo", "mcp__a__echo", "echo", "Echo",
        {"type": "object"}, frozenset(), 1, client)
    result = await adapter.execute({}, ToolExecutionContext("run", "agent"))
    assert result.error.category is category


def test_sdk_json_rpc_error_and_disconnect_are_distinct():
    assert classify_sdk_error(McpError(ErrorData(code=-32602, message="invalid params"))).category is ToolErrorCategory.PROTOCOL
    assert classify_sdk_error(McpError(ErrorData(code=-32000, message="closed"))).category is ToolErrorCategory.CONNECTION
