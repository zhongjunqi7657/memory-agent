"""Real PostgreSQL, Agent and Git verification of reviewable workspace changes."""

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from langchain_core.language_models.fake_chat_models import FakeChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from sqlalchemy import select

import app.api.workspaces as workspace_api
import app.workspace.worktrees as worktree_module
from app.agent.service import AgentInputError, AgentService
from app.config.business import BusinessConfig
from app.config.local import LocalExecutionConfig
from app.config.settings import Settings
from app.execution.controller import RunController
from app.persistence.models import Run, RunStatus
from app.persistence.repositories import ConversationRepository
from app.persistence.workspaces import ManagedWorktree, TaskSnapshot
from app.tools.local.files import FileWorkspace, LocalFileError
from app.workspace.git import GitRepository
from app.workspace.journal import ChangeJournal
from app.workspace.service import bind_project
from tests.test_run_control_integration import api, remove_users
from tests.test_workspace_git import git
from tests.test_workspace_git import repo as repo  # noqa: PLC0414


class EditModel(FakeChatModel):
    count: int = 0

    def bind_tools(self, tools, **kwargs):
        assert "builtin__local__write" in {item["function"]["name"] for item in tools}
        return self

    async def _agenerate(self, messages, **kwargs):
        self.count += 1
        if self.count == 1:
            name, args = "builtin__local__read", {"path": "code.py"}
        elif self.count in {2, 3}:
            result = json.loads(next(item.content for item in reversed(messages) if isinstance(item, ToolMessage)))
            assert "version" in result, result
            name, args = "builtin__local__write", {"path": "./code.py" if self.count == 2 else "code.py",
                                                    "content": f"print({self.count})\n", "expected_version": result["version"]}
        else:
            result = json.loads(next(item.content for item in reversed(messages) if isinstance(item, ToolMessage)))
            assert "version" in result, result
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="edited"))])
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="", tool_calls=[
            {"id": str(self.count), "name": name, "args": args}]))])


@pytest.fixture
async def workspace(postgres_factory, repo, tmp_path, monkeypatch):
    factory, controller = postgres_factory, RunController()
    key, config = f"workspace-{uuid4()}", LocalExecutionConfig(enabled=True)
    monkeypatch.setattr(workspace_api, "get_business_config", lambda: BusinessConfig(local=config))
    monkeypatch.setattr(worktree_module, "get_settings", lambda: SimpleNamespace(app_data_dir=tmp_path / "data"))
    async with factory() as session:
        repository = ConversationRepository(session)
        user = await repository.get_or_create_user(key)
        project = await bind_project(session, user.id, str(repo), "workspace")
        conversation = await repository.create_conversation(user.id)
        conversation.project_id = project.id
        await session.commit()
    try:
        async with api(factory, controller) as client:
            yield SimpleNamespace(factory=factory, controller=controller, config=config, key=key,
                                  project=project, conversation=conversation, root=repo, client=client)
    finally:
        await controller.close()
        await remove_users(factory, key)


async def request(s, method, path, **kwargs):
    return await getattr(s.client, method)(path, params={"user_key": s.key}, **kwargs)


async def create(s, mode="head", selected=()):
    response = await request(s, "post", f"/v1/projects/{s.project.id}/worktrees",
                             json={"start_mode": mode, "untracked_files": list(selected)})
    assert response.status_code == 201, response.text
    return response.json()


async def preview(s, row, operation):
    response = await request(s, "get", f"/v1/worktrees/{row['id']}/{operation}")
    assert response.status_code == 200, response.text
    return response.json()


async def confirm(s, row, operation, token):
    return await request(s, "post", f"/v1/worktrees/{row['id']}/{operation}",
                         json={"fingerprint": token, "confirm": True})


async def seed_journal(s):
    # A committed task record plus journal, independently reloadable by the API.
    async with s.factory() as session:
        repository = ConversationRepository(session)
        run = await repository.create_run(s.conversation.id, project_id=s.project.id,
                                          execution_directory=s.project.directory)
        run.status = RunStatus.COMPLETED
        journal = await ChangeJournal.start(session, run, s.config)
        return run.id, journal.snapshot.run_id


async def run_agent(s, **kwargs):
    async with s.factory() as session:
        return await AgentService(session, controller=s.controller, chat_model=EditModel(), embedding_model=None,
                                  settings=Settings(_env_file=None, dashscope_api_key=""),
                                  business=BusinessConfig(local=s.config)).chat(
            user_key=s.key, content="edit code", project_id=s.project.id, **kwargs)


