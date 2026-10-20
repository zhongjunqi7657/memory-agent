"""Shared memory scope predicates; project identity never depends on a path."""

from uuid import UUID

from sqlalchemy import or_, select

from app.persistence.models import Memory
from app.persistence.projects import Project


def memory_scope(project_id: UUID | None, *, include_personal: bool = False):
    exact = Memory.project_id == project_id
    return or_(exact, Memory.project_id.is_(None)) if project_id and include_personal else exact


async def validate_project(session, user_id: UUID, project_id: UUID | None) -> None:
    if project_id is not None and await session.scalar(
        select(Project.id).where(Project.id == project_id, Project.user_id == user_id)
    ) is None:
        raise ValueError("项目不存在或不属于当前用户")
