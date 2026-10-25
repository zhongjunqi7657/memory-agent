import asyncio
import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import pytest_asyncio

from app.mcp.config import MCPConfig
from app.mcp.manager import MCPConnectionManager
from app.protocol.tools import ToolCall, ToolExecutionContext, ToolStatus
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry

SERVER = Path(__file__).parent / "fixtures" / "mcp_http_server.py"


@pytest_asyncio.fixture
async def http_fixture(tmp_path, request):
    with socket.socket() as bound:
        bound.bind(("127.0.0.1", 0))
        port = bound.getsockname()[1]
    marker = tmp_path / "calls"
    process = await asyncio.to_thread(subprocess.Popen, [sys.executable, str(SERVER), str(port), str(marker), getattr(request, "param", "auth")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    base = f"http://127.0.0.1:{port}"
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError("HTTP fixture exited")
                try:
                    if (await client.get(base + "/health")).is_success:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.05)
            else:
                raise TimeoutError("HTTP fixture not ready")
        yield base, marker, process
    finally:
        if process.poll() is None:
            process.terminate()
        await asyncio.to_thread(process.wait, 10)


def config(base, auth):
    return MCPConfig(servers=[{"id": "web", "transport": "streamable_http", "url": base + "/mcp",
        "auth": auth, "default_permissions": {"capabilities": ["network.access"]}}])


@pytest.mark.asyncio
async def test_http_handshake_catalog_approvals_timeout_cancel_and_cleanup(http_fixture, monkeypatch):
    base, marker, _ = http_fixture
    monkeypatch.setenv("MCP_FIXTURE_TOKEN", "fixture-token")
    manager = MCPConnectionManager(config(base, {"type": "bearer", "token_env": "MCP_FIXTURE_TOKEN"}),
                                   default_timeout_seconds=0.15)
    try:
        await manager.start()
        assert manager.status()[0]["status"] == "ready"
        assert manager.status()[0]["capabilities"] == ["prompts", "resources", "tools"]
        snapshot = manager.snapshot()
        executor = ToolExecutor(ToolRegistry(snapshot))
        allowed = ToolExecutionContext("run", "agent", granted_permissions=frozenset({"network.access"}))
        denied = await executor.execute(ToolCall("deny", "mcp.web.echo", {"value": "no"}), ToolExecutionContext("run", "agent"))
        assert denied.status is ToolStatus.DENIED
        assert not marker.exists()
        for adapter in snapshot:
            if getattr(adapter, "operation", None):
                rejected = await executor.execute(ToolCall("no", adapter.id, {} if adapter.operation == "resource" else {"name": "Ada"}),
                                                   ToolExecutionContext("run", "agent"))
                assert rejected.status is ToolStatus.DENIED
                result = await executor.execute(ToolCall("ok", adapter.id, {} if adapter.operation == "resource" else {"name": "Ada"}), allowed)
                assert result.status is ToolStatus.SUCCESS
                assert result.metadata["untrusted_mcp_content"]
                bindings = []

                async def reject(tool, call, context, bindings=bindings):
                    bindings.append(call.arguments)
                    return False

                rejected = await executor.execute(ToolCall("reject", adapter.id, {} if adapter.operation == "resource" else {"name": "Ada"}),
                    ToolExecutionContext("run", "agent", authorize=reject))
                assert rejected.status is ToolStatus.DENIED
                assert bindings[0]["_mcp_target"] == adapter.target
        result = await executor.execute(ToolCall("echo", "mcp.web.echo", {"value": "ok"}), allowed)
        assert result.status is ToolStatus.SUCCESS
        assert marker.read_text() == "1"
        timed = await executor.execute(ToolCall("slow", "mcp.web.slow", {}), allowed)
        assert timed.error.category.value == "timeout"
        cancellation = asyncio.Event()
        task = asyncio.create_task(executor.execute(ToolCall("cancel", "mcp.web.slow", {}),
            ToolExecutionContext("run", "agent", granted_permissions=allowed.granted_permissions, cancellation=cancellation)))
        await asyncio.sleep(0.03)
        cancellation.set()
        assert (await task).status is ToolStatus.CANCELLED
        await manager.reconnect("web")
        stale = await executor.execute(ToolCall("stale", "mcp.web.echo", {"value": "old"}), allowed)
        assert stale.status is ToolStatus.FAILED
        assert marker.read_text() == "1"
    finally:
        await manager.close()
    assert manager.snapshot() == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("http_fixture", ["none"], indirect=True)
async def test_anonymous_http_and_disconnect_no_automatic_replay(http_fixture):
    base, marker, process = http_fixture
    manager = MCPConnectionManager(config(base, {"type": "none"}))
    try:
        await manager.start()
        assert manager.status()[0]["status"] == "ready"
        context = ToolExecutionContext("run", "agent", granted_permissions=frozenset({"network.access"}))
        executor = ToolExecutor(ToolRegistry(manager.snapshot()))
        assert (await executor.execute(ToolCall("one", "mcp.web.echo", {"value": "ok"}), context)).status is ToolStatus.SUCCESS
        process.terminate()
        await asyncio.to_thread(process.wait, 10)
        result = await executor.execute(ToolCall("lost", "mcp.web.echo", {"value": "again"}), context)
        assert result.status is ToolStatus.FAILED
        assert result.error.category.value == "connection"
        assert manager.status()[0]["status"] == "failed"
        assert manager.snapshot() == ()
        assert marker.read_text() == "1"
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_credentials_revoked_during_connection_fail_closed_without_retry(http_fixture, monkeypatch):
    base, marker, _ = http_fixture
    monkeypatch.setenv("MCP_FIXTURE_TOKEN", "fixture-token")
    manager = MCPConnectionManager(config(base, {"type": "bearer", "token_env": "MCP_FIXTURE_TOKEN"}))
    try:
        await manager.start()
        executor = ToolExecutor(ToolRegistry(manager.snapshot()))
        monkeypatch.setenv("MCP_FIXTURE_TOKEN", "revoked-token")
        context = ToolExecutionContext("run", "agent", granted_permissions=frozenset({"network.access"}))
        result = await executor.execute(ToolCall("revoked", "mcp.web.echo", {"value": "no"}), context)
        assert result.error.category.value == "permission"
        assert manager.snapshot() == ()
        assert not marker.exists()
    finally:
        await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("auth", [{"type": "bearer", "token_env": "MISSING_MCP_TEST_TOKEN"}, {"type": "none"}])
async def test_missing_or_rejected_credentials_never_publish_tools(http_fixture, auth):
    base, marker, _ = http_fixture
    manager = MCPConnectionManager(config(base, auth))
    try:
        await manager.start()
        assert manager.status()[0]["status"] == "failed"
        assert manager.status()[0]["error_category"] == "permission"
        assert not manager.snapshot() and not marker.exists()
        assert "never-log-this" not in json.dumps(manager.status())
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_real_oauth_pkce_refresh_state_single_use_and_local_revoke(http_fixture):
    base, _, _ = http_fixture
    manager = MCPConnectionManager(config(base, {"type": "oauth", "oauth": {
        "client_id": "fixture-client", "authorization_url": base + "/authorize", "token_url": base + "/token",
        "redirect_uri": "http://127.0.0.1:5173/"}}))
    server = manager.config.servers[0]
    try:
        url = manager.grants.begin(server)
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
            response = await client.get(url)
        params = parse_qs(urlsplit(response.headers["location"]).query)
        state = params["state"][0]
        with pytest.raises(ValueError):
            await manager.grants.complete("wrong-state", "fixture-code", server.auth.oauth.redirect_uri, manager.config.servers)
        await manager.grants.complete(state, "fixture-code", server.auth.oauth.redirect_uri, manager.config.servers)
        with pytest.raises(ValueError):
            await manager.grants.complete(state, "fixture-code", server.auth.oauth.redirect_uri, manager.config.servers)
        await manager.start()
        assert manager.status()[0]["status"] == "ready"
        manager.grants._tokens["web"]["expires_at"] = 0
        assert await manager.grants.token(server) == "fixture-token"
        await manager.disconnect("web")
        manager.grants.revoke("web")
        await manager.reconnect("web")
        assert manager.status()[0]["error_category"] == "permission"
        assert "fixture-token" not in json.dumps(manager.connections())
    finally:
        await manager.close()
