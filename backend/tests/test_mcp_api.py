import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

import app.api.mcp as mcp_api
from app.api.mcp import call_tool
from app.config.business import BusinessConfig
from app.config.settings import Settings, get_settings
from app.main import app
from app.mcp.protocol import MCPToolSpec
from app.mcp.transport import MCPCallRequest
from app.persistence.db import get_session
from app.persistence.models import MessageRole, RunStatus
from app.protocol.tools import ToolResult
from app.tools.builtin.memory import MemoryTool
from app.tools.registry import ToolRegistry

USER_ID = UUID("00000000-0000-0000-0000-000000000001")
OTHER_USER_ID = UUID("00000000-0000-0000-0000-000000000010")
CONVERSATION_ID = UUID("00000000-0000-0000-0000-000000000002")
OTHER_CONVERSATION_ID = UUID("00000000-0000-0000-0000-000000000020")
MESSAGE_ID = UUID("00000000-0000-0000-0000-000000000003")
RUN_ID = UUID("00000000-0000-0000-0000-000000000005")


class FakeSession:
    def __init__(self) -> None:
        self.commit_count = 0

    async def commit(self) -> None:
        self.commit_count += 1


class StubConversationRepository:
    runs: list[SimpleNamespace]
    events: list[tuple[UUID, int, str, dict]]
    finished: list[tuple[SimpleNamespace, RunStatus, str | None]]

    def __init__(self, _session: FakeSession) -> None:
        self.session = _session

    async def get_user(self, external_key: str):
        users = {
            "alice": SimpleNamespace(id=USER_ID),
            "bob": SimpleNamespace(id=OTHER_USER_ID),
        }
        return users.get(external_key)

    async def get_conversation(self, conversation_id: UUID, user_id: UUID):
        owned = {
            (CONVERSATION_ID, USER_ID),
            (OTHER_CONVERSATION_ID, OTHER_USER_ID),
        }
        if (conversation_id, user_id) in owned:
            return SimpleNamespace(id=conversation_id)
        return None

    async def get_message(self, message_id: UUID, conversation_id: UUID):
        if message_id == MESSAGE_ID and conversation_id == CONVERSATION_ID:
            return SimpleNamespace(id=message_id, role=MessageRole.USER)
        return None

    async def create_run(self, conversation_id: UUID):
        run = SimpleNamespace(id=RUN_ID, conversation_id=conversation_id)
        self.runs.append(run)
        return run

    async def add_run_event(
        self,
        run_id: UUID,
        *,
        sequence: int,
        event_type: str,
        payload: dict,
    ):
        self.events.append((run_id, sequence, event_type, payload))
        return SimpleNamespace(run_id=run_id, sequence=sequence)

    async def finish_run(
        self,
        run: SimpleNamespace,
        *,
        status: RunStatus,
        error_message: str | None = None,
    ) -> None:
        self.finished.append((run, status, error_message))


def stub_registry():
    async def execute(arguments):
        return ToolResult.success('{"items": []}')
    return ToolRegistry([MemoryTool(
        "builtin.memory.search", "builtin__memory__search", "Search memory",
        {"type": "object"}, frozenset({"memory.read"}), 1, execute,
    )])


def _session_override(session: FakeSession):
    async def dependency() -> AsyncIterator[FakeSession]:
        yield session

    return dependency


def _install_api_stubs(monkeypatch, session: FakeSession) -> None:
    StubConversationRepository.runs = []
    StubConversationRepository.events = []
    StubConversationRepository.finished = []
    monkeypatch.setattr(
        mcp_api, "ConversationRepository", StubConversationRepository
    )
    monkeypatch.setattr(
        mcp_api,
        "build_registry",
        lambda _session, **kwargs: stub_registry(),
    )
    app.dependency_overrides[get_session] = _session_override(session)


def test_mcp_routes_require_shared_password(monkeypatch) -> None:
    session = FakeSession()
    _install_api_stubs(monkeypatch, session)
    app.dependency_overrides[get_settings] = lambda: Settings(
        dashscope_api_key="", demo_shared_password="mcp-pass"
    )
    try:
        client = TestClient(app)
        response = client.get("/v1/mcp/tools")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Basic"


