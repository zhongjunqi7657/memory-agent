"""Project binding and recoverable run lifecycle."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0008_project_run_control"
down_revision = "0007_message_run_link"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for value in ("waiting_approval", "cancelled", "interrupted"):
        op.execute(f"ALTER TYPE run_status ADD VALUE '{value}'")
    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("directory", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "directory", name="uq_projects_user_directory"),
    )
    for table in ("conversations", "runs"):
        op.add_column(table, sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True))
        op.create_foreign_key(f"fk_{table}_project_id_projects", table, "projects", ["project_id"], ["id"])
    op.add_column("runs", sa.Column("execution_mode", sa.String(20), server_default="original", nullable=False))
    op.add_column("runs", sa.Column("execution_directory", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "execution_directory")
    op.drop_column("runs", "execution_mode")
    for table in ("runs", "conversations"):
        op.drop_constraint(f"fk_{table}_project_id_projects", table, type_="foreignkey")
        op.drop_column(table, "project_id")
    op.drop_table("projects")
    op.execute("UPDATE runs SET status = 'failed' WHERE status::text IN ('waiting_approval', 'cancelled', 'interrupted')")
    op.execute("ALTER TYPE run_status RENAME TO run_status_old")
    op.execute("CREATE TYPE run_status AS ENUM ('queued', 'running', 'completed', 'failed')")
    op.execute("ALTER TABLE runs ALTER COLUMN status TYPE run_status USING status::text::run_status")
    op.execute("DROP TYPE run_status_old")
