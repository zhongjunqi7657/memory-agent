
import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from fastapi import FastAPI
from mcp.types import Tool

from app import main
from app.config.business import BusinessConfig
from app.mcp.config import MCPConfig, MCPServerConfig
from app.mcp.manager import MCPConnectionManager


@pytest.mark.asyncio
async def test_failed_server_does_not_block_ready_server_or_publish_partial_tools():
    clients = []

    class Client:
        failure = None
        closed = False

        def __init__(self, config, **kwargs):
            self.config = config
            clients.append(self)

        async def start(self):
            pass

        async def list_tools(self):
            if self.config.id == "bad":
                return [Tool(name="read", inputSchema={"type": "object"}),
                        Tool(name="broken", inputSchema={"type": "invalid"})]
            return [Tool(name="read", inputSchema={"type": "object"})]

        async def close(self):
            self.closed = True

    config = MCPConfig(servers=[{"id": name, "command": "python",
        "default_permissions": {"capabilities": ["filesystem.read"]}} for name in ["bad", "good"]])
    manager = MCPConnectionManager(config, client_factory=Client)
    await manager.start()
    assert [item["status"] for item in manager.status()] == ["failed", "ready"]
    assert [tool.id for tool in manager.snapshot()] == ["mcp.good.read"]
    assert clients[0].closed
    await manager.close()
    assert all(client.closed for client in clients)
    assert manager.snapshot() == ()


@pytest.mark.asyncio
async def test_reconnecting_one_server_never_starts_other_saved_or_disconnected_servers():
    started = []

    class Client:
        failure = None

        def __init__(self, config, **kwargs):
            self.config = config

        async def start(self):
            started.append(self.config.id)

        async def list_tools(self):
            return []

        async def close(self):
            pass

    manager = MCPConnectionManager(MCPConfig(), client_factory=Client)
    try:
        for name in ("a", "b"):
            await manager.configure(MCPServerConfig(id=name, command="python"))
        await manager.reconnect("b")
        assert started == ["b"]
        assert manager.status()[0]["status"] == "stopped"
        await manager.reconnect("a")
        await manager.stop("a")
        await manager.reconnect("b")
        assert started == ["b", "a", "b"]
        assert manager.status()[0]["status"] == "stopped"
    finally:
        await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("allowed_tools,expected_status,expected_names", [
    (["get_datetime"], "ready", ["get_datetime"]),
    ([], "ready", []),
    (["missing"], "failed", []),
])
async def test_server_tool_allowlist_limits_published_tools(allowed_tools, expected_status, expected_names):
    class Client:
        failure = None

        def __init__(self, config, **kwargs):
            self.config = config

        async def start(self):
            pass

        async def list_tools(self):
            return [Tool(name="get_datetime", inputSchema={"type": "object"}),
                    Tool(name="unapproved_write", inputSchema={"type": "invalid"})]

        async def close(self):
            pass

    manager = MCPConnectionManager(MCPConfig(servers=[{"id": "local-mcp", "command": "npx",
        "allowed_tools": allowed_tools, "default_permissions": {"capabilities": []}}]),
        client_factory=Client)
    try:
        await manager.start()
        assert manager.status()[0]["status"] == expected_status
        assert [tool.remote_name for tool in manager.snapshot()] == expected_names
    finally:
        await manager.close()
    assert manager.snapshot() == ()


@pytest.mark.asyncio
async def test_application_lifecycle_orders_checkpoint_and_mcp(monkeypatch):
    events = []

    @asynccontextmanager
    async def lease():
        events.append("runtime.lock")
        yield
        events.append("runtime.unlock")

    @asynccontextmanager
    async def checkpoint(settings):
        events.append("checkpoint.start")
        yield
        events.append("checkpoint.close")

    class Manager:
        def __init__(self, *args, **kwargs):
            pass

        async def start(self):
            events.append("mcp.start")

        async def close(self):
            events.append("mcp.close")

    monkeypatch.setattr(main, "checkpoint_lifespan", checkpoint)
    monkeypatch.setattr(main, "runtime_lease", lease)
    monkeypatch.setattr(main, "MCPConnectionManager", Manager)
    monkeypatch.setattr(main, "get_business_config", BusinessConfig)
    async def recover():
        events.append("runs.recover")

    monkeypatch.setattr(main, "recover_runs", recover)
    app = FastAPI()
    async with main.lifespan(app):
        assert isinstance(app.state.mcp_manager, Manager)
        ready = asyncio.Event()

        async def pending_run():
            run_id = uuid4()
            app.state.run_controller.register(run_id, uuid4(), uuid4(), None)
            ready.set()
            try:
                await asyncio.Event().wait()
            finally:
                app.state.run_controller.release(run_id)
                events.append("run.closed")

        task = asyncio.create_task(pending_run())
        await ready.wait()
        events.append("application.ready")
    assert task.cancelled()
    assert events == ["runtime.lock", "checkpoint.start", "runs.recover", "mcp.start", "application.ready", "run.closed", "mcp.close", "checkpoint.close", "runtime.unlock"]


@pytest.mark.asyncio
@pytest.mark.parametrize("names", [("a.b", "a_b"), ("a", "a")])
async def test_manager_rejects_remote_mapping_collisions(names):
    class Client:
        failure = None

        def __init__(self, config, **kwargs):
            self.config = config

        async def start(self):
            pass

        async def list_tools(self):
            return [Tool(name=name, inputSchema={"type": "object"}) for name in names]

        async def close(self):
            pass

    manager = MCPConnectionManager(MCPConfig(servers=[{"id": "a", "command": "python",
        "default_permissions": {"capabilities": []}}]), client_factory=Client)
    await manager.start()
    assert manager.status()[0]["status"] == "failed"
    assert manager.snapshot() == ()
    await manager.close()
