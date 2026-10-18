"""Real PostgreSQL verification of live task visibility and cancellation boundaries."""

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.language_models.fake_chat_models import FakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from sqlalchemy import delete, func, select

import app.agent.service as agent_module
import app.api.chat as chat_api
from app.agent.service import AgentInputError, AgentService
from app.config.settings import Settings
from app.execution.controller import RunController
from app.execution.lease import runtime_lease
from app.execution.lifecycle import interrupt_stale_runs
from app.main import app
from app.persistence.db import get_session
from app.persistence.models import (
    Conversation,
    ExtractionJob,
    Message,
    MessageRole,
    Run,
    RunEvent,
    RunStatus,
    User,
)
from app.persistence.repositories import ConversationRepository
from app.tools.builtin.memory import MemoryTool
from app.tools.registry import ToolRegistry
from app.workspace.paths import canonical_directory


class GateModel(FakeChatModel):
    started: object
    release: object

    async def _agenerate(self, messages, **kwargs):
        self.started.set()
        await self.release.wait()
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="done"))])


def service(session, controller, model=None):
    return AgentService(session, controller=controller, chat_model=model, embedding_model=None,
                        settings=Settings(_env_file=None, dashscope_api_key=""))


@asynccontextmanager
async def api(factory, controller):
    async def sessions():
        async with factory() as session:
            yield session

    previous = getattr(app.state, "run_controller", None)
    app.state.run_controller = controller
    app.dependency_overrides[get_session] = sessions
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        if previous is None:
            del app.state.run_controller
        else:
            app.state.run_controller = previous


async def remove_users(factory, *keys):
    async with factory() as session:
        await session.execute(delete(User).where(User.external_key.in_(keys)))
        await session.commit()


@pytest.mark.asyncio
async def test_live_binding_conflicts_owner_cancel_and_atomic_terminal(postgres_factory, tmp_path):
    factory, controller = postgres_factory, RunController()
    owner, outsider = f"run-owner-{uuid4()}", f"outsider-{uuid4()}"
    started, release = asyncio.Event(), asyncio.Event()
    model = GateModel(started=started, release=release)
    events = asyncio.Queue()
    child = tmp_path / "child"
    child.mkdir()
    task = None
    try:
        async with api(factory, controller) as client:
            async def bind(key, directory):
                response = await client.post("/v1/projects", json={"user_key": key, "name": "demo", "directory": str(directory)})
                assert response.status_code == 201, response.text
                return UUID(response.json()["id"])

            project_id = await bind(owner, tmp_path)
            assert await bind(owner, tmp_path / "child" / "..") == project_id
            foreign_project = await bind(outsider, child)
            assert len((await client.get("/v1/projects", params={"user_key": owner})).json()) == 1
            invalid = await client.post("/v1/projects", json={"user_key": owner, "name": "bad", "directory": str(tmp_path / "missing")})
            assert invalid.status_code == 400

            async def run():
                async with factory() as session:
                    return await service(session, controller, model).chat(user_key=owner, content="hello",
                                                                         project_id=project_id, event_sink=events.put)

            task = asyncio.create_task(run())
            first = await asyncio.wait_for(events.get(), 5)
            run_id = first["run_id"]
            conversation_id = UUID(first["payload"]["conversation_id"])
            await asyncio.wait_for(started.wait(), 5)
            # A separate connection observes the binding and events before the model returns.
            status = await client.get(f"/v1/runs/{run_id}", params={"user_key": owner})
            assert status.status_code == 200
            assert status.json()["status"] == "running"
            assert status.json()["project_id"] == str(project_id)
            assert status.json()["execution_directory"] == canonical_directory(str(tmp_path))
            assert status.json()["execution_mode"] == "original"
            replay = (await client.get(f"/v1/runs/{run_id}/events", params={"user_key": owner})).json()
            assert replay[0]["event_type"] == "run.started"
            for suffix, method in (("", client.get), ("/cancel", client.post)):
                assert (await method(f"/v1/runs/{run_id}{suffix}", params={"user_key": outsider})).status_code == 404
            assert not task.done()
            # The same session, an overlapping physical directory, and another user's project are all rejected.
            for key, project, conversation in (
                (owner, project_id, conversation_id), (outsider, foreign_project, None),
                (owner, foreign_project, None), (owner, foreign_project, conversation_id),
            ):
                async with factory() as session:
                    with pytest.raises(AgentInputError):
                        await service(session, controller).chat(user_key=key, content="hello", project_id=project,
                                                                conversation_id=conversation)
            # A separate project can execute without changing the running task's root.
            other_directory = tmp_path.parent / (tmp_path.name + "-other")
            other_directory.mkdir()
            other_project = await bind(owner, other_directory)
            async with factory() as session:
                await service(session, controller).chat(user_key=owner, project_id=other_project, content="列出记忆")
            unchanged = (await client.get(f"/v1/runs/{run_id}", params={"user_key": owner})).json()
            assert unchanged["execution_directory"] == status.json()["execution_directory"]
            cancellation = await client.post(f"/v1/runs/{run_id}/cancel", params={"user_key": owner})
            assert cancellation.json()["cancellation_requested"] is True
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
            terminal = (await client.get(f"/v1/runs/{run_id}", params={"user_key": owner})).json()
            assert terminal["status"] == "cancelled" and terminal["finished_at"]
            assert (await client.post(f"/v1/runs/{run_id}/cancel", params={"user_key": owner})).json()["cancellation_requested"] is False
            replay = (await client.get(f"/v1/runs/{run_id}/events", params={"user_key": owner})).json()
            assert replay[-1]["event_type"] == "run.cancelled"
            assert [event["sequence"] for event in replay] == list(range(1, len(replay) + 1))
            async with factory() as session:
                assert await session.scalar(select(func.count()).select_from(Message).where(Message.run_id == UUID(run_id))) == 0
                assert await session.scalar(select(func.count()).select_from(ExtractionJob).where(ExtractionJob.conversation_id == conversation_id)) == 0
                # The reservation is released, and omitted project_id inherits the conversation's binding.
                result = await service(session, controller).chat(user_key=owner, conversation_id=conversation_id, content="列出记忆")
                run = await session.get(Run, result["run_id"])
                assert run.project_id == project_id and run.execution_directory == status.json()["execution_directory"]
    finally:
        await controller.close()
        if task:
            await asyncio.gather(task, return_exceptions=True)
        await remove_users(factory, owner, outsider)


