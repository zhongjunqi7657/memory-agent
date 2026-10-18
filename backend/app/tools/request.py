"""Compose request-owned built-ins with application MCP snapshots."""

from app.tools.builtin.memory import MEMORY_PERMISSIONS, create_memory_tools
from app.tools.registry import ToolRegistry


def build_request_registry(session, *, user_id, conversation_id, source_message_id,
                           business, embedding_model=None, emit=None, manager=None):
    tools = create_memory_tools(
        session, user_id=user_id, conversation_id=conversation_id,
        source_message_id=source_message_id, business=business,
        embedding_model=embedding_model, emit=emit,
        timeout_seconds=business.tools.default_timeout_seconds,
    )
    enabled_ids = set(business.tools.allowed_builtin_tools)
    if manager and business.mcp.enabled:
        remote_tools = manager.snapshot()
        tools.extend(remote_tools)
        enabled_ids.update(tool.id for tool in remote_tools)
    return ToolRegistry(tools, enabled=business.tools.enabled, enabled_tool_ids=frozenset(enabled_ids))


def request_permissions(manager=None):
    return MEMORY_PERMISSIONS | (manager.granted_permissions if manager else frozenset())
