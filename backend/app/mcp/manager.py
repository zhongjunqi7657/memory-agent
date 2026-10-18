"""Application-owned stdio connections and immutable remote tool snapshots."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

from app.mcp.client import MCPClient, classify_sdk_error
from app.mcp.config import MCPConfig
from app.mcp.naming import tool_names
from app.mcp.tool import MCPToolAdapter
from app.tools.registry import ToolRegistry


class MCPConnectionManager:
    def __init__(self, config: MCPConfig, *, default_timeout_seconds: float = 8,
                 cwd: str | None = None, client_factory=MCPClient) -> None:
        self.config = config
        self.timeout = default_timeout_seconds
        self.cwd = cwd or str(Path(__file__).resolve().parents[3])
        self.client_factory = client_factory
        self._clients = {}
        self._tools: tuple[MCPToolAdapter, ...] = ()
        self._states = {server.id: {"id": server.id, "status": "pending" if config.enabled and server.enabled else "stopped",
                                  "tool_count": 0, "error_category": None} for server in config.servers}

    async def start(self) -> None:
        registry = ToolRegistry()
        for server in self.config.servers:
            if not self.config.enabled or not server.enabled:
                continue
            client = self.client_factory(server, cwd=self.cwd)
            self._clients[server.id] = client
            try:
                await client.start()
                remote_tools = await asyncio.wait_for(client.list_tools(), server.command_timeout_seconds)
                if server.allowed_tools is not None:
                    if server.allowed_tools - {remote.name for remote in remote_tools}:
                        raise ValueError("MCP 工具白名单包含未发现的工具")
                    remote_tools = tuple(remote for remote in remote_tools if remote.name in server.allowed_tools)
                pending = ToolRegistry()
                for remote in remote_tools:
                    tool_id, model_name = tool_names(server.id, remote.name)
                    adapter = MCPToolAdapter(
                        tool_id, model_name, remote.name, remote.description or "",
                        deepcopy(remote.inputSchema), server.permissions_for(remote.name), self.timeout, client,
                    )
                    pending.register(adapter)
                # Publish the entire server only after every tool passes validation.
                for adapter in pending.snapshot():
                    registry.register(adapter)
                self._states[server.id].update(status="ready", tool_count=len(pending.snapshot()))
            except asyncio.CancelledError:
                await self.close()
                raise
            except Exception as error:  # noqa: BLE001 - isolate server startup failure
                self._states[server.id].update(status="failed", error_category=classify_sdk_error(error).category.value)
                await client.close()
        self._tools = registry.snapshot()

    def snapshot(self) -> tuple[MCPToolAdapter, ...]:
        self.status()
        return tuple(replace(tool, input_schema=deepcopy(dict(tool.input_schema))) for tool in self._tools
                     if self._states[tool.client.config.id]["status"] == "ready")

    @property
    def granted_permissions(self) -> frozenset[str]:
        return frozenset(capability for server in self.config.servers
                         if self.config.enabled and server.enabled and server.default_permissions
                         for capability in server.default_permissions.capabilities)

    def status(self) -> list[dict]:
        for server_id, client in self._clients.items():
            if client.failure and self._states[server_id]["status"] == "ready":
                self._states[server_id].update(status="failed", error_category=client.failure.category.value)
        return deepcopy(list(self._states.values()))

    async def close(self) -> None:
        try:
            results = await asyncio.gather(*(client.close() for client in self._clients.values()), return_exceptions=True)
            if any(isinstance(result, BaseException) for result in results):
                raise RuntimeError("MCP 连接关闭失败")
        finally:
            for server_id in self._states:
                self._states[server_id].update(status="stopped", tool_count=0)
            self._tools = ()