@pytest.mark.asyncio
async def test_plain_chat_worktree_rejection_and_failure_rolls_back_completion(postgres_factory, monkeypatch):
    factory, controller = postgres_factory, RunController()
    owner = f"atomic-{uuid4()}"
    started, release = asyncio.Event(), asyncio.Event()
    release.set()
    try:
        async with factory() as session:
            with pytest.raises(AgentInputError, match="worktree"):
                await service(session, controller).chat(user_key=owner, content="hello", execution_mode="worktree")
            assert await ConversationRepository(session).get_user(owner) is None
        original = ConversationRepository.add_run_event

        async def fail_completion(self, run_id, **kwargs):
            if kwargs["event_type"] == "run.completed":
                raise RuntimeError("injected terminal persistence failure")
            return await original(self, run_id, **kwargs)

        monkeypatch.setattr(ConversationRepository, "add_run_event", fail_completion)
        async with factory() as session:
            with pytest.raises(RuntimeError, match="injected"):
                await service(session, controller, GateModel(started=started, release=release)).chat(user_key=owner, content="hello")
        async with factory() as session:
            run = await session.scalar(select(Run).join(Conversation, Conversation.id == Run.conversation_id)
                                       .join(User, User.id == Conversation.user_id).where(User.external_key == owner))
            assert run.status is RunStatus.FAILED
            assert run.project_id is None and run.execution_directory is None
            events = list(await session.scalars(select(RunEvent.event_type).where(RunEvent.run_id == run.id).order_by(RunEvent.sequence)))
            assert events[-1] == "run.failed" and "memory.extraction_queued" not in events
            assert await session.scalar(select(func.count()).select_from(Message).where(Message.role == MessageRole.ASSISTANT, Message.conversation_id == run.conversation_id)) == 0
            assert await session.scalar(select(func.count()).select_from(ExtractionJob).where(ExtractionJob.conversation_id == run.conversation_id)) == 0
    finally:
        await controller.close()
        await remove_users(factory, owner)


@pytest.mark.asyncio
async def test_restart_interrupts_only_active_runs_once_without_replay(postgres_factory):
    factory, key = postgres_factory, f"restart-{uuid4()}"
    try:
        async with factory() as session:
            repository = ConversationRepository(session)
            user = await repository.get_or_create_user(key)
            conversation = await repository.create_conversation(user.id)
            for status in (RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.WAITING_APPROVAL, RunStatus.COMPLETED):
                run = await repository.create_run(conversation.id)
                run.status = status
                await repository.add_run_event(run.id, sequence=1, event_type="run.started", payload={})
            await session.commit()
        async with factory() as session:
            assert await interrupt_stale_runs(session) == 3
            assert await interrupt_stale_runs(session) == 0
            runs = list(await session.scalars(select(Run).where(Run.conversation_id == conversation.id)))
            assert sorted(run.status.value for run in runs) == ["completed", "interrupted", "interrupted", "interrupted"]
            assert await session.scalar(select(func.count()).select_from(RunEvent).where(RunEvent.event_type == "run.interrupted")) == 3
            assert await session.scalar(select(func.count()).select_from(Message).where(Message.conversation_id == conversation.id)) == 0
    finally:
        await remove_users(factory, key)


@pytest.mark.asyncio
async def test_second_runtime_cannot_recover_live_instance_and_lock_releases(postgres_factory):
    engine = postgres_factory.kw["bind"]
    async with runtime_lease(engine):
        with pytest.raises(RuntimeError, match="单进程"):
            async with runtime_lease(engine):
                pytest.fail("The second application must not reach run recovery")
    async with runtime_lease(engine):
        pass


