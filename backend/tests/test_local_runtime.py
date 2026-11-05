import asyncio
import json
import logging
import subprocess
import threading
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.config.settings import Settings
from app.local_runtime import backup as archive_module
from app.local_runtime import checks
from app.local_runtime.logging import configure_logging
from app.local_runtime.service import run_worker_until_stopped, wait_worker_ready


def test_archive_command_never_places_credentials_in_arguments(monkeypatch):
    settings = Settings(_env_file=None, database_url="postgresql+asyncpg://user:private-password@localhost:5432/demo")
    monkeypatch.setattr(archive_module.shutil, "which", lambda _name: "pg_dump")
    calls = []
    monkeypatch.setattr(archive_module.subprocess, "run", lambda argv, **kwargs: calls.append((argv, kwargs)) or SimpleNamespace(returncode=0))
    archive_module.archive_command(settings, "pg_dump", ["--format=custom"])
    assert "private-password" not in str(calls[0][0])
    assert calls[0][1]["env"]["PGPASSWORD"] == "private-password"
    assert "LOCAL_API_TOKEN" not in calls[0][1]["env"]


def test_failed_archive_command_is_an_error_without_raw_output(monkeypatch):
    monkeypatch.setattr(archive_module.shutil, "which", lambda _name: "pg_dump")
    monkeypatch.setattr(archive_module.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1))
    with pytest.raises(ValueError, match="备份或恢复失败"):
        archive_module.archive_command(Settings(_env_file=None), "pg_dump", [])


async def test_restore_requires_confirmation_before_connecting(tmp_path, monkeypatch):
    monkeypatch.setattr(archive_module, "create_async_engine", lambda *_args: pytest.fail("must not connect"))
    with pytest.raises(ValueError, match="confirm-restore"):
        await archive_module.restore(tmp_path / "missing.dump")


async def test_failed_backup_removes_partial_files_and_preserves_existing_archives(tmp_path, monkeypatch):
    @asynccontextmanager
    async def lease(_engine):
        yield

    async def dispose():
        pass

    monkeypatch.setattr(archive_module, "runtime_lease", lease)
    monkeypatch.setattr(archive_module, "create_async_engine", lambda *_args: SimpleNamespace(dispose=dispose))

    def fail(*args):
        raise ValueError("dump failed")

    monkeypatch.setattr(archive_module, "archive_command", fail)
    target = tmp_path / "backup.dump"
    with pytest.raises(ValueError):
        await archive_module.backup(target)
    assert list(tmp_path.iterdir()) == []
    target.write_bytes(b"existing archive")
    with pytest.raises(FileExistsError):
        await archive_module.backup(target)
    assert target.read_bytes() == b"existing archive"


async def test_worker_stop_cancels_inflight_job_and_disposes_its_engine(monkeypatch):
    import app.jobs.worker as worker_module
    from app.persistence import db

    started, finished, disposed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    stop = SimpleNamespace(is_set=finished.is_set)

    async def loop():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            finished.set()

    async def dispose():
        disposed.set()

    monkeypatch.setattr(worker_module, "worker_loop", loop)
    monkeypatch.setattr(db, "engine", SimpleNamespace(dispose=dispose))
    task = asyncio.create_task(run_worker_until_stopped(stop))
    await started.wait()
    finished.set()
    await asyncio.wait_for(task, 2)
    assert disposed.is_set()


def test_formatted_logs_redact_secrets_even_in_tracebacks(tmp_path):
    settings = Settings(_env_file=None, app_data_dir=tmp_path, local_api_token="a-local-secret", dashscope_api_key="a-model-secret")
    handler = configure_logging(settings)
    try:
        try:
            raise ValueError("a-local-secret a-model-secret")
        except ValueError:
            logging.getLogger("stage7-test").exception("failure a-model-secret")
        handler.flush()
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()
    value = (tmp_path / "logs/runtime.log").read_text(encoding="utf-8")
    assert "a-local-secret" not in value
    assert "a-model-secret" not in value
    assert "[REDACTED]" in value


async def test_doctor_returns_safe_failure_when_database_is_unavailable(monkeypatch, tmp_path):
    settings = Settings(_env_file=None, database_url="postgresql+asyncpg://user:must-not-leak@127.0.0.1:1/demo")
    result = await checks.database_checks(settings)
    assert not result[0]["ok"]
    assert "must-not-leak" not in str(result)