@pytest.mark.asyncio
async def test_agent_diff_uses_dirty_task_baseline_and_undo_preserves_index(workspace):
    s = workspace
    (s.root / "code.py").write_bytes(b"staged\n")
    git(s.root, "add", "code.py")
    original = b"\xef\xbb\xbfuser dirty\r\n"
    (s.root / "code.py").write_bytes(original)
    before = GitRepository(str(s.root), s.config).state()
    response = await run_agent(s)
    run_id = response["run_id"]
    assert (s.root / "code.py").read_bytes() == b"\xef\xbb\xbfprint(3)\r\n"
    diff = await request(s, "get", f"/v1/runs/{run_id}/changes")
    assert diff.status_code == 200, diff.text
    item, = diff.json()["changes"]
    assert "user dirty" in item["diff"] and "committed" not in item["diff"]
    assert item["undoable"] and item["attribution"] == "file_tool"
    assert (await s.client.get(f"/v1/runs/{run_id}/changes", params={"user_key": "outsider"})).status_code == 404
    undone = await request(s, "post", f"/v1/runs/{run_id}/undo", json={"fingerprint": diff.json()["fingerprint"], "confirm": True})
    assert undone.status_code == 200, undone.text
    assert undone.json()["changes"] == []
    assert GitRepository(str(s.root), s.config).state() == before
    assert (await request(s, "get", f"/v1/projects/{s.project.id}/worktrees")).json() == []
    async with s.factory() as session:
        stored = await session.get(Run, run_id)
        assert stored.execution_mode == "original" and stored.worktree_id is None
        assert list((await session.get(TaskSnapshot, run_id)).edits) == ["code.py"]


@pytest.mark.asyncio
async def test_journal_create_delete_roundtrip_and_external_conflict(workspace):
    s = workspace
    run_id, _ = await seed_journal(s)
    async with s.factory() as session:
        journal = ChangeJournal(session, await session.get(TaskSnapshot, run_id), s.config)
        files = FileWorkspace(str(s.root), s.config)
        await journal.mutate(files, "write", {"value": "new.txt", "content": "created\n", "expected_version": None})
        await journal.mutate(files, "delete", {"value": "deleted.txt", "expected_version": files.read("deleted.txt")["version"]})
    diff = (await request(s, "get", f"/v1/runs/{run_id}/changes")).json()
    assert len(diff["changes"]) == 2 and all(item["undoable"] for item in diff["changes"])
    (s.root / "new.txt").write_text("external")
    assert (await request(s, "post", f"/v1/runs/{run_id}/undo", json={"fingerprint": diff["fingerprint"], "confirm": True})).status_code == 409
    latest = (await request(s, "get", f"/v1/runs/{run_id}/changes")).json()
    assert (await request(s, "post", f"/v1/runs/{run_id}/undo", json={"fingerprint": latest["fingerprint"], "confirm": True})).status_code == 409
    assert not (s.root / "deleted.txt").exists()  # Preflight prevents partial undo.
    (s.root / "new.txt").write_bytes(b"created\n")
    latest = (await request(s, "get", f"/v1/runs/{run_id}/changes")).json()
    undone = await request(s, "post", f"/v1/runs/{run_id}/undo", json={"fingerprint": latest["fingerprint"], "confirm": True})
    assert undone.status_code == 200, undone.text
    assert not (s.root / "new.txt").exists() and (s.root / "deleted.txt").read_bytes() == b"keep\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["external", "pending", "budget", "active"])
async def test_unsafe_or_active_journal_never_overwrites(workspace, reason):
    s = workspace
    run_id, _ = await seed_journal(s)
    async with s.factory() as session:
        snapshot = await session.get(TaskSnapshot, run_id)
        journal, files = ChangeJournal(session, snapshot, s.config), FileWorkspace(str(s.root), s.config)
        values = {"value": "code.py", "content": "task\n", "expected_version": files.read("code.py")["version"]}
        if reason == "budget":
            journal.config = s.config.model_copy(update={"max_snapshot_bytes": 1024})
            values["content"] = "x" * 1000
            with pytest.raises(LocalFileError, match="容量"):
                await journal.mutate(files, "write", values)
            assert (s.root / "code.py").read_bytes() == b"committed\n"
            assert snapshot.edits == {}
            return
        await journal.mutate(files, "write", values)
        if reason == "external":
            await journal.external_started()
            (s.root / "external.txt").write_bytes(b"command or MCP output\n")
        elif reason == "pending":
            snapshot.edits = {"code.py": {**snapshot.edits["code.py"], "status": "pending"}}
            await session.commit()
        else:
            (await session.get(Run, run_id)).status = RunStatus.RUNNING
            await session.commit()
    before = (s.root / "code.py").read_bytes()
    diff = (await request(s, "get", f"/v1/runs/{run_id}/changes")).json()
    undone = await request(s, "post", f"/v1/runs/{run_id}/undo", json={"fingerprint": diff["fingerprint"], "confirm": True})
    assert undone.status_code == 409, undone.text
    assert (s.root / "code.py").read_bytes() == before
    if reason == "external":
        assert diff["external_effects"] and not any(item["undoable"] for item in diff["changes"])