@pytest.mark.asyncio
async def test_cancel_during_tool_rolls_back_partial_write_and_waits_for_cleanup(postgres_factory, monkeypatch):
    factory, controller = postgres_factory, RunController()
    key = f"tool-cancel-{uuid4()}"
    entered, cleaned = asyncio.Event(), asyncio.Event()
    events = asyncio.Queue()
    task = None

    class CallingModel(FakeChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

        async def _agenerate(self, messages, **kwargs):
            response = AIMessage(content="", tool_calls=[{"id": "partial", "name": "partial_write", "args": {}}])
            return ChatResult(generations=[ChatGeneration(message=response)])

    def registry(session, **kwargs):
        async def partial_write(arguments):
            session.add(Message(conversation_id=kwargs["conversation_id"], sequence=2,
                                role=MessageRole.TOOL, content="unfinished write"))
            await session.flush()
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                cleaned.set()

        return ToolRegistry([MemoryTool("builtin.test.partial", "partial_write", "Test partial write",
                                       {"type": "object"}, frozenset(), 30, partial_write)])

    monkeypatch.setattr(agent_module, "build_request_registry", registry)
    try:
        async def run():
            async with factory() as session:
                await service(session, controller, CallingModel()).chat(user_key=key, content="hello", event_sink=events.put)

        task = asyncio.create_task(run())
        first = await asyncio.wait_for(events.get(), 5)
        run_id = UUID(first["run_id"])
        await asyncio.wait_for(entered.wait(), 5)
        async with factory() as session:
            user_id = (await ConversationRepository(session).get_user(key)).id
        assert controller.cancel(run_id, user_id)
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
        assert cleaned.is_set()
        async with factory() as session:
            run = await session.get(Run, run_id)
            assert run.status is RunStatus.CANCELLED
            stored = list(await session.scalars(select(Message).where(Message.conversation_id == run.conversation_id)))
            assert [message.role for message in stored] == [MessageRole.USER]
            replay = await ConversationRepository(session).list_run_events(run_id, user_id)
            assert [event.event_type for event in replay][-2:] == ["tool.execution.cancelled", "run.cancelled"]
    finally:
        await controller.close()
        if task:
            await asyncio.gather(task, return_exceptions=True)
        await remove_users(factory, key)


@pytest.mark.asyncio
async def test_early_retrieval_failure_still_has_durable_terminal(postgres_factory, monkeypatch):
    factory, key, controller = postgres_factory, f"early-failure-{uuid4()}", RunController()

    async def broken_embedding(*args, **kwargs):
        raise RuntimeError("retrieval failed")

    monkeypatch.setattr(agent_module, "embed_text", broken_embedding)
    try:
        async with factory() as session:
            with pytest.raises(RuntimeError, match="retrieval"):
                await service(session, controller).chat(user_key=key, content="hello")
        async with factory() as session:
            user_id = (await ConversationRepository(session).get_user(key)).id
            run = await session.scalar(select(Run).join(Conversation, Conversation.id == Run.conversation_id)
                                       .where(Conversation.user_id == user_id))
            assert run.status is RunStatus.FAILED
            events = await ConversationRepository(session).list_run_events(run.id, user_id)
            assert [event.event_type for event in events] == ["run.started", "run.failed"]
    finally:
        await controller.close()
        await remove_users(factory, key)


@pytest.mark.asyncio
async def test_real_run_completes_after_stream_disconnect(postgres_factory, monkeypatch):
    factory, controller = postgres_factory, RunController()
    owner = f"disconnect-{uuid4()}"
    started, release = asyncio.Event(), asyncio.Event()
    model = GateModel(started=started, release=release)
    original = AgentService

    def create(session, **kwargs):
        return original(session, chat_model=model, embedding_model=None,
                        settings=Settings(_env_file=None, dashscope_api_key=""), **kwargs)

    monkeypatch.setattr(chat_api, "AgentService", create)
    try:
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(run_controller=controller)))
        response = await chat_api.chat_stream(chat_api.ChatRequest(user_key=owner, content="hello"), factory, request)
        iterator = response.body_iterator
        chunk = await anext(iterator)
        first = json.loads(chunk.split("data: ", 1)[1])
        await iterator.aclose()
        await asyncio.wait_for(started.wait(), 5)
        release.set()
        tasks = list(chat_api._running_stream_tasks)
        await asyncio.wait_for(asyncio.gather(*tasks), 5)
        async with factory() as session:
            run = await session.get(Run, UUID(first["run_id"]))
            assert run.status is RunStatus.COMPLETED
            events = await ConversationRepository(session).list_run_events(run.id, (await ConversationRepository(session).get_user(owner)).id)
            assert events[-1].event_type == "run.completed"
    finally:
        await controller.close()
        await remove_users(factory, owner)