async def test_migration_uses_absolute_paths_and_checks_subprocess_exit(monkeypatch):
    @asynccontextmanager
    async def lease(_engine):
        yield

    async def dispose():
        pass

    import app.execution.lease as lease_module
    monkeypatch.setattr(lease_module, "runtime_lease", lease)
    monkeypatch.setattr(checks, "create_async_engine", lambda *_args: SimpleNamespace(dispose=dispose))
    commands = []
    monkeypatch.setattr(checks.subprocess, "run", lambda argv, **kwargs: commands.append((argv, kwargs)) or SimpleNamespace(returncode=1))
    with pytest.raises(ValueError, match="迁移失败"):
        await checks.migrate()
    assert commands[0][1]["cwd"].is_absolute()
    assert commands[0][1]["stderr"] == subprocess.DEVNULL


async def test_backup_cancellation_keeps_lease_and_files_until_command_finishes(tmp_path, monkeypatch):
    started, finish = threading.Event(), threading.Event()
    lease_released = asyncio.Event()

    @asynccontextmanager
    async def lease(_engine):
        try:
            yield
        finally:
            lease_released.set()

    async def dispose():
        pass

    def command(_settings, _name, arguments):
        started.set()
        assert finish.wait(5)
        from pathlib import Path
        Path(arguments[-1]).write_bytes(b"complete archive")

    monkeypatch.setattr(archive_module, "runtime_lease", lease)
    monkeypatch.setattr(archive_module, "create_async_engine", lambda *_args: SimpleNamespace(dispose=dispose))
    monkeypatch.setattr(archive_module, "archive_command", command)
    task = asyncio.create_task(archive_module.backup(tmp_path / "archive.dump"))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not lease_released.is_set()
        assert not task.done()
    finally:
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert lease_released.is_set()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("command", ["doctor", "start"])
def test_cli_reports_failed_checks_and_never_starts_services(command, tmp_path, monkeypatch, capsys):
    from app.cli.main import main
    from app.local_runtime import service

    async def failed_doctor():
        return {"ok": False, "checks": [{"check": "migrations", "ok": False}]}

    monkeypatch.setattr(checks, "doctor", failed_doctor)
    monkeypatch.setattr(service, "serve", lambda: pytest.fail("failed preflight must not start services"))
    config = tmp_path / "config.toml"
    config.write_text("", encoding="utf-8")
    monkeypatch.setenv("MEMORY_AGENT_CONFIG", str(config))
    assert main(["--config", str(config), "local", command]) == 1
    assert json.loads(capsys.readouterr().out)["checks"] == [{"check": "migrations", "ok": False}]


async def test_doctor_flags_missing_dependencies_and_credentials_without_touching_data(tmp_path, monkeypatch):
    config = tmp_path / "config.toml"
    config.write_text("", encoding="utf-8")
    data = tmp_path / "not-created"
    settings = Settings(_env_file=None, app_data_dir=data, local_api_token="a" * 32, dashscope_api_key=None)
    monkeypatch.setattr(checks, "get_settings", lambda: settings)
    monkeypatch.setattr(checks, "business_config_path", lambda: config)
    monkeypatch.setattr(checks.shutil, "which", lambda _name: None)

    async def database(_settings):
        return [{"check": "database", "ok": True}, {"check": "migrations", "ok": False}]

    monkeypatch.setattr(checks, "database_checks", database)
    report = await checks.doctor()
    assert report["ok"] is False
    failed = {item["check"] for item in report["checks"] if not item["ok"]}
    assert {"node", "git", "model_credentials", "migrations"} <= failed
    assert not data.exists()


async def test_worker_readiness_requires_signal_and_rejects_early_exit_or_timeout():
    ready = asyncio.Event()
    worker = SimpleNamespace(is_alive=lambda: True)
    waiting = asyncio.create_task(wait_worker_ready(worker, ready, 2))
    await asyncio.sleep(0)
    assert not waiting.done()
    ready.set()
    await waiting
    ready.clear()
    with pytest.raises(ValueError, match="初始化超时"):
        await wait_worker_ready(worker, ready, 0)
    worker.is_alive = lambda: False
    with pytest.raises(ValueError, match="初始化期间退出"):
        await wait_worker_ready(worker, ready, 2)
