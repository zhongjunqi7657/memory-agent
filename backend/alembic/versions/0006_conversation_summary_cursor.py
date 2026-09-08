"""Track how far each durable conversation summary has progressed."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_summary_cursor"
down_revision: str | None = "0005_memory_governance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column(
            "summary_through_sequence",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("conversations", "summary_through_sequence")
