"""Real decisions, transactions and processes; no LLM or paid API calls."""

import asyncio
import sys
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from langchain_core.language_models.fake_chat_models import FakeChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from sqlalchemy import delete, select

from app.agent.service import AgentService
from app.audit.sink import CallbackAuditSink
from app.config.business import BusinessConfig
from app.config.local import LocalExecutionConfig
from app.config.settings import Settings
from app.execution.controller import RunController
from app.execution.lifecycle import RunLifecycle, interrupt_stale_runs
from app.mcp.manager import MCPConnectionManager
from app.permission.approvals import ApprovalBroker
from app.persistence.approvals import Approval
from app.persistence.models import Run, RunEvent, RunStatus, User
from app.persistence.repositories import ConversationRepository
from app.persistence.workspaces import TaskSnapshot
from app.protocol.tools import ToolCall, ToolExecutionContext, ToolStatus
from app.tools.executor import ToolExecutor
from app.tools.local.registry import LOCAL_PERMISSIONS, create_local_tools
from app.tools.registry import ToolRegistry
from app.workspace.journal import ChangeJournal
from app.workspace.paths import canonical_directory
from app.workspace.service import bind_project
from tests.test_local_process import wait_marker
from tests.test_mcp_stdio_integration import fixture_config, process_alive
from tests.test_run_control_integration import api


@pytest.fixture
async def scenario(postgres_factory, tmp_path):
    factory, controller, broker = postgres_factory, RunController(), ApprovalBroker()
    key = f"approval-{uuid4()}"
    async with factory() as session:
        repository = ConversationRepository(session)
        user = await repository.get_or_create_user(key)
        project = await bind_project(session, user.id, str(tmp_path), "test")
        conversation = await repository.create_conversation(user.id)
        conversation.project_id = project.id
        await session.commit()
    events = asyncio.Queue()
    config = LocalExecutionConfig(enabled=True, approval_timeout_seconds=2)

    async def execute(arguments, *, tool="builtin.local.command", identity=None):
        async with factory() as session:
            lifecycle = RunLifecycle(session, controller, events.put)
            lifecycle.reserve(user.id, conversation.id, project.directory)
            repository = ConversationRepository(session)
            run = await repository.create_run(conversation.id, id=lifecycle.run_id, project_id=project.id, execution_directory=project.directory)
            await lifecycle.emit("run.started", {})

            async def authorize(tool, call, context):
                return await broker.authorize(tool, call, context, lifecycle, config.approval_timeout_seconds)

            context = ToolExecutionContext(str(run.id), "test", user_id=str(user.id), conversation_id=str(conversation.id),
                                           project_id=str(project.id), execution_directory=canonical_directory(str(tmp_path)),
                                           granted_permissions=LOCAL_PERMISSIONS, authorize=authorize,
                                           audit_sink=CallbackAuditSink(lifecycle.emit))
            if identity:
                context = replace(context, **identity)
            try:
                result = await ToolExecutor(ToolRegistry(create_local_tools(config))).execute(ToolCall(str(uuid4()), tool, arguments), context)
                await repository.finish_run(run, status=RunStatus.COMPLETED if result.status is ToolStatus.SUCCESS else RunStatus.FAILED)
                await lifecycle.emit("run.completed" if result.status is ToolStatus.SUCCESS else "run.failed", {})
                return result
            except asyncio.CancelledError as error:
                await lifecycle.terminate(RunStatus.CANCELLED, error)
                raise
            finally:
                controller.release(lifecycle.run_id)

    from app.main import app

    previous = getattr(app.state, "approval_broker", None)
    app.state.approval_broker = broker
    try:
        async with api(factory, controller) as client:
            yield {"factory": factory, "controller": controller, "broker": broker, "key": key,
                   "project": project, "conversation": conversation, "events": events, "execute": execute,
                   "client": client, "config": config, "root": tmp_path}
    finally:
        await controller.close()
        if previous is None:
            del app.state.approval_broker
        else:
            app.state.approval_broker = previous
        async with factory() as session:
            await session.execute(delete(User).where(User.external_key == key))
            await session.commit()


async def pending(scenario):
    while True:
        event = await asyncio.wait_for(scenario["events"].get(), 5)
        if event["event_type"] == "approval.requested":
            response = await scenario["client"].get("/v1/approvals", params={"user_key": scenario["key"]})
            assert response.status_code == 200
            return next(item for item in response.json() if item["id"] == event["payload"]["approval_id"])


