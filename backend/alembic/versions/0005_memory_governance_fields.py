"""Add complete memory provenance and ranking fields."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005_memory_governance"
down_revision: str | None = "0004_memory_rejected"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "memories",
        sa.Column(
            "importance",
            sa.Numeric(precision=4, scale=3),
            server_default="0.500",
            nullable=False,
        ),
    )
    op.add_column(
        "memories",
        sa.Column(
            "source_message_ids",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "memories",
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_memories_conversation_id_conversations",
        "memories",
        "conversations",
        ["conversation_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute(
        "UPDATE memories SET source_message_ids = "
        "jsonb_build_array(source_message_id::text) "
        "WHERE source_message_id IS NOT NULL"
    )
    op.execute(
        "UPDATE memories SET conversation_id = messages.conversation_id "
        "FROM messages WHERE memories.source_message_id = messages.id"
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_memories_conversation_id_conversations", "memories", type_="foreignkey"
    )
    op.drop_column("memories", "conversation_id")
    op.drop_column("memories", "source_message_ids")
    op.drop_column("memories", "importance")