def test_mcp_discovery_returns_configured_whitelist(monkeypatch) -> None:
    session = FakeSession()
    _install_api_stubs(monkeypatch, session)
    spec = MCPToolSpec(
        name="search_memory",
        description="Search memory",
        input_schema={"type": "object"},
        output_schema={"type": "string"},
        permission="memory.read",
        timeout_seconds=8,
    )
    monkeypatch.setattr(mcp_api, "get_business_config", lambda: BusinessConfig())
    monkeypatch.setattr(mcp_api, "discovery_specs", lambda *_args, **_kwargs: [spec])
    try:
        response = TestClient(app).get("/v1/mcp/tools")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["tools"] == [
        {
            "name": "search_memory",
            "description": "Search memory",
            "inputSchema": {"type": "object"},
            "outputSchema": {"type": "string"},
            "permission": "memory.read",
            "timeoutSeconds": 8.0,
        }
    ]


def test_mcp_call_rejects_cross_user_conversation(monkeypatch) -> None:
    session = FakeSession()
    _install_api_stubs(monkeypatch, session)
    try:
        response = TestClient(app).post(
            "/v1/mcp/call",
            json={
                "user_key": "alice",
                "tool": "search_memory",
                "conversation_id": str(OTHER_CONVERSATION_ID),
                "arguments": {"query": "secret"},
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json()["detail"] == "会话不存在"
    assert StubConversationRepository.runs == []


def test_mcp_write_requires_source_message(monkeypatch) -> None:
    session = FakeSession()
    _install_api_stubs(monkeypatch, session)
    try:
        response = TestClient(app).post(
            "/v1/mcp/call",
            json={
                "user_key": "alice",
                "tool": "propose_memory_update",
                "conversation_id": str(CONVERSATION_ID),
                "arguments": {"content": "用户喜欢阅读"},
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json()["detail"] == "写入工具需要来源消息"
    assert StubConversationRepository.runs == []


def test_mcp_write_rejects_source_message_outside_conversation(monkeypatch) -> None:
    session = FakeSession()
    _install_api_stubs(monkeypatch, session)
    try:
        response = TestClient(app).post(
            "/v1/mcp/call",
            json={
                "user_key": "alice",
                "tool": "propose_memory_update",
                "conversation_id": str(CONVERSATION_ID),
                "source_message_id": str(OTHER_CONVERSATION_ID),
                "arguments": {"content": "用户喜欢阅读"},
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json()["detail"] == "来源消息不存在或不可用"
    assert StubConversationRepository.runs == []


def test_mcp_call_returns_run_id_and_persists_audit_events(monkeypatch) -> None:
    session = FakeSession()
    _install_api_stubs(monkeypatch, session)
    try:
        response = TestClient(app).post(
            "/v1/mcp/call",
            json={
                "user_key": "alice",
                "tool": "search_memory",
                "conversation_id": str(CONVERSATION_ID),
                "arguments": {"query": "学习"},
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "run_id": str(RUN_ID),
        "tool": "search_memory",
        "ok": True,
        "content": '{"items": []}',
        "error": None,
    }
    assert [event[2] for event in StubConversationRepository.events] == [
        "tool.execution.started",
        "tool.execution.completed",
        "mcp.call.completed",
    ]
    assert StubConversationRepository.finished[0][1] is RunStatus.COMPLETED
    assert session.commit_count == 1


@pytest.mark.parametrize("tool", ["builtin.memory.propose_update", "builtin__memory__propose_update"])
def test_write_aliases_cannot_bypass_source_validation(monkeypatch, tool):
    session = FakeSession()
    _install_api_stubs(monkeypatch, session)
    try:
        response = TestClient(app).post("/v1/mcp/call", json={
            "user_key": "alice", "tool": tool, "conversation_id": str(CONVERSATION_ID),
            "arguments": {"content": "阅读"},
        })
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 400
    assert not StubConversationRepository.runs


@pytest.mark.asyncio
async def test_mcp_cancellation_finishes_run_before_propagating(monkeypatch) -> None:
    session = FakeSession()
    _install_api_stubs(monkeypatch, session)

    class CancelledExecutor:
        def __init__(self, registry):
            pass

        async def execute(self, *_args, **_kwargs):
            raise asyncio.CancelledError

    monkeypatch.setattr(
        mcp_api, "ToolExecutor", CancelledExecutor
    )
    request = MCPCallRequest(
        user_key="alice",
        tool="search_memory",
        conversation_id=CONVERSATION_ID,
    )

    with pytest.raises(asyncio.CancelledError):
        await call_tool(request, session)

    assert StubConversationRepository.finished[0][1] is RunStatus.FAILED
    assert StubConversationRepository.finished[0][2] == "工具调用已取消"
    assert session.commit_count == 1
