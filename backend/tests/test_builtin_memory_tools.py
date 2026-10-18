from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

import app.tools.builtin.memory as module
from app.config.business import BusinessConfig
from app.persistence.models import MemoryKind, MemoryStatus
from app.protocol.tools import ToolCall, ToolExecutionContext
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_memory_tools_keep_user_scope_limits_and_source(monkeypatch):
    user, conversation, source = uuid4(), uuid4(), uuid4()
    repository = SimpleNamespace(
        search_hybrid=AsyncMock(return_value=[]), list_timeline=AsyncMock(return_value=[])
    )
    service = SimpleNamespace(propose_update=AsyncMock(return_value=SimpleNamespace(
        id=uuid4(), status=MemoryStatus.PENDING
    )))
    monkeypatch.setattr(module, "MemoryRepository", lambda session: repository)
    monkeypatch.setattr(module, "MemoryService", lambda session, **kwargs: service)
    tools = module.create_memory_tools(
        object(), user_id=user, conversation_id=conversation, source_message_id=source,
        business=BusinessConfig(),
    )
    executor = ToolExecutor(ToolRegistry(tools))
    context = ToolExecutionContext("run", "agent", granted_permissions=module.MEMORY_PERMISSIONS)
    await executor.execute(ToolCall("search", tools[0].id, {"query": "学习", "limit": 500}), context)
    await executor.execute(ToolCall("timeline", tools[1].id, {"limit": -1}), context)
    result = await executor.execute(ToolCall("propose", tools[2].id, {"content": "喜欢阅读"}), context)
    assert repository.search_hybrid.call_args.args[0] == user
    assert repository.search_hybrid.call_args.kwargs["limit"] == 10
    assert repository.list_timeline.call_args.args == (user,)
    assert repository.list_timeline.call_args.kwargs["limit"] == 1
    assert service.propose_update.call_args.kwargs == {
        "user_id": user, "content": "喜欢阅读", "source_message_id": source,
        "conversation_id": conversation, "kind": MemoryKind.SEMANTIC,
    }
    assert result.content["status"] == MemoryStatus.PENDING.value


@pytest.mark.asyncio
async def test_request_tools_do_not_share_sessions_and_redact_query(monkeypatch):
    captured = []

    class Repository:
        def __init__(self, session):
            self.session = session

        async def search_hybrid(self, user_id, query, **kwargs):
            captured.append((self.session, user_id, query))
            return []

    monkeypatch.setattr(module, "MemoryRepository", Repository)
    for session, user in [("session-a", uuid4()), ("session-b", uuid4())]:
        tool = module.create_memory_tools(
            session, user_id=user, conversation_id=uuid4(), source_message_id=uuid4(),
            business=BusinessConfig(),
        )[0]
        await tool.execute({"query": "api_key=sk-abcdefghijklmnopqrstuvwxyz123456"},
                           ToolExecutionContext("run", "agent"))
    assert captured[0][0] == "session-a" and captured[1][0] == "session-b"
    assert captured[0][1] != captured[1][1]
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in captured[0][2]
