"""Single asynchronous protocol implemented by every tool source."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from app.protocol.tools import ToolExecutionContext, ToolResult


class Tool(Protocol):
    id: str
    model_name: str
    remote_name: str | None
    description: str
    input_schema: Mapping[str, Any]
    required_permissions: frozenset[str]
    timeout_seconds: float

    async def execute(
        self,
        arguments: Mapping[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult: ...
