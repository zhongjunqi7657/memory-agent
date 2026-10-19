"""Durable, single-use decisions bound to a concrete execution snapshot."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.base import Base, TimestampMixin
from app.persistence.models import JSON_TYPE


class Approval(TimestampMixin, Base):
    __tablename__ = "approvals"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    call_id: Mapped[str] = mapped_column(String(200), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_id: Mapped[str] = mapped_column(String(200), nullable=False)
    binding: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    session_allowed: Mapped[bool] = mapped_column(nullable=False, default=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    warning: Mapped[str] = mapped_column(Text, nullable=False)
