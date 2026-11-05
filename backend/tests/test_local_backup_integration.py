"""Real pg_dump/pg_restore roundtrip; baseline Docker supplies the client tools."""

import asyncio
import shutil
import subprocess

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.agent.checkpoint import checkpoint_lifespan
from app.config.settings import Settings
from app.local_runtime import backup as archives
from tests.test_local_delivery_integration import (
    local_database as local_database,  # noqa: PLC0414
)


def container_archive_adapter(settings, monkeypatch):
    if shutil.which("pg_dump") and shutil.which("pg_restore"):
        return
    url = make_url(settings.database_url)
    assert url.host in {"127.0.0.1", "localhost"}
    assert url.database.endswith("_test")
    found = subprocess.run(["docker", "ps", "--filter", "name=memory-agent-baseline-", "--format", "{{.ID}} {{.Names}} {{.Ports}}"],
                           capture_output=True, text=True, check=True)
    rows = [row.split() for row in found.stdout.splitlines()
            if "memory-agent-baseline-" in row and f"127.0.0.1:{url.port}->5432/tcp" in row]
    assert len(rows) == 1, "Install native PostgreSQL clients or use verify-baseline.ps1"
    container = rows[0][0]

    def run(_settings, name, arguments):
        argv = ["docker", "exec", "-i", container, name, "-U", url.username]
        if name == "pg_dump":
            index = arguments.index("--file")
            with open(arguments[index + 1], "wb") as target:
                result = subprocess.run([*argv, "-d", url.database, *arguments[:index]],
                    stdout=target, stderr=subprocess.DEVNULL, check=False)
        else:
            with open(arguments[-1], "rb") as source:
                result = subprocess.run([*argv, *arguments[:-1]], stdin=source,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if result.returncode:
            raise ValueError("archive operation failed")
    monkeypatch.setattr(archives, "archive_command", run)


async def test_real_backup_restore_checkpoint_schema_and_failed_restore_rollback(local_database, tmp_path, monkeypatch):
    settings = Settings(_env_file=None, database_url=local_database, app_data_dir=tmp_path)
    monkeypatch.setattr(archives, "get_settings", lambda: settings)
    await asyncio.to_thread(container_archive_adapter, settings, monkeypatch)
    # Checkpoint tables are part of the same database archive.
    async with checkpoint_lifespan(settings):
        pass
    engine = create_async_engine(local_database)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE delivery_backup_marker (value text NOT NULL)"))
            await connection.execute(text("INSERT INTO delivery_backup_marker VALUES ('原始中文内容')"))
        target = tmp_path / "before.dump"
        await archives.backup(target)
        assert target.read_bytes().startswith(b"PGDMP")
        async with engine.begin() as connection:
            await connection.execute(text("UPDATE delivery_backup_marker SET value = 'changed'"))
        await archives.restore(target, confirmed=True)
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT value FROM delivery_backup_marker")) == "原始中文内容"
            assert await connection.scalar(text("SELECT to_regclass('public.checkpoints')")) == "checkpoints"
        corrupt = tmp_path / "corrupt.dump"
        corrupt.write_bytes(b"invalid archive")
        with pytest.raises(ValueError):
            await archives.restore(corrupt, confirmed=True)
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT value FROM delivery_backup_marker")) == "原始中文内容"
        # A dependency absent from the archive makes --clean fail after restore
        # has begun; the single transaction must preserve the pre-restore state.
        async with engine.begin() as connection:
            await connection.execute(text("UPDATE delivery_backup_marker SET value = 'must survive'"))
            await connection.execute(text("CREATE VIEW external_marker_dependency AS SELECT * FROM delivery_backup_marker"))
        with pytest.raises(ValueError):
            await archives.restore(target, confirmed=True)
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT value FROM delivery_backup_marker")) == "must survive"
            assert await connection.scalar(text("SELECT to_regclass('public.checkpoints')")) == "checkpoints"
    finally:
        await engine.dispose()
