"""Link assistant messages to their persisted runs."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007_message_run_link"
down_revision: str | None = "0006_summary_cursor"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_messages_run_id_runs",
        "messages",
        "runs",
        ["run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute(
        """
        WITH assistant_messages AS (
            SELECT id, conversation_id,
                   row_number() OVER (
                       PARTITION BY conversation_id ORDER BY sequence, id
                   ) AS ordinal
            FROM messages
            WHERE role = 'assistant'
        ),
        completed_runs AS (
            SELECT r.id, r.conversation_id,
                   row_number() OVER (
                       PARTITION BY r.conversation_id ORDER BY r.created_at, r.id
                   ) AS ordinal
            FROM runs AS r
            WHERE EXISTS (
                SELECT 1 FROM run_events AS event
                WHERE event.run_id = r.id AND event.event_type = 'run.completed'
            )
        )
        UPDATE messages AS message
        SET run_id = completed_runs.id
        FROM assistant_messages
        JOIN completed_runs
          ON completed_runs.conversation_id = assistant_messages.conversation_id
         AND completed_runs.ordinal = assistant_messages.ordinal
        WHERE message.id = assistant_messages.id
        """
    )
    op.create_unique_constraint("uq_messages_run_id", "messages", ["run_id"])


def downgrade() -> None:
    op.drop_constraint("uq_messages_run_id", "messages", type_="unique")
    op.drop_constraint("fk_messages_run_id_runs", "messages", type_="foreignkey")
    op.drop_column("messages", "run_id")