@pytest.mark.asyncio
async def test_worktree_agent_snapshot_delivery_and_confirmed_cleanup(workspace):
    s = workspace
    (s.root / "code.py").write_bytes(b"staged\n")
    git(s.root, "add", "code.py")
    (s.root / "code.py").write_bytes(b"user dirty\r\n")
    (s.root / "new.txt").write_bytes(b"selected\n")
    source = GitRepository(str(s.root), s.config)
    before = source.state()
    row = await create(s, "snapshot", ["new.txt"])
    directory = Path(row["directory"])
    assert source.state() == before
    (directory / "deleted.txt").unlink()
    (directory / "newdir").mkdir()
    (directory / "newdir" / "result.txt").write_bytes(b"result\n")
    response = await run_agent(s, execution_mode="worktree", worktree_id=UUID(row["id"]))
    assert (directory / "code.py").read_bytes() == b"print(3)\r\n"
    assert source.state() == before
    async with s.factory() as session:
        stored = await session.get(Run, response["run_id"])
        assert stored.project_id == s.project.id and str(stored.worktree_id) == row["id"]
        assert stored.execution_directory == row["directory"]
    blocked = await preview(s, row, "cleanup")
    assert blocked["conflicts"] and (await confirm(s, row, "cleanup", blocked["fingerprint"])).status_code == 409
    delivery = await preview(s, row, "delivery")
    assert not delivery["conflicts"]
    assert (await confirm(s, row, "delivery", "0" * 64)).status_code == 409
    assert (await confirm(s, row, "delivery", delivery["fingerprint"])).status_code == 200
    assert (s.root / "code.py").read_bytes() == b"print(3)\r\n"
    assert source.state()["index"] == before["index"]
    assert (s.root / "new.txt").read_bytes() == b"selected\n"
    assert not (s.root / "deleted.txt").exists()
    assert (s.root / "newdir" / "result.txt").read_bytes() == b"result\n"
    clean = await preview(s, row, "cleanup")
    assert not clean["conflicts"]
    assert (await confirm(s, row, "cleanup", clean["fingerprint"])).status_code == 200
    assert not directory.exists()
    async with s.factory() as session:
        assert (await session.get(ManagedWorktree, UUID(row["id"]))).status == "removed"


@pytest.mark.asyncio
@pytest.mark.parametrize("conflict", ["source", "dirty_head", "index", "ignored", "stale", "after_delivery"])
async def test_delivery_and_cleanup_protect_every_changed_state(workspace, conflict):
    s = workspace
    if conflict == "dirty_head":
        (s.root / "code.py").write_bytes(b"user dirty\n")
    row = await create(s)
    target = Path(row["directory"])
    (target / "code.py").write_bytes(b"task\n")
    first = await preview(s, row, "delivery")
    if conflict == "source":
        (s.root / "other.txt").write_text("external")
    elif conflict == "index":
        git(target, "add", "code.py")
    elif conflict == "ignored":
        (target / "ignored.txt").write_text("artifact")
    elif conflict == "stale":
        (target / "code.py").write_bytes(b"newer task\n")
    elif conflict == "after_delivery":
        assert (await confirm(s, row, "delivery", first["fingerprint"])).status_code == 200
        (target / "code.py").write_bytes(b"later external\n")
        clean = await preview(s, row, "cleanup")
        assert clean["conflicts"] and (await confirm(s, row, "cleanup", clean["fingerprint"])).status_code == 409
        assert target.exists()
        return
    source_before = GitRepository(str(s.root), s.config).state()
    assert (await confirm(s, row, "delivery", first["fingerprint"])).status_code == 409
    latest = await preview(s, row, "delivery")
    if conflict != "stale":
        assert latest["conflicts"]
        assert (await confirm(s, row, "delivery", latest["fingerprint"])).status_code == 409
    assert GitRepository(str(s.root), s.config).state() == source_before
    assert (await preview(s, row, "cleanup"))["conflicts"]