async def decide(scenario, approval, decision="approve_once", **overrides):
    return await scenario["client"].post(f"/v1/approvals/{approval['id']}/decision", params={"user_key": scenario["key"]},
                                          json={"fingerprint": approval["fingerprint"], "decision": decision, **overrides})


@pytest.mark.asyncio
async def test_generated_uuid_metadata_is_not_mistaken_for_bank_card(scenario):
    identity = {"conversation_id": "b11c0d11-1444-4000-8000-111111111111"}
    task = asyncio.create_task(scenario["execute"](
        {"executable": sys.executable, "args": ["--version"]}, identity=identity,
    ))
    approval = await pending(scenario)
    assert approval["binding"]["conversation_id"] == identity["conversation_id"]
    assert (await decide(scenario, approval)).status_code == 200
    assert (await asyncio.wait_for(task, 5)).status is ToolStatus.SUCCESS


@pytest.mark.asyncio
async def test_rejection_ownership_stale_ui_and_replay_have_zero_side_effects(scenario):
    args = {"executable": sys.executable, "args": ["-c", "from pathlib import Path; Path('marker.txt').write_text('bad')"]}
    task = asyncio.create_task(scenario["execute"](args))
    approval = await pending(scenario)
    assert approval["binding"]["arguments"]["args"] == args["args"]
    assert approval["binding"]["project_id"] == str(scenario["project"].id)
    assert approval["binding"]["directory"] == scenario["project"].directory
    assert "沙箱" in approval["warning"] and not approval["session_allowed"]
    state = await scenario["client"].get(f"/v1/runs/{approval['run_id']}", params={"user_key": scenario["key"]})
    assert state.json()["status"] == "waiting_approval"
    assert (await scenario["client"].get("/v1/approvals", params={"user_key": "unknown"})).json() == []
    assert (await scenario["client"].post(f"/v1/approvals/{approval['id']}/decision", params={"user_key": "unknown"},
                                            json={"fingerprint": approval["fingerprint"], "decision": "approve_once"})).status_code == 404
    assert (await decide(scenario, approval, fingerprint="0" * 64)).status_code == 409
    assert (await decide(scenario, approval, "approve_session")).status_code == 409
    assert (await decide(scenario, approval, "reject")).status_code == 200
    result = await asyncio.wait_for(task, 5)
    assert result.status is ToolStatus.DENIED
    assert not (scenario["root"] / "marker.txt").exists()
    assert (await decide(scenario, approval)).status_code == 409


@pytest.mark.asyncio
async def test_single_use_approval_executes_exact_snapshot_and_rechecks_executable(scenario):
    args = {"executable": sys.executable, "args": ["-c", "from pathlib import Path; Path('marker.txt').write_text('approved')"]}
    task = asyncio.create_task(scenario["execute"](args))
    approval = await pending(scenario)
    args["args"][1] = "raise RuntimeError('mutated')"
    assert (await decide(scenario, approval)).status_code == 200
    assert (await asyncio.wait_for(task, 5)).status is ToolStatus.SUCCESS
    assert (scenario["root"] / "marker.txt").read_text() == "approved"
    executable = scenario["root"] / "fake.exe"
    executable.write_bytes(b"first")
    task = asyncio.create_task(scenario["execute"]({"executable": str(executable), "args": []}))
    approval = await pending(scenario)
    executable.write_bytes(b"changed")
    await decide(scenario, approval)
    result = await asyncio.wait_for(task, 5)
    assert result.status is not ToolStatus.SUCCESS and "失效" in result.error.message


