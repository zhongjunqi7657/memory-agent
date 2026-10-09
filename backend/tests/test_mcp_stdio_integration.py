import asyncio
import os
import sys
from pathlib import Path

import pytest

from app.audit.sink import CallbackAuditSink
from app.mcp.config import MCPConfig
from app.mcp.manager import MCPConnectionManager
from app.protocol.tools import (
    ToolCall,
    ToolErrorCategory,
    ToolExecutionContext,
    ToolStatus,
)
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry

SERVER = Path(__file__).parent / "fixtures" / "mcp_test_server.py"


def fixture_config(marker):
    return MCPConfig(allowed_commands=[sys.executable], servers=[{
        "id": "fixture", "command": sys.executable, "args": [str(SERVER), str(marker)],
        "env_allowlist": ["MCP_TEST_ALLOWED"], "command_timeout_seconds": 10,
        "default_permissions": {"capabilities": ["filesystem.read"]},
    }])


def process_alive(pid):
    if os.name == "nt":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


@pytest.mark.asyncio
async def test_stdio_handshake_permissions_errors_environment_and_cleanup(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_TEST_SECRET", "do-not-inherit")
    monkeypatch.setenv("MCP_TEST_ALLOWED", "safe")
    marker = tmp_path / "pid.txt"
    manager = MCPConnectionManager(fixture_config(marker), default_timeout_seconds=0.2)
    events = []

    async def emit(event_type, payload):
        events.append((event_type, payload))

    try:
        await manager.start()
        assert manager.status()[0]["status"] == "ready"
        pid = int(marker.read_text())
        assert process_alive(pid)
        executor = ToolExecutor(ToolRegistry(manager.snapshot()), audit_sink=CallbackAuditSink(emit))
        context = ToolExecutionContext("run", "agent", granted_permissions=frozenset({"filesystem.read"}))
        denied = await executor.execute(ToolCall("denied", "mcp.fixture.crash", {}),
                                       ToolExecutionContext("run", "agent"))
        assert denied.status is ToolStatus.DENIED
        assert process_alive(pid)
        success = await executor.execute(ToolCall("echo", "mcp__fixture__remote_echo", {"value": "ok"}), context)
        assert success.status is ToolStatus.SUCCESS
        assert success.metadata["structured_content"]["value"] == "ok"
        environment = await executor.execute(ToolCall("env", "mcp.fixture.secret_env", {}), context)
        assert environment.metadata["structured_content"] == {"secret": None, "allowed": "safe"}
        failed = await executor.execute(ToolCall("fail", "mcp.fixture.fail", {}), context)
        assert failed.error.category is ToolErrorCategory.BUSINESS
        timeout = await executor.execute(ToolCall("slow", "mcp.fixture.slow", {}), context)
        assert timeout.error.category is ToolErrorCategory.TIMEOUT
        crashed = await executor.execute(ToolCall("crash", "mcp.fixture.crash", {}), context)
        assert crashed.error.category is ToolErrorCategory.CONNECTION
        assert manager.status()[0]["status"] == "failed"
        for call_id in ["denied", "echo", "env", "fail", "slow", "crash"]:
            assert len([event for event in events if event[1]["call_id"] == call_id]) == 2
    finally:
        await manager.close()
    assert not process_alive(pid)


@pytest.mark.asyncio
async def test_manager_closes_live_child_process(tmp_path):
    marker = tmp_path / "pid.txt"
    manager = MCPConnectionManager(fixture_config(marker))
    await manager.start()
    assert manager.status()[0]["status"] == "ready"
    pid = int(marker.read_text())
    await manager.close()
    assert not process_alive(pid)


@pytest.mark.asyncio
async def test_idle_child_exit_updates_status_without_tool_call(tmp_path):
    config = fixture_config(tmp_path / "pid.txt")
    config.servers[0].args.append("idle-exit")
    manager = MCPConnectionManager(config)
    try:
        await manager.start()
        assert manager.status()[0]["status"] == "ready"
        async def until_failed():
            while manager.status()[0]["status"] != "failed":
                await asyncio.sleep(0.05)
        await asyncio.wait_for(until_failed(), 5)
        assert manager.status()[0]["error_category"] == "connection"
        assert manager.snapshot() == ()
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_handshake_timeout_reaps_child(tmp_path):
    marker = tmp_path / "pid.txt"
    config = fixture_config(marker)
    config.servers[0].args.append("hang-init")
    config.servers[0].command_timeout_seconds = 2
    manager = MCPConnectionManager(config)
    try:
        await asyncio.wait_for(manager.start(), 10)
        assert manager.status()[0]["status"] == "failed"
        assert manager.status()[0]["error_category"] == "timeout"
        assert not process_alive(int(marker.read_text()))
    finally:
        await manager.close()
