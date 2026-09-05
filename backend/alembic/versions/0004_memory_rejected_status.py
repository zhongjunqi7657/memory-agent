"""Add an auditable rejected state for memory candidates."""

from collections.abc import Sequence

from alembic import op

revision: str = "0004_memory_rejected"
down_revision: str | None = "0003_job_reliability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE memory_status ADD VALUE IF NOT EXISTS 'rejected'")


def downgrade() -> None:
    # PostgreSQL cannot remove one enum value in place. Re-map rejected
    # candidates before rebuilding the type so downgrade stays deterministic.
    op.execute(
        "ALTER TABLE memories ALTER COLUMN status TYPE text "
        "USING status::text"
    )
    op.execute("UPDATE memories SET status = 'pending' WHERE status = 'rejected'")
    op.execute("DROP TYPE memory_status")
    op.execute(
        "CREATE TYPE memory_status AS ENUM "
        "('pending', 'active', 'superseded', 'deleted')"
    )
    op.execute(
        "ALTER TABLE memories ALTER COLUMN status TYPE memory_status "
        "USING status::memory_status"
    )
