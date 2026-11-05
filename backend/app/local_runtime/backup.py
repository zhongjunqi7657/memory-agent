"""Native PostgreSQL archives; credentials never enter command arguments."""

import os
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.config.settings import get_settings
from app.execution.lease import runtime_lease
from app.execution.process import process_environment
from app.local_runtime.maintenance import run_maintenance_command


def postgres_environment(settings):
    url = make_url(settings.database_url)
    if url.get_backend_name() != "postgresql":
        raise ValueError("备份恢复仅支持 PostgreSQL")
    return {**process_environment(), "PGHOST": url.host or "localhost", "PGPORT": str(url.port or 5432),
            "PGDATABASE": url.database or "", "PGUSER": url.username or "",
            "PGPASSWORD": url.password or "", "PGCONNECT_TIMEOUT": "5"}


def archive_command(settings, name, arguments):
    executable = shutil.which(name)
    if not executable:
        raise ValueError(f"需要 PostgreSQL 原生客户端 {name}")
    result = subprocess.run([executable, *arguments], env=postgres_environment(settings),
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    if result.returncode:
        raise ValueError("数据库备份或恢复失败；请检查客户端版本、连接与归档")


async def backup(output: Path):
    settings = get_settings()
    output = output.resolve()
    # Exclusive creation prevents replacing an existing backup, including symlinks.
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    temporary = output.with_name(f".{output.name}.{uuid4().hex}.partial")
    engine = None
    try:
        os.close(os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        engine = create_async_engine(settings.database_url)
        async with runtime_lease(engine):
            await run_maintenance_command(archive_command, settings, "pg_dump",
                ["--format=custom", "--no-owner", "--no-privileges", "--file", str(temporary)])
            if not temporary.is_file() or temporary.stat().st_size == 0:
                raise ValueError("数据库备份为空")
            temporary.replace(output)
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    finally:
        temporary.unlink(missing_ok=True)
        if engine is not None:
            await engine.dispose()
    return {"backup": str(output), "includes": ["business_data", "run_events", "task_snapshots", "checkpoints"],
            "separate_backup": ["config.toml", "mcp-connections.json", "managed worktrees / project files"]}


async def restore(archive: Path, *, confirmed=False):
    if not confirmed:
        raise ValueError("恢复会替换目标数据库数据，必须显式传入 --confirm-restore")
    archive = archive.resolve(strict=True)
    if not archive.is_file():
        raise ValueError("归档文件不存在")
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    try:
        async with runtime_lease(engine):
            await run_maintenance_command(archive_command, settings, "pg_restore",
                ["--dbname", make_url(settings.database_url).database, "--clean", "--if-exists",
                 "--no-owner", "--no-privileges", "--exit-on-error", "--single-transaction", str(archive)])
    finally:
        await engine.dispose()
    return {"restored": True}
