"""Task change evidence and explicitly created worktrees."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0010_workspace_changes"
down_revision = "0009_tool_approvals"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "managed_worktrees",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("directory", sa.Text(), nullable=False, unique=True),
        sa.Column("base_commit", sa.String(64), nullable=False),
        sa.Column("start_mode", sa.String(20), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("source_state", postgresql.JSONB(), nullable=False),
        sa.Column("baseline", postgresql.JSONB(), nullable=False),
        sa.Column("baseline_state", postgresql.JSONB(), nullable=False),
        sa.Column("delivered_state", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_managed_worktrees_project_id", "managed_worktrees", ["project_id"])
    op.add_column("runs", sa.Column("worktree_id", sa.Uuid(), sa.ForeignKey("managed_worktrees.id")))
    op.create_table(
        "task_snapshots",
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("directory", sa.Text(), nullable=False),
        sa.Column("baseline", postgresql.JSONB(), nullable=False),
        sa.Column("edits", postgresql.JSONB(), nullable=False),
        sa.Column("external_effects", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade():
    op.drop_table("task_snapshots")
    op.drop_column("runs", "worktree_id")
    op.drop_table("managed_worktrees")
