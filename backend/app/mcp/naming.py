"""Deterministic MCP internal, model and remote name mapping."""

import re

_SERVER_ID = re.compile(r"[A-Za-z0-9_-]+\Z")
_REMOTE_NAME = re.compile(r"[A-Za-z0-9_.-]+\Z")


def validate_server_id(value: str) -> str:
    if not _SERVER_ID.fullmatch(value) or "__" in value:
        raise ValueError("MCP Server ID 含非法字符或分隔符")
    return value


def tool_names(server_id: str, remote_name: str) -> tuple[str, str]:
    validate_server_id(server_id)
    if not _REMOTE_NAME.fullmatch(remote_name) or "__" in remote_name:
        raise ValueError("MCP 工具名称含非法字符或分隔符")
    model_name = f"mcp__{server_id}__{remote_name.replace('.', '_')}"
    if len(model_name) > 64:
        raise ValueError("MCP 模型工具名称超过 64 字符")
    return f"mcp.{server_id}.{remote_name}", model_name
