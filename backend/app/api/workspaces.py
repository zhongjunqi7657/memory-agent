"""Reviewable local operations. No model tool can create/deliver/remove a worktree."""

from contextlib import contextmanager
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.execution import run_controller
from app.api.runs import owned_run
from app.config.business import get_business_config
from app.execution.controller import RunConflict
from app.execution.lifecycle import ACTIVE_STATUSES
from app.persistence.db import get_session
from app.persistence.models import User
from app.persistence.projects import Project
from app.persistence.workspaces import ManagedWorktree, TaskSnapshot
from app.security.auth import require_demo_auth
from app.tools.local.files import LocalFileError
from app.workspace.journal import ChangeJournal
from app.workspace.paths import DirectoryError
from app.workspace.service import project_directory
from app.workspace.worktrees import (
    cleanup,
    cleanup_preview,
    create_worktree,
    deliver,
    delivery_preview,
)

router = APIRouter(prefix="/v1", tags=["workspaces"], dependencies=[Depends(require_demo_auth)])
Session = Annotated[AsyncSession, Depends(get_session)]
UserKey = Annotated[str, Query(min_length=1, max_length=128)]


class WorktreeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_mode: Literal["head", "snapshot"] = "head"
    untracked_files: list[str] = Field(default_factory=list, max_length=1000)


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    fingerprint: str = Field(pattern="^[a-f0-9]{64}$")
    confirm: Literal[True]


@contextmanager
def workspace_errors():
    try:
        yield
    except (LocalFileError, DirectoryError, RunConflict) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except OSError as error:
        raise HTTPException(status_code=409, detail="本地工作区访问失败，请检查路径或权限") from error


def local_config():
    config = get_business_config().local
    if not config.enabled:
        raise HTTPException(status_code=409, detail="本地执行默认关闭，未开放工作区修改")
    return config


async def owned_project(session, project_id, user_key):
    project = await session.scalar(select(Project).join(User, User.id == Project.user_id)
                                   .where(Project.id == project_id, User.external_key == user_key))
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    await project_directory(session, project.user_id, project.id)
    return project


async def owned_worktree(session, worktree_id, user_key):
    row = await session.scalar(select(ManagedWorktree).join(User, User.id == ManagedWorktree.user_id)
                               .where(ManagedWorktree.id == worktree_id, User.external_key == user_key))
    if row is None:
        raise HTTPException(status_code=404, detail="worktree 不存在")
    return row, await owned_project(session, row.project_id, user_key)


def worktree_response(row):
    return {"id": row.id, "project_id": row.project_id, "directory": row.directory,
            "base_commit": row.base_commit, "start_mode": row.start_mode, "status": row.status,
            "omitted": row.baseline.get("omitted", [])}


@router.post("/projects/{project_id}/worktrees", status_code=201)
async def new_worktree(project_id: UUID, request: WorktreeRequest, http_request: Request,
                       session: Session, user_key: UserKey = "demo-user"):
    config = local_config()
    with workspace_errors():
        project = await owned_project(session, project_id, user_key)
        with run_controller(http_request).hold_directories(project.user_id, project.directory):
            row = await create_worktree(session, project, config, request.start_mode, request.untracked_files)
            return worktree_response(row)


@router.get("/projects/{project_id}/worktrees")
async def list_worktrees(project_id: UUID, session: Session, user_key: UserKey = "demo-user",
                        limit: Annotated[int, Query(ge=1, le=200)] = 100):
    with workspace_errors():
        project = await owned_project(session, project_id, user_key)
        rows = await session.scalars(select(ManagedWorktree).where(ManagedWorktree.project_id == project.id,
                                    ManagedWorktree.user_id == project.user_id)
                                    .order_by(ManagedWorktree.created_at.desc()).limit(limit))
        return [worktree_response(row) for row in rows]


@router.get("/worktrees/{worktree_id}/{operation}")
async def preview_worktree(worktree_id: UUID, operation: Literal["delivery", "cleanup"],
                           http_request: Request, session: Session, user_key: UserKey = "demo-user"):
    with workspace_errors():
        row, project = await owned_worktree(session, worktree_id, user_key)
        with run_controller(http_request).hold_directories(project.user_id, project.directory, row.directory):
            preview = delivery_preview if operation == "delivery" else cleanup_preview
            return preview(row, project, get_business_config().local)


@router.post("/worktrees/{worktree_id}/{operation}")
async def apply_worktree(worktree_id: UUID, operation: Literal["delivery", "cleanup"], request: ConfirmRequest,
                         http_request: Request, session: Session, user_key: UserKey = "demo-user"):
    config = local_config()
    with workspace_errors():
        row, project = await owned_worktree(session, worktree_id, user_key)
        with run_controller(http_request).hold_directories(project.user_id, project.directory, row.directory):
            action = deliver if operation == "delivery" else cleanup
            return await action(session, row, project, config, request.fingerprint)


async def owned_journal(session, run_id, user_key):
    run = await owned_run(session, run_id, user_key)
    snapshot = await session.get(TaskSnapshot, run.id)
    if snapshot is None:
        raise HTTPException(status_code=409, detail="此任务没有本地修改基线；不能推断任务 Diff 或撤销")
    return run, ChangeJournal(session, snapshot, get_business_config().local)


@router.get("/runs/{run_id}/changes")
async def task_changes(run_id: UUID, session: Session, user_key: UserKey = "demo-user"):
    with workspace_errors():
        _, journal = await owned_journal(session, run_id, user_key)
        return journal.preview()


@router.post("/runs/{run_id}/undo")
async def undo_task(run_id: UUID, request: ConfirmRequest, http_request: Request,
                    session: Session, user_key: UserKey = "demo-user"):
    local_config()
    with workspace_errors():
        run, journal = await owned_journal(session, run_id, user_key)
        if run.status in ACTIVE_STATUSES:
            raise HTTPException(status_code=409, detail="任务尚未结束，请先停止并等待清理")
        project = await owned_project(session, run.project_id, user_key)
        with run_controller(http_request).hold_directories(project.user_id, journal.snapshot.directory):
            return await journal.undo(request.fingerprint)
