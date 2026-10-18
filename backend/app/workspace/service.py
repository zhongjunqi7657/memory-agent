"""Ownership checks and immutable project binding for a conversation."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.projects import Project
from app.workspace.paths import DirectoryError, canonical_directory


async def bind_project(session: AsyncSession, user_id: UUID, directory: str, name: str) -> Project:
    directory = canonical_directory(directory)
    # Rebinding an alias is idempotent, including concurrent requests.
    await session.execute(insert(Project).values(user_id=user_id, directory=directory, name=name)
                          .on_conflict_do_nothing(constraint="uq_projects_user_directory"))
    return await session.scalar(select(Project).where(Project.user_id == user_id, Project.directory == directory))


async def project_directory(session: AsyncSession, user_id: UUID, project_id: UUID | None) -> str | None:
    if project_id is None:
        return None
    project = await session.scalar(select(Project).where(Project.id == project_id, Project.user_id == user_id))
    if project is None:
        raise DirectoryError("项目不存在或不属于当前用户")
    directory = canonical_directory(project.directory)
    if directory != project.directory:
        raise DirectoryError("项目目录的链接目标已改变，请重新绑定")
    return directory