@pytest.mark.asyncio
async def test_session_rule_is_exact_scoped_and_not_restored_after_restart(scenario):
    args = {"executable": sys.executable, "args": ["--version"]}
    task = asyncio.create_task(scenario["execute"](args))
    approval = await pending(scenario)
    assert approval["session_allowed"]
    await decide(scenario, approval, "approve_session")
    assert (await task).status is ToolStatus.SUCCESS
    assert len(scenario["broker"].rules) == 1
    assert (await asyncio.wait_for(scenario["execute"](args), 5)).status is ToolStatus.SUCCESS
    # Different argv cannot reuse the session grant.
    task = asyncio.create_task(scenario["execute"]({"executable": sys.executable, "args": ["-V"]}))
    changed = await pending(scenario)
    await decide(scenario, changed, "reject")
    assert (await task).status is ToolStatus.DENIED
    # Restart creates a fresh broker, never restores session grants from the DB.
    assert not ApprovalBroker().rules
    assert (await decide(scenario, approval)).status_code == 409
    revoked = await scenario["client"].post("/v1/approvals/revoke", params={"user_key": scenario["key"]},
                                             json={"conversation_id": str(scenario["conversation"].id)})
    assert revoked.status_code == 200 and not scenario["broker"].rules
    task = asyncio.create_task(scenario["execute"](args))
    approval = await pending(scenario)
    await decide(scenario, approval, "reject")
    assert (await task).status is ToolStatus.DENIED


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["expired", "cancelled"])
async def test_expiration_and_cancel_close_pending_approval_without_execution(scenario, outcome):
    scenario["config"].approval_timeout_seconds = 0.2 if outcome == "expired" else 5
    args = {"executable": sys.executable, "args": ["-c", "from pathlib import Path; Path('marker.txt').write_text('bad')"]}
    task = asyncio.create_task(scenario["execute"](args))
    approval = await pending(scenario)
    if outcome == "cancelled":
        assert (await scenario["client"].post(f"/v1/runs/{approval['run_id']}/cancel", params={"user_key": scenario["key"]})).status_code == 200
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
    else:
        assert (await asyncio.wait_for(task, 5)).status is ToolStatus.DENIED
    assert not scenario["broker"].pending and not (scenario["root"] / "marker.txt").exists()
    async with scenario["factory"]() as session:
        stored = await session.get(Approval, UUID(approval["id"]))
        assert stored.status == outcome
        if outcome == "cancelled":
            assert (await session.get(Run, stored.run_id)).status is RunStatus.CANCELLED
    assert (await decide(scenario, approval)).status_code == 409


@pytest.mark.asyncio
async def test_restart_invalidates_pending_approvals_without_reexecution(scenario):
    async with scenario["factory"]() as session:
        repository = ConversationRepository(session)
        run = await repository.create_run(scenario["conversation"].id)
        run.status = RunStatus.WAITING_APPROVAL
        from datetime import datetime, timedelta, timezone
        approval = Approval(run_id=run.id, user_id=scenario["project"].user_id, call_id="old", tool_id="old",
                            fingerprint="0" * 64, binding={}, status="pending", session_allowed=False,
                            warning="old", expires_at=datetime.now(timezone.utc) + timedelta(minutes=1))
        session.add(approval)
        await session.commit()
        approval_id = approval.id
    async with scenario["factory"]() as session:
        assert await interrupt_stale_runs(session) == 1
        assert (await session.get(Approval, approval_id)).status == "interrupted"
        assert await interrupt_stale_runs(session) == 0
        assert len(list(await session.scalars(select(RunEvent).where(RunEvent.run_id == run.id)))) == 1


@pytest.mark.asyncio
async def test_revocation_while_waiting_invalidates_old_approval(scenario):
    task = asyncio.create_task(scenario["execute"]({"executable": sys.executable, "args": ["--version"]}))
    approval = await pending(scenario)
    response = await scenario["client"].post("/v1/approvals/revoke", params={"user_key": scenario["key"]},
                                              json={"conversation_id": str(scenario["conversation"].id)})
    assert response.status_code == 200
    await decide(scenario, approval, "approve_session")
    assert (await task).status is ToolStatus.DENIED
    assert not scenario["broker"].rules
    async with scenario["factory"]() as session:
        assert (await session.get(Approval, UUID(approval["id"])) ).status == "revoked"


