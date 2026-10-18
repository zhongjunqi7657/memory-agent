"""MCP CLI operations reuse the application connection manager."""

import base64
import json
import os
from urllib.request import Request, urlopen

from app.mcp.config import MCPConfig
from app.mcp.manager import MCPConnectionManager


def list_servers(business) -> dict:
    return {"servers": [{"id": server.id, "enabled": business.mcp.enabled and server.enabled,
                         "transport": server.transport, "command": server.command}
                        for server in business.mcp.servers]}


def server_status(api_url: str) -> dict:
    request = Request(f"{api_url.rstrip('/')}/v1/mcp/status")
    password = os.environ.get("DEMO_SHARED_PASSWORD")
    if password:
        credential = base64.b64encode(f"demo:{password}".encode()).decode()
        request.add_header("Authorization", f"Basic {credential}")
    with urlopen(request, timeout=10) as response:
        return json.load(response)


async def test_server(business, server_id: str, *, cwd=None) -> tuple[dict, int]:
    server = next((item for item in business.mcp.servers if item.id == server_id), None)
    if server is None:
        raise ValueError("配置中未找到 MCP Server")
    config = MCPConfig(enabled=True, allowed_commands=business.mcp.allowed_commands,
                       servers=[server.model_copy(update={"enabled": True})])
    manager = MCPConnectionManager(config, default_timeout_seconds=business.tools.default_timeout_seconds, cwd=cwd)
    try:
        await manager.start()
        status = manager.status()[0]
        tools = [{"id": tool.id, "model_name": tool.model_name, "remote_name": tool.remote_name,
                  "inputSchema": dict(tool.input_schema), "permissions": sorted(tool.required_permissions)}
                 for tool in manager.snapshot()]
        return {"server": status, "tools": tools}, 0 if status["status"] == "ready" else 1
    finally:
        await manager.close()
