"""Compose request-owned built-ins with application MCP snapshots."""

from app.tools.builtin.memory import MEMORY_PERMISSIONS, create_memory_tools
from app.tools.local.registry import LOCAL_PERMISSIONS, create_local_tools
from app.tools.registry import ToolRegistry


def build_request_registry(session, *, user_id, conversation_id, source_message_id,
                           business, embedding_model=None, emit=None, manager=None, execution_directory=None, project_id=None):
    tools = create_memory_tools(
        session, user_id=user_id, conversation_id=conversation_id,
        source_message_id=source_message_id, business=business,
        embedding_model=embedding_model, emit=emit, project_id=project_id,
        timeout_seconds=business.tools.default_timeout_seconds,
    )
    enabled_ids = set(business.tools.allowed_builtin_tools)
    if business.local.enabled and execution_directory:
        local_tools = create_local_tools(business.local)
        tools.extend(local_tools)
        enabled_ids.update(tool.id for tool in local_tools)
    if manager and business.mcp.enabled:
        remote_tools = manager.snapshot()
        tools.extend(remote_tools)
        enabled_ids.update(tool.id for tool in remote_tools)
    return ToolRegistry(tools, enabled=business.tools.enabled, enabled_tool_ids=frozenset(enabled_ids))


def request_permissions(manager=None, *, local=False):
    # MCP connection configuration describes required capabilities, not user grants.
    return MEMORY_PERMISSIONS | (LOCAL_PERMISSIONS if local else frozenset())
