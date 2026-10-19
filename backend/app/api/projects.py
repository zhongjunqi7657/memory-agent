"""User-scoped local directory bindings. No Git initialization or worktree fallback."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.db import get_session
from app.persistence.models import User
from app.persistence.projects import Project
from app.persistence.repositories import ConversationRepository
from app.security.auth import require_demo_auth
from app.workspace.paths import DirectoryError
from app.workspace.service import bind_project

router = APIRouter(prefix="/v1/projects", tags=["projects"], dependencies=[Depends(require_demo_auth)])


class ProjectRequest(BaseModel):
    user_key: str = Field(default="demo-user", min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=120)
    directory: str = Field(min_length=1, max_length=32767)


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    directory: str
    created_at: datetime


@router.post("", response_model=ProjectResponse, status_code=201)
async def create_project(request: ProjectRequest, session: Annotated[AsyncSession, Depends(get_session)]):
    try:
        user = await ConversationRepository(session).get_or_create_user(request.user_key)
        project = await bind_project(session, user.id, request.directory, request.name)
        await session.commit()
        return ProjectResponse.model_validate(project)
    except DirectoryError as error:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    session: Annotated[AsyncSession, Depends(get_session)],
    user_key: Annotated[str, Query(min_length=1, max_length=128)] = "demo-user",
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    projects = await session.scalars(select(Project).join(User, User.id == Project.user_id)
                                    .where(User.external_key == user_key)
                                    .order_by(Project.created_at.desc()).limit(limit))
    return [ProjectResponse.model_validate(project) for project in projects]
