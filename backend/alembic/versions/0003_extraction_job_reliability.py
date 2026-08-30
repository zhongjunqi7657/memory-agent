"""Add extraction job leases, retries and idempotency metadata."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_job_reliability"
down_revision: str | None = "0002_embedding_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "extraction_jobs",
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.add_column(
        "extraction_jobs", sa.Column("locked_at", sa.DateTime(timezone=True))
    )
    op.add_column("extraction_jobs", sa.Column("locked_by", sa.String(length=100)))
    op.add_column(
        "extraction_jobs", sa.Column("idempotency_key", sa.String(length=200))
    )
    op.add_column("extraction_jobs", sa.Column("last_error", sa.Text()))
    op.execute(
        "UPDATE extraction_jobs "
        "SET idempotency_key = 'message:' || message_id::text "
        "WHERE idempotency_key IS NULL"
    )
    op.execute(
        "UPDATE extraction_jobs SET last_error = error_message "
        "WHERE last_error IS NULL"
    )
    op.alter_column("extraction_jobs", "idempotency_key", nullable=False)
    op.drop_column("extraction_jobs", "error_message")
    op.create_unique_constraint(
        "uq_extraction_jobs_idempotency_key", "extraction_jobs", ["idempotency_key"]
    )
    op.create_index(
        "ix_extraction_jobs_status_available",
        "extraction_jobs",
        ["status", "available_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_extraction_jobs_status_available", table_name="extraction_jobs"
    )
    op.drop_constraint(
        "uq_extraction_jobs_idempotency_key", "extraction_jobs", type_="unique"
    )
    op.add_column("extraction_jobs", sa.Column("error_message", sa.Text()))
    op.execute(
        "UPDATE extraction_jobs SET error_message = last_error "
        "WHERE error_message IS NULL"
    )
    op.drop_column("extraction_jobs", "last_error")
    op.drop_column("extraction_jobs", "idempotency_key")
    op.drop_column("extraction_jobs", "locked_by")
    op.drop_column("extraction_jobs", "locked_at")
    op.drop_column("extraction_jobs", "available_at")
