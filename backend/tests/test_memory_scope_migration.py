"""Exercise the migration with existing rows in an isolated temporary schema."""

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations


async def test_legacy_memories_survive_upgrade_and_project_data_blocks_downgrade(postgres_factory):
    path = Path(__file__).parents[1] / "alembic/versions/0011_memory_scopes.py"
    spec = importlib.util.spec_from_file_location("memory_scope_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    schema = f"scope_migration_{uuid4().hex}"

    def verify(connection):
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
        for sql in (
            "CREATE TABLE projects (id uuid PRIMARY KEY)",
            "CREATE TABLE memories (id integer PRIMARY KEY, user_id uuid, status text, content text)",
            "CREATE TABLE conversations (id uuid PRIMARY KEY, project_id uuid)",
            "CREATE TABLE extraction_jobs (id integer PRIMARY KEY, conversation_id uuid, payload jsonb)",
            "INSERT INTO projects VALUES ('00000000-0000-0000-0000-000000000001')",
            "INSERT INTO memories VALUES (1, NULL, 'active', '用户偏好中文说明')",
            "INSERT INTO conversations VALUES ('00000000-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000001')",
            "INSERT INTO extraction_jobs VALUES (1, '00000000-0000-0000-0000-000000000002', '{}')",
            "INSERT INTO extraction_jobs VALUES (2, '00000000-0000-0000-0000-000000000002', '{\"job_type\":\"embedding_backfill\"}')",
        ):
            connection.exec_driver_sql(sql)
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            row = connection.exec_driver_sql("SELECT content, project_id FROM memories").one()
            assert row == ("用户偏好中文说明", None)
            jobs = connection.exec_driver_sql("SELECT project_id FROM extraction_jobs ORDER BY id").all()
            assert jobs[0][0] is not None and jobs[1][0] is None
            connection.exec_driver_sql("UPDATE memories SET project_id = '00000000-0000-0000-0000-000000000001'")
            with pytest.raises(RuntimeError, match="拒绝降级"):
                migration.downgrade()
            connection.exec_driver_sql("UPDATE memories SET project_id = NULL")
            migration.downgrade()
            assert connection.exec_driver_sql("SELECT content FROM memories").scalar() == "用户偏好中文说明"

    async with postgres_factory() as session:
        connection = await session.connection()
        try:
            await connection.run_sync(verify)
        finally:
            await session.rollback()  # Rolls back the entire temporary schema too.
