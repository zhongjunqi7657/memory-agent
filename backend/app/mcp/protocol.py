"""Compatibility imports and metadata for the legacy Tool HTTP API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.protocol.tools import (
    ToolCall,
    ToolError,
    ToolErrorCategory,
    ToolExecutionContext,
    ToolResult,
    ToolStatus,
)

__all__ = ["MCPToolSpec", "ToolCall", "ToolError", "ToolErrorCategory", "ToolExecutionContext", "ToolResult", "ToolStatus"]


@dataclass(frozen=True)
class MCPToolSpec:
    """Discoverable metadata for one registered tool."""

    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    permission: str
    timeout_seconds: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
            "outputSchema": self.output_schema,
            "permission": self.permission,
            "timeoutSeconds": self.timeout_seconds,
        }
