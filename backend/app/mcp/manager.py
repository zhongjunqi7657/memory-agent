"""Application-owned MCP connections and immutable governed capability snapshots."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

from app.mcp.auth import OAuthGrants
from app.mcp.catalog import content_adapters
from app.mcp.client import MCPClient, classify_sdk_error
from app.mcp.config import MCPConfig
from app.mcp.naming import tool_names
from app.mcp.store import ConnectionStore
from app.mcp.tool import MCPToolAdapter
from app.security.tool_secrets import contains_secret
from app.tools.registry import ToolRegistry


class MCPConnectionManager:
    def __init__(self, config: MCPConfig, *, default_timeout_seconds: float = 8,
                 cwd: str | None = None, client_factory=MCPClient, store_path=None) -> None:
        self.store = ConnectionStore(Path(store_path)) if store_path else None
        self.config = self.store.load(config) if self.store else config.model_copy(deep=True)
        config = self.config
        self.timeout = default_timeout_seconds
        self.cwd = cwd or str(Path(__file__).resolve().parents[3])
        self.client_factory = client_factory
        self.grants = OAuthGrants()
        self._lock = asyncio.Lock()
        self._clients = {}
        self._tools: tuple[MCPToolAdapter, ...] = ()
        self._states = {server.id: {"id": server.id, "status": "pending" if config.enabled and server.enabled else "stopped",
                                  "tool_count": 0, "error_category": None} for server in config.servers}

    async def start(self, server_ids: frozenset[str] | None = None) -> None:
        registry = ToolRegistry()
        for tool in self._tools:
            registry.register(tool)
        for server in self.config.servers:
            if server_ids is not None and server.id not in server_ids:
                continue
            if not self.config.enabled or not server.enabled:
                continue
            if server.id in self._clients:
                continue
            client = self.client_factory(server, cwd=self.cwd, grants=self.grants)
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
                contents = await asyncio.wait_for(content_adapters(server, client, self.timeout),
                                                  server.command_timeout_seconds)
                for adapter in contents:
                    pending.register(adapter)
                # Publish the entire server only after every tool passes validation.
                ToolRegistry([*registry.snapshot(), *pending.snapshot()])
                for adapter in pending.snapshot():
                    registry.register(adapter)
                self._states[server.id].update(status="ready", tool_count=len(remote_tools),
                    resource_count=sum(tool.operation == "resource" for tool in contents),
                    prompt_count=sum(tool.operation == "prompt" for tool in contents),
                    capabilities=sorted(getattr(client, "capabilities", {"tools"})), error_category=None)
            except asyncio.CancelledError:
                await self.close()
                raise
            except Exception as error:  # noqa: BLE001 - isolate server startup failure
                self._states[server.id].update(status="failed", error_category=classify_sdk_error(error).category.value)
                await client.close()
        self._tools = registry.snapshot()

    async def disconnect(self, server_id):
        client = self._clients.pop(server_id, None)
        # Withdraw before awaiting shutdown; retained request snapshots hold a closed client.
        self._tools = tuple(tool for tool in self._tools if tool.client.config.id != server_id)
        self._states[server_id].update(status="stopped", tool_count=0, resource_count=0, prompt_count=0)
        if client:
            await client.close()

    async def reconnect(self, server_id):
        async with self._lock:
            server = next(item for item in self.config.servers if item.id == server_id)
            if not self.config.enabled or not server.enabled:
                raise ValueError("连接已禁用")
            await self.disconnect(server_id)
            await self.start(frozenset({server_id}))

    async def stop(self, server_id, *, revoke=False):
        async with self._lock:
            await self.disconnect(server_id)
            if revoke:
                self.grants.revoke(server_id)

    async def complete_authorization(self, state, code, callback_uri):
        async with self._lock:
            server_id = await self.grants.complete(state, code, callback_uri, self.config.servers)
            await self.disconnect(server_id)
            await self.start(frozenset({server_id}))
            return server_id

    async def configure(self, server):
        async with self._lock:
            if contains_secret(server.model_dump(mode="json")):
                raise ValueError("连接配置不能包含密钥，请使用环境变量引用")
            candidate = MCPConfig(enabled=self.config.enabled, allowed_commands=self.config.allowed_commands,
                servers=[item for item in self.config.servers if item.id != server.id] + [server])
            if self.store:
                self.store.save(server)
            self.grants.revoke(server.id)
            if server.id in self._states:
                await self.disconnect(server.id)
            self.config = candidate
            self._states[server.id] = {"id": server.id, "status": "stopped", "tool_count": 0, "error_category": None}
            # Saving configuration never starts a subprocess or network connection.

    def connections(self):
        states = {item["id"]: item for item in self.status()}
        return [{"config": server.model_dump(mode="json") if not contains_secret(server.model_dump(mode="json")) else None,
                 "state": states[server.id]}
                for server in self.config.servers]

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
                self._states[server_id].update(status="failed", error_category=client.failure.category.value,
                                               tool_count=0, resource_count=0, prompt_count=0)
        return deepcopy(list(self._states.values()))

    async def close(self) -> None:
        try:
            results = await asyncio.gather(*(client.close() for client in self._clients.values()), return_exceptions=True)
            if any(isinstance(result, BaseException) for result in results):
                raise RuntimeError("MCP 连接关闭失败")
        finally:
            for server_id in self._states:
                self._states[server_id].update(status="stopped", tool_count=0, resource_count=0, prompt_count=0)
            self._tools = ()
            for server_id in self._states:
                self.grants.revoke(server_id)
