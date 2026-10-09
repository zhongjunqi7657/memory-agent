"""Legacy Tool HTTP API adapters; this is not a standard MCP transport."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.business import BusinessConfig
from app.mcp.protocol import MCPToolSpec
from app.protocol.tools import ToolResult, ToolStatus
from app.tools.builtin.memory import LEGACY_TOOL_IDS
from app.tools.registry import ToolRegistry
from app.tools.request import build_request_registry


class MCPToolsResponse(BaseModel):
    tools: list[dict[str, Any]]


class MCPCallRequest(BaseModel):
    user_key: str = Field(default="demo-user", min_length=1, max_length=128)
    tool: str = Field(min_length=1, max_length=100)
    arguments: dict[str, Any] = Field(default_factory=dict)
    conversation_id: UUID
    source_message_id: UUID | None = None


class MCPCallResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    tool: str
    ok: bool
    content: str | None = None
    error: dict[str, str] | None = None

    @classmethod
    def from_result(cls, run_id: UUID, result: ToolResult, tool: str) -> MCPCallResponse:
        error = None
        if result.error:
            code = result.error.code
            if code == "internal_error":
                code = "execution_failed"
            elif code == "tools_disabled":
                code = "mcp_disabled"
            error = {"code": code, "message": result.error.message}
        return cls(
            run_id=run_id,
            tool=tool,
            ok=result.status is ToolStatus.SUCCESS,
            content=(result.content if isinstance(result.content, str) else
                     json.dumps(result.content, ensure_ascii=False))
                    if result.status is ToolStatus.SUCCESS else None,
            error=error,
        )


def build_registry(
    session: AsyncSession,
    *,
    user_id: UUID,
    conversation_id: UUID,
    source_message_id: UUID,
    business: BusinessConfig,
    emit=None,
    manager=None,
) -> ToolRegistry:
    """Build the same governed memory tools used by the main Agent graph."""

    return build_request_registry(
        session,
        user_id=user_id,
        conversation_id=conversation_id,
        source_message_id=source_message_id,
        business=business,
        emit=emit,
        manager=manager,
    )


def discovery_specs(
    session: AsyncSession, *, business: BusinessConfig, manager=None
) -> list[MCPToolSpec]:
    """Return discovery metadata without creating a user or persisting data."""

    registry = build_registry(
        session,
        user_id=UUID(int=0),
        conversation_id=UUID(int=0),
        source_message_id=uuid4(),
        business=business,
        manager=manager,
    )
    legacy_names = {value: key for key, value in LEGACY_TOOL_IDS.items()}
    return [MCPToolSpec(
        name=legacy_names.get(tool.id, tool.model_name), description=tool.description,
        input_schema=dict(tool.input_schema), output_schema={"type": "string"},
        permission=",".join(sorted(tool.required_permissions)), timeout_seconds=tool.timeout_seconds,
    ) for tool in registry.snapshot()]
