"""Official MCP SDK stdio/Streamable HTTP client with task-owned sessions."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
from contextlib import AsyncExitStack
from typing import Any

import anyio
import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import McpError
from mcp.types import CONNECTION_CLOSED, Tool
from pydantic import ValidationError

from app.mcp.auth import AuthenticationRequired, MCPHTTPAuth, OAuthGrants
from app.mcp.config import MCPServerConfig
from app.mcp.http_security import http_client
from app.mcp.runtime import SDKLoopBridge
from app.protocol.tools import ToolErrorCategory


class MCPClientError(RuntimeError):
    def __init__(self, category: ToolErrorCategory, message: str) -> None:
        super().__init__(message)
        self.category = category


def classify_sdk_error(error: BaseException) -> MCPClientError:
    if isinstance(error, MCPClientError):
        return error
    if isinstance(error, AuthenticationRequired):
        return MCPClientError(ToolErrorCategory.PERMISSION, str(error))
    if isinstance(error, McpError):
        if error.error.code == CONNECTION_CLOSED:
            return MCPClientError(ToolErrorCategory.CONNECTION, "MCP 连接已断开")
        return MCPClientError(ToolErrorCategory.PROTOCOL, "MCP 协议请求失败")
    if isinstance(error, (ValidationError, json.JSONDecodeError)):
        return MCPClientError(ToolErrorCategory.PROTOCOL, "MCP 协议响应无效")
    if isinstance(error, (TimeoutError, asyncio.TimeoutError, httpx.TimeoutException)):
        return MCPClientError(ToolErrorCategory.TIMEOUT, "MCP 连接或握手超时")
    if isinstance(error, (OSError, httpx.TransportError, anyio.EndOfStream, anyio.BrokenResourceError, anyio.ClosedResourceError)):
        return MCPClientError(ToolErrorCategory.CONNECTION, "MCP 连接已断开")
    if isinstance(error, httpx.HTTPStatusError):
        category = ToolErrorCategory.PERMISSION if error.response.status_code in {401, 403} else ToolErrorCategory.PROTOCOL
        return MCPClientError(category, "MCP HTTP 请求被拒绝")
    for child in getattr(error, "exceptions", ()):
        classified = classify_sdk_error(child)
        if classified.category is not ToolErrorCategory.INTERNAL:
            return classified
    return MCPClientError(ToolErrorCategory.INTERNAL, "MCP 客户端执行失败")


class MCPClient:
    def __init__(self, config: MCPServerConfig, *, cwd: str | None = None, grants=None) -> None:
        self.config = config
        self.cwd = cwd
        self._session: ClientSession | None = None
        self._owner: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._ready: asyncio.Future | None = None
        self._failure: MCPClientError | None = None
        self._bridge: SDKLoopBridge | None = None
        self._delegate: MCPClient | None = None
        self.grants = grants or OAuthGrants()
        self.capabilities: frozenset[str] = frozenset()
        self._http_failed = asyncio.Event()

    async def start(self) -> None:
        if self.config.transport == "stdio" and sys.platform == "win32" and isinstance(asyncio.get_running_loop(), asyncio.SelectorEventLoop):
            if self._bridge is not None:
                raise RuntimeError("MCP Client 已启动")
            self._bridge = SDKLoopBridge()
            await self._bridge.start()
            self._delegate = MCPClient(self.config, cwd=self.cwd)
            try:
                await self._bridge.run(self._delegate.start())
                self.capabilities = self._delegate.capabilities
            except BaseException:
                await self.close()
                raise
            return
        if self._owner is not None:
            raise RuntimeError("MCP Client 已启动")
        self._ready = asyncio.get_running_loop().create_future()
        self._owner = asyncio.create_task(self._serve(), name=f"mcp:{self.config.id}")
        try:
            await asyncio.shield(self._ready)
        except asyncio.CancelledError:
            await self.close()
            raise
        except Exception as error:
            await self.close()
            raise classify_sdk_error(error) from error

    async def _serve(self) -> None:
        try:
            environment = get_default_environment()
            environment.update({name: os.environ[name] for name in self.config.env_allowlist if name in os.environ})
            # Child diagnostics never include inherited secrets in application logs.
            with open(os.devnull, "w", encoding="utf-8") as errlog:  # noqa: ASYNC230 - local null device
                async with AsyncExitStack() as stack:
                    if self.config.transport == "stdio":
                        read, write = await stack.enter_async_context(stdio_client(
                            StdioServerParameters(command=self.config.command, args=self.config.args,
                                                  env=environment, cwd=self.cwd), errlog=errlog,
                        ))
                    else:
                        # Resolve missing credentials before opening a socket.
                        await self.grants.token(self.config)
                        http = await stack.enter_async_context(http_client(
                            timeout=self.config.command_timeout_seconds,
                            auth=MCPHTTPAuth(self.config, self.grants),
                            on_error=self._http_error,
                        ))
                        read, write, _ = await stack.enter_async_context(streamable_http_client(
                            self.config.url, http_client=http,
                        ))
                    self._session = await stack.enter_async_context(ClientSession(read, write))
                    with anyio.fail_after(self.config.command_timeout_seconds):
                        initialized = await self._rpc(self._session.initialize())
                        self.capabilities = frozenset(name for name in ("tools", "resources", "prompts")
                            if getattr(initialized.capabilities, name) is not None)
                    if not self._ready.done():
                        self._ready.set_result(None)
                    while not self._stop.is_set():
                        if self._http_failed.is_set():
                            break
                        if read.statistics().open_send_streams == 0:
                            self._failure = MCPClientError(ToolErrorCategory.CONNECTION, "MCP 连接已结束")
                            break
                        try:
                            await asyncio.wait_for(self._stop.wait(), 0.1)
                        except asyncio.TimeoutError:
                            continue
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - normalize SDK ExceptionGroup
            self._failure = self._failure or classify_sdk_error(error)
            if self._ready and not self._ready.done():
                self._ready.set_exception(self._failure)
        finally:
            self._session = None

    def _http_error(self, error):
        if not self._stop.is_set():
            self._failure = classify_sdk_error(error)
            self._http_failed.set()

    async def _rpc(self, operation):
        if self.config.transport != "streamable_http":
            return await operation
        task = asyncio.ensure_future(operation)
        failed = asyncio.create_task(self._http_failed.wait())
        try:
            done, _ = await asyncio.wait({task, failed}, return_when=asyncio.FIRST_COMPLETED)
            if failed in done:
                raise self._failure
            return await task
        finally:
            for pending in (task, failed):
                if not pending.done():
                    pending.cancel()
            for pending in (task, failed):
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await pending

    def _connected_session(self) -> ClientSession:
        if self._stop.is_set() or self._session is None or self._owner is None or self._owner.done():
            raise self._failure or MCPClientError(ToolErrorCategory.CONNECTION, "MCP 连接未就绪")
        return self._session

    async def list_tools(self) -> tuple[Tool, ...]:
        return await self._list("tools")

    async def list_resources(self):
        return await self._list("resources")

    async def list_prompts(self):
        return await self._list("prompts")

    async def _list(self, kind):
        if self._bridge and self._delegate:
            return await self._bridge.run(self._delegate._list(kind))
        if kind not in self.capabilities:
            return ()
        items = []
        cursor = None
        seen = set()
        try:
            while True:
                page = await self._rpc(getattr(self._connected_session(), "list_" + kind)(cursor=cursor))
                items.extend(getattr(page, kind))
                cursor = page.nextCursor
                if not cursor:
                    return tuple(items)
                if cursor in seen:
                    raise MCPClientError(ToolErrorCategory.PROTOCOL, "MCP 工具分页游标重复")
                seen.add(cursor)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise classify_sdk_error(error) from error

    async def call_tool(self, remote_name: str, arguments: dict[str, Any]):
        return await self._request("tools", "call_tool", remote_name, arguments)

    async def read_resource(self, uri: str):
        return await self._request("resources", "read_resource", uri)

    async def get_prompt(self, name: str, arguments: dict[str, str]):
        return await self._request("prompts", "get_prompt", name, arguments)

    async def _request(self, capability, method, *args):
        if self._bridge and self._delegate:
            return await self._bridge.run(self._delegate._request(capability, method, *args))
        if capability not in self.capabilities:
            raise MCPClientError(ToolErrorCategory.PROTOCOL, "MCP Server 未协商所需能力")
        try:
            return await self._rpc(getattr(self._connected_session(), method)(*args))
        except asyncio.CancelledError:
            raise
        except Exception as error:
            classified = self._failure or classify_sdk_error(error)
            if classified.category in {ToolErrorCategory.CONNECTION, ToolErrorCategory.PERMISSION}:
                self._failure = classified
            raise classified from error

    async def close(self) -> None:
        if self._bridge and self._delegate:
            try:
                await self._bridge.run(self._delegate.close())
                self._failure = self._delegate.failure
            finally:
                await self._bridge.close()
                self._bridge = None
                self._delegate = None
            return
        if self._owner is None:
            return
        self._stop.set()
        if self._ready and not self._ready.done():
            self._owner.cancel()
        try:
            await self._owner
        except asyncio.CancelledError:
            if not self._owner.cancelled():
                raise
        finally:
            if self._ready and self._ready.done() and not self._ready.cancelled():
                self._ready.exception()

    @property
    def failure(self) -> MCPClientError | None:
        return self._delegate.failure if self._delegate else self._failure
