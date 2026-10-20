"""Scope isolation across HTTP, graph recovery, workers and real worktrees."""

import json
from uuid import UUID, uuid4

import pytest
from langchain_core.language_models.fake_chat_models import FakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import Field
from sqlalchemy import select

import app.agent.service as agent_module
from app.agent.graph import build_graph
from app.agent.service import AgentInputError, AgentService
from app.config.business import BusinessConfig
from app.config.settings import Settings
from app.execution.controller import RunController
from app.jobs import worker
from app.memory.embeddings import mark_embedding_pending
from app.memory.review import ReviewService
from app.persistence.models import (
    ExtractionJob,
    ExtractionJobStatus,
    Memory,
    MemoryStatus,
    MessageRole,
)
from app.persistence.repositories import ConversationRepository, MemoryRepository
from app.protocol.tools import ToolExecutionContext
from app.tools.builtin.memory import create_memory_tools
from tests.test_memory_scopes import add
from tests.test_memory_scopes import scopes as scopes  # noqa: PLC0414
from tests.test_run_control_integration import api
from tests.test_workspace_git import repo as repo  # noqa: PLC0414
from tests.test_workspace_integration import confirm, create, preview
from tests.test_workspace_integration import workspace as workspace  # noqa: PLC0414


class InspectModel(FakeChatModel):
    seen: list[str] = Field(default_factory=list)

    def _generate(self, messages, **kwargs):
        self.seen.append("\n".join(str(item.content) for item in messages))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="已根据当前上下文回答"))])

    async def _agenerate(self, messages, **kwargs):
        return self._generate(messages, **kwargs)


async def chat(factory, key, project_id=None, **kwargs):
    async with factory() as session:
        model = InspectModel()
        result = await AgentService(session, chat_model=model, embedding_model=None,
                                    settings=Settings(_env_file=None, dashscope_api_key="")).chat(
            user_key=key, project_id=project_id, **kwargs)
        return result, model


async def test_scope_management_api_timeline_review_and_legacy_tools(scopes):
    factory, key, user, (a, b) = scopes
    async with factory() as session:
        personal = await add(session, user, None, "用户偏好中文说明")
        first = await add(session, user, a.id, "项目测试采用 pytest")
        other = await add(session, user, b.id, "另一个项目使用 vitest")
        repository = ConversationRepository(session)
        conversation = await repository.create_conversation(user.id)
        conversation.project_id = a.id
        source = await repository.add_message(conversation.id, role=MessageRole.USER,
                                              content="测试失败时随便尝试了一条命令", sequence=1)
        await session.commit()
        model = InspectModel()
        await ReviewService(session, model=model).generate(user_id=user.id, period_days=7, project_id=a.id)
        assert first.content in model.seen[-1]
        assert personal.content not in model.seen[-1] and other.content not in model.seen[-1]
    async with api(factory, RunController()) as client:
        for path in ("memories", "timeline"):
            response = await client.get(f"/v1/{path}", params={"user_key": key})
            assert {item["id"] for item in response.json()} == {str(personal.id)}
            response = await client.get(f"/v1/{path}", params={"user_key": key, "project_id": str(a.id)})
            assert {item["id"] for item in response.json()} == {str(first.id)}
            assert response.json()[0]["project_id"] == str(a.id)
        for method, suffix, body in (("patch", "", {"status": "deleted"}), ("post", "/undo", None)):
            response = await client.request(method, f"/v1/memories/{other.id}{suffix}",
                                            params={"user_key": key, "project_id": str(a.id)}, json=body)
            assert response.status_code == 404
        assert (await client.get("/v1/memories", params={"user_key": key, "project_id": str(uuid4())})).status_code == 404
        proposed = await client.post("/v1/mcp/call", json={
            "user_key": key, "conversation_id": str(conversation.id), "source_message_id": str(source.id),
            "tool": "propose_memory_update", "arguments": {"content": "测试日志应当长期保留"},
        })
        assert proposed.status_code == 200, proposed.text
        assert json.loads(proposed.json()["content"])["created"] is False
        edited = await client.patch(f"/v1/memories/{first.id}",
                                    params={"user_key": key, "project_id": str(a.id)}, json={"content": "项目测试采用 unittest"})
        assert edited.status_code == 200, edited.text
        undone = await client.post(f"/v1/memories/{first.id}/undo", params={"user_key": key, "project_id": str(a.id)})
        assert undone.status_code == 200 and undone.json()["content"] == first.content