@pytest.mark.asyncio
async def test_real_agent_registry_read_edit_approved_command_and_durable_output(scenario):
    import json

    class CodingModel(FakeChatModel):
        count: int = 0

        def bind_tools(self, tools, **kwargs):
            assert "builtin__local__command" in {tool["function"]["name"] for tool in tools}
            return self

        async def _agenerate(self, messages, **kwargs):
            self.count += 1
            if self.count == 1:
                name, args = "builtin__local__read", {"path": "code.py"}
            elif self.count == 2:
                result = json.loads(next(message.content for message in reversed(messages) if isinstance(message, ToolMessage)))
                name, args = "builtin__local__write", {"path": "code.py", "content": "print(2)\n", "expected_version": result["version"]}
            elif self.count == 3:
                name, args = "builtin__local__command", {"executable": sys.executable, "args": ["code.py"]}
            else:
                output = json.loads(next(message.content for message in reversed(messages) if isinstance(message, ToolMessage)))
                assert output["exit_code"] == 0 and output["stdout"].strip() == "2"
                return ChatResult(generations=[ChatGeneration(message=AIMessage(content="done"))])
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="", tool_calls=[{"id": str(self.count), "name": name, "args": args}]))])

    (scenario["root"] / "code.py").write_bytes(b"print(1)\r\n")

    async def run():
        async with scenario["factory"]() as session:
            return await AgentService(session, chat_model=CodingModel(), embedding_model=None,
                                      business=BusinessConfig(local=scenario["config"]),
                                      settings=Settings(_env_file=None, dashscope_api_key=""),
                                      controller=scenario["controller"], broker=scenario["broker"]).chat(
                user_key=scenario["key"], content="edit and verify", project_id=scenario["project"].id,
                event_sink=scenario["events"].put)

    task = asyncio.create_task(run())
    approval = await pending(scenario)
    assert approval["tool_id"] == "builtin.local.command"
    await decide(scenario, approval)
    response = await asyncio.wait_for(task, 10)
    assert response["message"] == "done"
    assert (scenario["root"] / "code.py").read_bytes() == b"print(2)\r\n"
    async with scenario["factory"]() as session:
        events = list(await session.scalars(select(RunEvent).where(RunEvent.run_id == response["run_id"]).order_by(RunEvent.sequence)))
        commands = [event.payload["command_result"] for event in events if "command_result" in event.payload]
        assert commands[0]["exit_code"] == 0 and commands[0]["stdout"].strip() == "2"
        assert events[-1].event_type == "run.completed"
        snapshot = await session.get(TaskSnapshot, response["run_id"])
        changes = ChangeJournal(session, snapshot, scenario["config"]).preview()
        assert changes["external_effects"]
        assert not any(item["undoable"] for item in changes["changes"])
        with pytest.raises(ValueError, match="外部执行"):
            await ChangeJournal(session, snapshot, scenario["config"]).undo(changes["fingerprint"])


@pytest.mark.asyncio
async def test_application_shutdown_cancels_approved_command_and_reaps_descendants(scenario):
    scenario["config"].command_timeout_seconds = 30
    child = "import os,time,pathlib; pathlib.Path('pid.txt').write_text(str(os.getpid())); time.sleep(60)"
    parent = f"import subprocess,sys,time; subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(60)"
    task = asyncio.create_task(scenario["execute"]({"executable": sys.executable, "args": ["-c", parent]}))
    approval = await pending(scenario)
    await decide(scenario, approval)
    pid = await wait_marker(scenario["root"] / "pid.txt")
    assert process_alive(pid)
    await asyncio.wait_for(scenario["controller"].close(), 10)
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not process_alive(pid)
    async with scenario["factory"]() as session:
        assert (await session.get(Run, UUID(approval["run_id"]))).status is RunStatus.CANCELLED


@pytest.mark.asyncio
async def test_legacy_remote_http_requires_separate_call_approval_with_real_stdio(scenario, monkeypatch):
    import json

    import app.api.mcp as mcp_api
    from app.main import app

    config = fixture_config(scenario["root"] / "server-pid.txt")
    manager = MCPConnectionManager(config)
    previous = getattr(app.state, "mcp_manager", None)
    app.state.mcp_manager = manager
    monkeypatch.setattr(mcp_api, "get_business_config", lambda: BusinessConfig(mcp=config, local=scenario["config"]))
    try:
        await manager.start()
        assert manager.status()[0]["status"] == "ready"
        task = asyncio.create_task(scenario["client"].post("/v1/mcp/call", json={
            "user_key": scenario["key"], "conversation_id": str(scenario["conversation"].id),
            "tool": "mcp__fixture__remote_echo", "arguments": {"value": "approved"},
        }))
        approval = None
        for _ in range(100):
            response = await scenario["client"].get("/v1/approvals", params={"user_key": scenario["key"]})
            if response.json():
                approval = response.json()[0]
                break
            await asyncio.sleep(0.02)
        assert approval and not task.done()
        assert not approval["session_allowed"]
        assert (await decide(scenario, approval)).status_code == 200
        response = await asyncio.wait_for(task, 5)
        assert response.status_code == 200 and response.json()["ok"]
        assert "approved" in json.dumps(response.json())
        async with scenario["factory"]() as session:
            snapshot = await session.get(TaskSnapshot, UUID(approval["run_id"]))
            assert snapshot.external_effects
    finally:
        await manager.close()
        if previous is None:
            del app.state.mcp_manager
        else:
            app.state.mcp_manager = previous
