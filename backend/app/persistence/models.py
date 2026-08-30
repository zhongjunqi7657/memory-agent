"""Database entities for conversations, runs and governed user memories."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.persistence.base import Base, TimestampMixin

JSON_TYPE = JSONB().with_variant(JSON(), "sqlite")


class MessageRole(str, enum.Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class RunStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class MemoryKind(str, enum.Enum):
    EPISODIC = "episodic"
    SEMANTIC = "semantic"


class MemoryStatus(str, enum.Enum):
    PENDING = "pending"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    DELETED = "deleted"


class MemorySensitivity(str, enum.Enum):
    NORMAL = "normal"
    SENSITIVE = "sensitive"
    SECRET = "secret"


class ExtractionJobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    external_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    display_name: Mapped[str | None] = mapped_column(String(120))

    conversations: Mapped[list[Conversation]] = relationship(back_populates="user")
    memories: Mapped[list[Memory]] = relationship(back_populates="user")


class Conversation(TimestampMixin, Base):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_user_updated", "user_id", "updated_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str | None] = mapped_column(String(200))
    summary: Mapped[str | None] = mapped_column(Text)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    user: Mapped[User] = relationship(back_populates="conversations")
    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )
    runs: Mapped[list[Run]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )
    extraction_jobs: Mapped[list[ExtractionJob]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class Message(TimestampMixin, Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id", "sequence", name="uq_messages_conversation_sequence"
        ),
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[MessageRole] = mapped_column(
        Enum(MessageRole, name="message_role"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    is_redacted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON_TYPE, default=dict, nullable=False
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class Run(TimestampMixin, Base):
    __tablename__ = "runs"
    __table_args__ = (
        Index("ix_runs_conversation_created", "conversation_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus, name="run_status"), default=RunStatus.QUEUED, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)

    conversation: Mapped[Conversation] = relationship(back_populates="runs")
    events: Mapped[list[RunEvent]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class RunEvent(Base):
    __tablename__ = "run_events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_run_events_run_sequence"),
        Index("ix_run_events_run_created", "run_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON_TYPE, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    run: Mapped[Run] = relationship(back_populates="events")


class Memory(TimestampMixin, Base):
    __tablename__ = "memories"
    __table_args__ = (
        Index("ix_memories_user_status", "user_id", "status"),
        Index("ix_memories_user_kind", "user_id", "kind"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[MemoryKind] = mapped_column(
        Enum(MemoryKind, name="memory_kind"), nullable=False
    )
    status: Mapped[MemoryStatus] = mapped_column(
        Enum(MemoryStatus, name="memory_status"),
        default=MemoryStatus.PENDING,
        nullable=False,
    )
    sensitivity: Mapped[MemorySensitivity] = mapped_column(
        Enum(MemorySensitivity, name="memory_sensitivity"),
        default=MemorySensitivity.NORMAL,
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_key: Mapped[str | None] = mapped_column(String(200))
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL")
    )
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("memories.id", ondelete="SET NULL")
    )
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON_TYPE, default=dict, nullable=False
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024))

    user: Mapped[User] = relationship(back_populates="memories")
    source_message: Mapped[Message | None] = relationship(
        foreign_keys=[source_message_id]
    )
    superseded_by: Mapped[Memory | None] = relationship(
        remote_side=[id], foreign_keys=[superseded_by_id]
    )


class ExtractionJob(TimestampMixin, Base):
    __tablename__ = "extraction_jobs"
    __table_args__ = (
        Index("ix_extraction_jobs_status_created", "status", "created_at"),
        Index("ix_extraction_jobs_status_available", "status", "available_at"),
        Index("ix_extraction_jobs_user_created", "user_id", "created_at"),
        UniqueConstraint("idempotency_key", name="uq_extraction_jobs_idempotency_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[ExtractionJobStatus] = mapped_column(
        Enum(ExtractionJobStatus, name="extraction_job_status"),
        default=ExtractionJobStatus.QUEUED,
        nullable=False,
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(100))
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON_TYPE, default=dict, nullable=False
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship()
    conversation: Mapped[Conversation] = relationship(back_populates="extraction_jobs")
    message: Mapped[Message] = relationship()
