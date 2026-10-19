"""Explicit worktree lifecycle and version-bound user delivery decisions."""

from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from app.config.settings import get_settings
from app.persistence.workspaces import ManagedWorktree
from app.tools.local.files import FileWorkspace, LocalFileError
from app.workspace.git import GitRepository
from app.workspace.journal import review_token
from app.workspace.paths import canonical_directory
from app.workspace.snapshots import capture, changes, matches, restore


def storage_directory():
    root = get_settings().app_data_dir / "worktrees"
    root.mkdir(parents=True, exist_ok=True)
    return Path(canonical_directory(str(root)))


async def create_worktree(session, project, config, start_mode, untracked):
    source = GitRepository(project.directory, config)
    state = source.state()
    identity = uuid4()
    directory = storage_directory() / str(identity)
    row = ManagedWorktree(id=identity, project_id=project.id, user_id=project.user_id,
                          directory=str(directory), base_commit=state["head"], start_mode=start_mode,
                          source_state=state, status="creating")
    session.add(row)
    await session.commit()
    try:
        result = source.create(str(directory), start_mode, untracked)
        row.source_state = result["source_state"]
        row.baseline = capture(str(directory), config)
        row.baseline = {**row.baseline, "omitted": result["omitted"]}
        row.baseline_state = GitRepository(str(directory), config).state(include_ignored=True)
        row.status = "ready"
        await session.commit()
        return row
    except BaseException:
        row.status = "interrupted"
        await session.commit()
        raise


def checked_repository(row, project, config):
    target = Path(row.directory)
    if target != storage_directory() / str(row.id) or target.is_symlink():
        raise LocalFileError("worktree 不属于本应用管理目录")
    repo = GitRepository(row.directory, config)
    if repo.directory != row.directory or repo.common_directory() != GitRepository(project.directory, config).common_directory():
        raise LocalFileError("worktree 的仓库身份或路径已改变")
    return repo


async def execution_worktree(session, user_id, project, worktree_id, config):
    row = await session.scalar(select(ManagedWorktree).where(ManagedWorktree.id == worktree_id,
                                ManagedWorktree.user_id == user_id, ManagedWorktree.project_id == project.id))
    if row is None or row.status != "ready":
        raise LocalFileError("worktree 不存在、不属于此项目或已停止接收任务")
    checked_repository(row, project, config)
    return row.directory


def delivery_preview(row, project, config):
    if row.status != "ready":
        raise LocalFileError("仅可交付尚未带回的完整 worktree")
    repo = checked_repository(row, project, config)
    current_state = repo.state(include_ignored=True)
    source_state = GitRepository(project.directory, config).state()
    current = capture(row.directory, config)
    original = capture(project.directory, config)
    diff = changes(row.baseline, current, config)
    conflicts = []
    if source_state != row.source_state:
        conflicts.append("原目录状态自创建后已变化")
    if current_state["head"] != row.baseline_state["head"] or current_state["index"] != row.baseline_state["index"]:
        conflicts.append("worktree 的提交或暂存区已改变，不能仅交付文件内容")
    changed_names = {name for name in current_state["files"].keys() | row.baseline_state["files"].keys()
                     if current_state["files"].get(name) != row.baseline_state["files"].get(name)}
    supported = {item["path"] for item in diff if item["recoverable"]}
    ignored = {name.decode("utf-8") for name in repo.git("ls-files", "--others", "--ignored", "--exclude-standard", "-z").split(b"\0") if name}
    if changed_names - supported or changed_names & ignored:
        conflicts.append("含不受支持、被忽略或秘密文件的变化，无法安全带回")
    for item in diff:
        name = item["path"]
        if not matches(original["files"].get(name), row.baseline["files"].get(name)):
            conflicts.append(f"{name}：原目录已有不同于 worktree 起点的内容")
    return {"worktree_id": str(row.id), "changes": diff, "conflicts": conflicts,
            "fingerprint": review_token([str(row.id), source_state, current_state, row.status]),
            "limitations": "仅带回受支持的文本文件变化，不提交、不改变原目录暂存区；不包含网络或其他外部副作用"}


async def deliver(session, row, project, config, token):
    preview = delivery_preview(row, project, config)
    if token != preview["fingerprint"] or preview["conflicts"]:
        raise LocalFileError("带回状态已变化或存在冲突，请重新检查 Diff")
    current = capture(row.directory, config)
    state = checked_repository(row, project, config).state(include_ignored=True)
    workspace = FileWorkspace(project.directory, config)
    row.status = "delivering"
    row.delivered_state = {"worktree": state, "applied": []}
    await session.commit()
    try:
        if (GitRepository(project.directory, config).state() != row.source_state
                or checked_repository(row, project, config).state(include_ignored=True) != state):
            raise LocalFileError("确认后工作区已变化，停止带回")
        for item in preview["changes"]:
            name = item["path"]
            restore(workspace, name, current["files"].get(name), row.baseline["files"].get(name), create_parents=True)
            row.delivered_state = {"worktree": state, "applied": [*row.delivered_state["applied"], name]}
            await session.commit()
        if checked_repository(row, project, config).state(include_ignored=True) != state:
            raise LocalFileError("带回期间 worktree 发生变化，保留现场供检查")
        row.status = "delivered"
        await session.commit()
        return {"worktree_id": str(row.id), "status": row.status, "applied": row.delivered_state["applied"]}
    except BaseException:
        row.status = "interrupted"
        await session.commit()
        raise


def cleanup_preview(row, project, config):
    if row.status not in {"ready", "delivered"}:
        raise LocalFileError("worktree 状态不允许自动清理，请保留并检查现场")
    repo = checked_repository(row, project, config)
    current = repo.state(include_ignored=True)
    expected = row.delivered_state["worktree"] if row.status == "delivered" else row.baseline_state
    conflicts = []
    if current != expected:
        conflicts.append("worktree 存在未交付或交付后变化（含忽略文件），请保留")
    if row.status == "ready" and GitRepository(project.directory, config).state() != row.source_state:
        conflicts.append("原目录已变化，不能确认起点快照仍安全保留")
    return {"worktree_id": str(row.id), "conflicts": conflicts,
            "fingerprint": review_token([str(row.id), current, expected, row.status, conflicts])}


async def cleanup(session, row, project, config, token):
    preview = cleanup_preview(row, project, config)
    if preview["conflicts"] or preview["fingerprint"] != token:
        raise LocalFileError("清理状态已变化或仍有未交付改动，请保留 worktree")
    # Exact UUID-owned path, repository identity, all files (including ignored),
    # index and HEAD were verified. Force is needed for delivered/snapshot dirty
    # trees; it is never used to discard an unknown or undelivered modification.
    expected = row.delivered_state["worktree"] if row.status == "delivered" else row.baseline_state
    require_source = row.status == "ready"
    row.status = "removing"
    await session.commit()
    try:
        if (checked_repository(row, project, config).state(include_ignored=True) != expected
                or (require_source and GitRepository(project.directory, config).state() != row.source_state)):
            raise LocalFileError("确认后工作区已变化，停止清理")
        GitRepository(project.directory, config).git("worktree", "remove", "--force", row.directory)
        row.status = "removed"
        await session.commit()
    except BaseException:
        row.status = "interrupted"
        await session.commit()
        raise
    return {"worktree_id": str(row.id), "status": row.status}
