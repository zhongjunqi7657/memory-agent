"""Protocol-independent tool call and result types."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol


class AuditWriter(Protocol):
    async def emit(self, event_type: str, payload: dict[str, object]) -> None: ...


class ToolStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    DENIED = "denied"
    CANCELLED = "cancelled"


class ToolErrorCategory(str, Enum):
    VALIDATION = "validation"
    PERMISSION = "permission"
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    PROTOCOL = "protocol"
    BUSINESS = "business"
    INTERNAL = "internal"


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolError:
    code: str
    category: ToolErrorCategory
    message: str
    retryable: bool = False


@dataclass(frozen=True)
class ToolResult:
    status: ToolStatus
    content: Any = None
    error: ToolError | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def success(
        cls, content: Any = None, *, metadata: dict[str, Any] | None = None
    ) -> ToolResult:
        return cls(
            status=ToolStatus.SUCCESS,
            content=content,
            metadata=metadata or {},
        )

    @classmethod
    def failure(
        cls,
        *,
        code: str,
        category: ToolErrorCategory,
        message: str,
        retryable: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> ToolResult:
        return cls(
            status=ToolStatus.FAILED,
            error=ToolError(code, category, message, retryable),
            metadata=metadata or {},
        )


@dataclass(frozen=True)
class ToolExecutionContext:
    run_id: str
    agent_id: str
    parent_run_id: str | None = None
    user_id: str | None = None
    conversation_id: str | None = None
    source_message_id: str | None = None
    granted_permissions: frozenset[str] = field(default_factory=frozenset)
    audit_sink: AuditWriter | None = None
    cancellation: asyncio.Event | None = None
    project_id: str | None = None
    execution_directory: str | None = None
    execution_mode: str = "original"
    authorize: Callable[..., Awaitable[bool]] | None = None
    change_journal: Any = None
