"""Map MCP SDK content and failures onto the unified Tool protocol."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.mcp.client import MCPClient, MCPClientError
from app.protocol.tools import ToolErrorCategory, ToolExecutionContext, ToolResult


@dataclass(frozen=True)
class MCPToolAdapter:
    id: str
    model_name: str
    remote_name: str
    description: str
    input_schema: Mapping[str, Any]
    required_permissions: frozenset[str]
    timeout_seconds: float
    client: MCPClient

    async def execute(self, arguments: Mapping[str, Any], context: ToolExecutionContext) -> ToolResult:
        try:
            result = await self.client.call_tool(self.remote_name, dict(arguments))
        except MCPClientError as error:
            return ToolResult.failure(
                code=f"mcp_{error.category.value}", category=error.category, message=str(error),
            )
        content = [item.model_dump(mode="json", by_alias=True, exclude_none=True) for item in result.content]
        metadata = {"structured_content": result.structuredContent} if result.structuredContent is not None else {}
        if result.isError:
            failure = ToolResult.failure(code="mcp_business", category=ToolErrorCategory.BUSINESS,
                                         message="MCP 工具返回业务错误", metadata=metadata)
            return ToolResult(failure.status, content, failure.error, failure.metadata)
        return ToolResult.success(content, metadata=metadata)