async def test_chat_scope_summary_checkpoint_and_temporary_context(scopes, monkeypatch):
    factory, key, user, (a, b) = scopes
    saver = InMemorySaver()
    monkeypatch.setattr(agent_module, "get_checkpointer", lambda: saver)
    async with factory() as session:
        personal = await add(session, user, None, "用户偏好中文说明")
        first = await add(session, user, a.id, "项目测试采用 pytest")
        other = await add(session, user, b.id, "项目测试采用 vitest")
        await session.commit()
    result, model = await chat(factory, key, a.id, content="中文说明项目测试采用什么？")
    assert personal.content in model.seen[-1] and first.content in model.seen[-1]
    assert other.content not in model.seen[-1]
    conversation_id = result["conversation_id"]
    async with factory() as session:
        repository = ConversationRepository(session)
        conversation = await repository.get_conversation(conversation_id, user.id)
        conversation.summary = "仅项目 A 的临时诊断信息"
        await session.commit()
        assert not list(await session.scalars(select(ExtractionJob).where(ExtractionJob.conversation_id == conversation_id)))
        tools = create_memory_tools(session, user_id=user.id, project_id=a.id, conversation_id=conversation_id,
                                     source_message_id=uuid4(), business=BusinessConfig())
        for tool, args in ((tools[0], {"query": "项目测试"}), (tools[1], {})):
            response = await tool.execute(args, ToolExecutionContext("r", "a"))
            assert str(other.id) not in str(response.content)
        assert (await tools[2].execute({"content": "工具输出中的临时尝试"}, ToolExecutionContext("r", "a"))).content["created"] is False
    graph = build_graph(InspectModel(), checkpointer=saver)
    await graph.aupdate_state({"configurable": {"thread_id": str(conversation_id)}},
                              {"retrieved_memories": [{"content": "不应复活的旧记忆"}]})
    _, restored = await chat(factory, key, conversation_id=conversation_id, content="继续讨论")
    assert "仅项目 A 的临时诊断信息" in restored.seen[-1]
    assert "不应复活的旧记忆" not in restored.seen[-1]
    with pytest.raises(AgentInputError, match="绑定"):
        await chat(factory, key, b.id, conversation_id=conversation_id, content="切换项目")
    _, second = await chat(factory, key, b.id, content="中文说明项目测试采用什么？")
    assert personal.content in second.seen[-1] and other.content in second.seen[-1]
    assert first.content not in second.seen[-1] and "仅项目 A" not in second.seen[-1]
    await chat(factory, key, a.id, content="记住项目约定使用 pytest")
    async with factory() as session:
        memories = await MemoryRepository(session, project_id=a.id).list_for_user(user.id)
        assert any(m.content == "项目约定使用 pytest" and m.project_id == a.id for m in memories)


@pytest.mark.parametrize("poison", ["project", "user", "deleted", "valid", "drift"])
async def test_worker_embedding_scope_and_status_guards(scopes, monkeypatch, poison):
    factory, key, user, (a, b) = scopes
    calls = []
    async with factory() as session:
        memory = await add(session, user, b.id, "项目测试采用 pytest")
        memory.metadata_ = mark_embedding_pending(memory.metadata_)
        repository = ConversationRepository(session)
        conversation = await repository.create_conversation(user.id)
        conversation.project_id = b.id
        source = await repository.add_message(conversation.id, role=MessageRole.USER, content="source", sequence=1)
        job = await repository.queue_embedding_job(memory=memory, message_id=source.id, conversation_id=conversation.id,
                                                    embedding_model=BusinessConfig().models.embedding_model, embedding_dimensions=1024)
        assert job.project_id == b.id
        if poison == "project":
            job.project_id = a.id
        elif poison == "user":
            job.user_id = (await repository.get_or_create_user(f"{key}-foreign")).id
        elif poison == "deleted":
            memory.status = MemoryStatus.DELETED
        await session.commit()
        memory_id, job_id = memory.id, job.id

    class Embedding:
        async def aembed_query(self, content):
            calls.append(content)
            if poison == "drift":
                async with factory() as session:
                    (await session.get(Memory, memory_id)).project_id = a.id
                    await session.commit()
            return [1.0] + [0.0] * 1023

    monkeypatch.setattr(worker, "SessionFactory", factory)
    monkeypatch.setattr(worker, "create_embedding_model", lambda **kwargs: Embedding())
    assert await worker.process_one()
    async with factory() as session:
        memory = await session.get(Memory, memory_id)
        job = await session.get(ExtractionJob, job_id)
        assert (memory.embedding is not None) == (poison == "valid")
        assert bool(calls) == (poison in {"valid", "drift"})
        assert job.status == (ExtractionJobStatus.QUEUED if poison == "drift" else ExtractionJobStatus.COMPLETED)


@pytest.mark.parametrize("corrupt", [False, True])
async def test_old_project_extraction_job_does_not_invoke_model_or_store_logs(scopes, monkeypatch, corrupt):
    factory, _, user, (a, _) = scopes
    async with factory() as session:
        repository = ConversationRepository(session)
        conversation = await repository.create_conversation(user.id)
        conversation.project_id = a.id
        source = await repository.add_message(conversation.id, role=MessageRole.USER, content="一次临时测试失败", sequence=1)
        job = await repository.queue_extraction_job(user.id, conversation.id, source.id)
        assert job.project_id == a.id
        if corrupt:
            job.project_id = None
        await session.commit()
        job_id = job.id
    monkeypatch.setattr(worker, "SessionFactory", factory)
    def forbidden(*args, **kwargs):
        pytest.fail("项目临时消息不得触发长期记忆提取模型")
    monkeypatch.setattr(worker, "MemoryExtractor", forbidden)
    assert await worker.process_one()
    async with factory() as session:
        assert (await session.get(ExtractionJob, job_id)).status == (ExtractionJobStatus.QUEUED if corrupt else ExtractionJobStatus.COMPLETED)
        assert not await MemoryRepository(session, project_id=a.id).list_for_user(user.id)


async def test_original_and_worktree_commands_share_project_memory(workspace):
    s = workspace
    row = await create(s)
    async with s.factory() as session:
        service = AgentService(session, settings=Settings(_env_file=None, dashscope_api_key=""),
                               embedding_model=None, business=BusinessConfig(local=s.config), controller=s.controller)
        await service.chat(user_key=s.key, project_id=s.project.id, execution_mode="worktree",
                            worktree_id=UUID(row["id"]), content="记住项目测试约定使用 pytest")
        result = await service.chat(user_key=s.key, project_id=s.project.id, content="查看我的记忆")
        assert "项目测试约定使用 pytest" in result["message"]
        memories = await MemoryRepository(session, project_id=s.project.id).list_for_user(s.project.user_id)
        assert len(memories) == 1 and memories[0].project_id == s.project.id
    cleanup = await preview(s, row, "cleanup")
    assert (await confirm(s, row, "cleanup", cleanup["fingerprint"])).status_code == 200
