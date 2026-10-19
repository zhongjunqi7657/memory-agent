"""Thin official MCP SDK stdio client with task-owned session lifetime."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import AsyncExitStack
from typing import Any

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client
from mcp.shared.exceptions import McpError
from mcp.types import CONNECTION_CLOSED, Tool
from pydantic import ValidationError

from app.mcp.config import MCPServerConfig
from app.mcp.runtime import SDKLoopBridge
from app.protocol.tools import ToolErrorCategory


class MCPClientError(RuntimeError):
    def __init__(self, category: ToolErrorCategory, message: str) -> None:
        super().__init__(message)
        self.category = category


def classify_sdk_error(error: BaseException) -> MCPClientError:
    if isinstance(error, MCPClientError):
        return error
    if isinstance(error, McpError):
        if error.error.code == CONNECTION_CLOSED:
            return MCPClientError(ToolErrorCategory.CONNECTION, "MCP 连接已断开")
        return MCPClientError(ToolErrorCategory.PROTOCOL, "MCP 协议请求失败")
    if isinstance(error, (ValidationError, json.JSONDecodeError)):
        return MCPClientError(ToolErrorCategory.PROTOCOL, "MCP 协议响应无效")
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        return MCPClientError(ToolErrorCategory.TIMEOUT, "MCP 连接或握手超时")
    if isinstance(error, (OSError, anyio.EndOfStream, anyio.BrokenResourceError, anyio.ClosedResourceError)):
        return MCPClientError(ToolErrorCategory.CONNECTION, "MCP 连接已断开")
    for child in getattr(error, "exceptions", ()):
        classified = classify_sdk_error(child)
        if classified.category is not ToolErrorCategory.INTERNAL:
            return classified
    return MCPClientError(ToolErrorCategory.INTERNAL, "MCP 客户端执行失败")


class MCPClient:
    def __init__(self, config: MCPServerConfig, *, cwd: str | None = None) -> None:
        self.config = config
        self.cwd = cwd
        self._session: ClientSession | None = None
        self._owner: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._ready: asyncio.Future | None = None
        self._failure: MCPClientError | None = None
        self._bridge: SDKLoopBridge | None = None
        self._delegate: MCPClient | None = None

    async def start(self) -> None:
        if sys.platform == "win32" and isinstance(asyncio.get_running_loop(), asyncio.SelectorEventLoop):
            if self._bridge is not None:
                raise RuntimeError("MCP Client 已启动")
            self._bridge = SDKLoopBridge()
            await self._bridge.start()
            self._delegate = MCPClient(self.config, cwd=self.cwd)
            try:
                await self._bridge.run(self._delegate.start())
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
                    read, write = await stack.enter_async_context(stdio_client(
                        StdioServerParameters(command=self.config.command, args=self.config.args,
                                              env=environment, cwd=self.cwd), errlog=errlog,
                    ))
                    self._session = await stack.enter_async_context(ClientSession(read, write))
                    with anyio.fail_after(self.config.command_timeout_seconds):
                        await self._session.initialize()
                    if not self._ready.done():
                        self._ready.set_result(None)
                    while not self._stop.is_set():
                        if read.statistics().open_send_streams == 0:
                            self._failure = MCPClientError(ToolErrorCategory.CONNECTION, "MCP 子进程已退出")
                            break
                        try:
                            await asyncio.wait_for(self._stop.wait(), 0.1)
                        except asyncio.TimeoutError:
                            continue
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - normalize SDK ExceptionGroup
            self._failure = classify_sdk_error(error)
            if self._ready and not self._ready.done():
                self._ready.set_exception(self._failure)
        finally:
            self._session = None

    def _connected_session(self) -> ClientSession:
        if self._session is None or self._owner is None or self._owner.done():
            raise self._failure or MCPClientError(ToolErrorCategory.CONNECTION, "MCP 连接未就绪")
        return self._session

    async def list_tools(self) -> tuple[Tool, ...]:
        if self._bridge and self._delegate:
            return await self._bridge.run(self._delegate.list_tools())
        tools = []
        cursor = None
        seen = set()
        try:
            while True:
                page = await self._connected_session().list_tools(cursor=cursor)
                tools.extend(page.tools)
                cursor = page.nextCursor
                if not cursor:
                    return tuple(tools)
                if cursor in seen:
                    raise MCPClientError(ToolErrorCategory.PROTOCOL, "MCP 工具分页游标重复")
                seen.add(cursor)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise classify_sdk_error(error) from error

    async def call_tool(self, remote_name: str, arguments: dict[str, Any]):
        if self._bridge and self._delegate:
            return await self._bridge.run(self._delegate.call_tool(remote_name, arguments))
        try:
            return await self._connected_session().call_tool(remote_name, arguments)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            classified = classify_sdk_error(error)
            if classified.category is ToolErrorCategory.CONNECTION:
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
