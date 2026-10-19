"""Durable local change evidence and application-owned Git worktrees."""

from uuid import UUID, uuid4

from sqlalchemy import Boolean, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.base import Base, TimestampMixin
from app.persistence.models import JSON_TYPE


class ManagedWorktree(TimestampMixin, Base):
    __tablename__ = "managed_worktrees"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    directory: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    base_commit: Mapped[str] = mapped_column(String(64), nullable=False)
    start_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="creating")
    source_state: Mapped[dict] = mapped_column(JSON_TYPE, default=dict, nullable=False)
    baseline: Mapped[dict] = mapped_column(JSON_TYPE, default=dict, nullable=False)
    baseline_state: Mapped[dict] = mapped_column(JSON_TYPE, default=dict, nullable=False)
    delivered_state: Mapped[dict | None] = mapped_column(JSON_TYPE)


class TaskSnapshot(TimestampMixin, Base):
    __tablename__ = "task_snapshots"

    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True)
    directory: Mapped[str] = mapped_column(Text, nullable=False)
    baseline: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False)
    edits: Mapped[dict] = mapped_column(JSON_TYPE, default=dict, nullable=False)
    external_effects: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="recording", nullable=False)
