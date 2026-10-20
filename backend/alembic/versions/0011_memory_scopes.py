"""Layered memory and durable extraction scope snapshots."""

import sqlalchemy as sa

from alembic import op

revision = "0011_memory_scopes"
down_revision = "0010_workspace_changes"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("memories", sa.Column("project_id", sa.Uuid(), nullable=True))
    op.create_foreign_key("fk_memories_project", "memories", "projects", ["project_id"], ["id"])
    op.create_index("ix_memories_user_project_status", "memories", ["user_id", "project_id", "status"])
    op.add_column("extraction_jobs", sa.Column("project_id", sa.Uuid(), nullable=True))
    op.create_foreign_key("fk_extraction_jobs_project", "extraction_jobs", "projects", ["project_id"], ["id"])
    # Existing memories remain personal. Old project extraction jobs acquire the
    # source scope and will be subject to the new temporary-context policy.
    op.execute("""UPDATE extraction_jobs AS j SET project_id = c.project_id
                  FROM conversations AS c WHERE j.conversation_id = c.id
                  AND COALESCE(j.payload->>'job_type', '') != 'embedding_backfill'""")


def downgrade():
    # Never silently turn private project facts into globally visible memories.
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT count(*) FROM memories WHERE project_id IS NOT NULL")):
        raise RuntimeError("存在项目记忆，拒绝降级为个人记忆；请先备份并显式处理项目记忆")
    op.drop_constraint("fk_extraction_jobs_project", "extraction_jobs", type_="foreignkey")
    op.drop_column("extraction_jobs", "project_id")
    op.drop_index("ix_memories_user_project_status", table_name="memories")
    op.drop_constraint("fk_memories_project", "memories", type_="foreignkey")
    op.drop_column("memories", "project_id")