@pytest.mark.asyncio
async def test_worktree_ownership_explicit_mode_disabled_and_busy_directory(workspace):
    s = workspace
    with s.controller.hold_directories(s.project.user_id, s.project.directory):
        assert (await request(s, "post", f"/v1/projects/{s.project.id}/worktrees", json={})).status_code == 409
    row = await create(s)
    path = f"/v1/worktrees/{row['id']}/cleanup"
    assert (await s.client.get(path, params={"user_key": "outsider"})).status_code == 404
    assert (await request(s, "post", path, json={"fingerprint": "0" * 64, "confirm": False})).status_code == 422
    with pytest.raises(AgentInputError):
        await run_agent(s, execution_mode="worktree")
    with pytest.raises(AgentInputError):
        await run_agent(s, worktree_id=UUID(row["id"]))
    with s.controller.hold_directories(s.project.user_id, row["directory"]):
        assert (await request(s, "get", path)).status_code == 409
    s.config.enabled = False
    assert (await request(s, "post", f"/v1/projects/{s.project.id}/worktrees", json={})).status_code == 409
    clean = await preview(s, row, "cleanup")
    assert (await confirm(s, row, "cleanup", clean["fingerprint"])).status_code == 409
    s.config.enabled = True
    assert (await confirm(s, row, "cleanup", clean["fingerprint"])).status_code == 200


@pytest.mark.asyncio
async def test_interrupted_delivery_is_durable_and_preserves_worktree(workspace, monkeypatch):
    s = workspace
    row = await create(s)
    (Path(row["directory"]) / "code.py").write_bytes(b"task\n")
    delivery = await preview(s, row, "delivery")

    def fail(*args, **kwargs):
        raise OSError("simulated disk error")

    monkeypatch.setattr(worktree_module, "restore", fail)
    assert (await confirm(s, row, "delivery", delivery["fingerprint"])).status_code == 409
    assert (s.root / "code.py").read_bytes() == b"committed\n"
    assert Path(row["directory"]).exists()
    async with s.factory() as session:
        stored = await session.get(ManagedWorktree, UUID(row["id"]))
        assert stored.status == "interrupted" and stored.delivered_state["applied"] == []
    assert (await request(s, "get", f"/v1/worktrees/{row['id']}/cleanup")).status_code == 409


@pytest.mark.asyncio
async def test_after_write_interference_is_not_attributed_to_the_agent(workspace, monkeypatch):
    s = workspace
    run_id, _ = await seed_journal(s)
    async with s.factory() as session:
        files = FileWorkspace(str(s.root), s.config)
        journal = ChangeJournal(session, await session.get(TaskSnapshot, run_id), s.config)
        original = files.write

        def interfere(**values):
            result = original(**values)
            (s.root / "code.py").write_bytes(b"external writer\n")
            return result

        monkeypatch.setattr(files, "write", interfere)
        await journal.mutate(files, "write", {"value": "code.py", "content": "task\n",
                                              "expected_version": files.read("code.py")["version"]})
    latest = (await request(s, "get", f"/v1/runs/{run_id}/changes")).json()
    assert not latest["changes"][0]["undoable"]
    assert (await request(s, "post", f"/v1/runs/{run_id}/undo", json={"fingerprint": latest["fingerprint"], "confirm": True})).status_code == 409
    assert (s.root / "code.py").read_bytes() == b"external writer\n"


@pytest.mark.asyncio
async def test_managed_directory_identity_and_missing_historic_baseline(workspace):
    s = workspace
    async with s.factory() as session:
        run = await ConversationRepository(session).create_run(s.conversation.id, project_id=s.project.id)
        await session.commit()
    assert (await request(s, "get", f"/v1/runs/{run.id}/changes")).status_code == 409
    row = await create(s)
    async with s.factory() as session:
        stored = await session.get(ManagedWorktree, UUID(row["id"]))
        stored.directory = s.project.directory  # Never accept a DB path as deletion authority.
        await session.commit()
    before = GitRepository(str(s.root), s.config).state()
    assert (await request(s, "get", f"/v1/worktrees/{row['id']}/cleanup")).status_code == 409
    with pytest.raises(AgentInputError):
        await run_agent(s, execution_mode="worktree", worktree_id=UUID(row["id"]))
    assert GitRepository(str(s.root), s.config).state() == before


@pytest.mark.asyncio
async def test_failed_baseline_terminates_run_before_model_or_file_mutation(workspace):
    s = workspace
    original = b"x" * 2048
    (s.root / "code.py").write_bytes(original)
    s.config.max_snapshot_bytes = 1024
    with pytest.raises(AgentInputError, match="快照"):
        await run_agent(s)
    async with s.factory() as session:
        stored = await session.scalar(select(Run).where(Run.project_id == s.project.id))
        assert stored.status == RunStatus.FAILED and stored.finished_at is not None
        assert await session.get(TaskSnapshot, stored.id) is None
    assert (s.root / "code.py").read_bytes() == original
    # Failed start released its directory reservation.
    with s.controller.hold_directories(s.project.user_id, s.project.directory):
        pass
