"""List built-in schemas without a database session or model."""

from uuid import UUID

from app.tools.builtin.memory import create_memory_tools


def list_tools(business) -> dict:
    tools = create_memory_tools(None, user_id=UUID(int=0), conversation_id=UUID(int=0),
        source_message_id=UUID(int=0), business=business,
        timeout_seconds=business.tools.default_timeout_seconds)
    return {"tools": [
        {"id": tool.id, "model_name": tool.model_name, "description": tool.description,
         "inputSchema": dict(tool.input_schema), "permissions": sorted(tool.required_permissions),
         "timeout_seconds": tool.timeout_seconds,
         "enabled": business.tools.enabled and tool.id in business.tools.allowed_builtin_tools}
        for tool in tools
    ]}
