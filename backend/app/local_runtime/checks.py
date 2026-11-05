"""Read-only startup diagnostics with safe, actionable status codes."""

import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config.business import BusinessConfig, business_config_path
from app.config.settings import get_settings
from app.local_runtime.maintenance import run_maintenance_command
from app.security.local_access import validate_local_settings

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = BACKEND_ROOT.parent


def migration_config():
    return Config(str(BACKEND_ROOT / "alembic.ini"))


async def database_checks(settings):
    engine = create_async_engine(settings.database_url, connect_args={"timeout": 5})
    try:
        async with engine.connect() as connection:
            vector = await connection.scalar(text("SELECT extversion FROM pg_extension WHERE extname = 'vector'"))
            revisions = set((await connection.execute(text("SELECT version_num FROM alembic_version"))).scalars())
            heads = set(ScriptDirectory.from_config(migration_config()).get_heads())
            return [{"check": "database", "ok": True}, {"check": "pgvector", "ok": bool(vector)},
                    {"check": "migrations", "ok": revisions == heads}]
    except Exception:  # noqa: BLE001 - expose safe diagnostics, never connection errors
        return [{"check": "database_schema", "ok": False, "hint": "检查数据库、pgvector，并显式执行 local migrate"}]
    finally:
        await engine.dispose()


async def doctor(*, require_token=True):
    settings = get_settings()
    checks = [{"check": "python", "ok": (3, 10) <= sys.version_info[:2] < (3, 14)}]
    for name in ("node", "git"):
        path = shutil.which(name)
        try:
            result = await asyncio.to_thread(subprocess.run, [path, "--version"],
                capture_output=True, timeout=5, check=False) if path else None
            ok = result is not None and result.returncode == 0
            if ok and name == "node":
                ok = int(result.stdout.decode().strip().lstrip("v").split(".")[0]) >= 20
        except (OSError, ValueError, subprocess.TimeoutExpired):
            ok = False
        checks.append({"check": name, "ok": ok})
    try:
        path = business_config_path()
        if not path.is_file():
            raise ValueError("缺少配置")
        business = BusinessConfig.load(path)
        from app.mcp.store import ConnectionStore
        ConnectionStore(settings.app_data_dir / "mcp-connections.json").load(business.mcp)
        validate_local_settings(settings.model_copy(update={"local_api_token": settings.local_api_token or ("x" * 32 if not require_token else None)}),
                                local_enabled=business.local.enabled)
        checks.append({"check": "configuration", "ok": True})
    except (ValueError, OSError):
        checks.append({"check": "configuration", "ok": False, "hint": "检查业务配置、本机绑定及访问令牌"})
    checks.extend([
        {"check": "model_credentials", "ok": bool(settings.dashscope_api_key and settings.dashscope_api_key != "replace-me")},
        {"check": "frontend_build", "ok": (REPOSITORY_ROOT / "frontend/dist/index.html").is_file()},
    ])
    try:
        checks.extend(await asyncio.wait_for(database_checks(settings), timeout=15))
    except asyncio.TimeoutError:
        checks.append({"check": "database", "ok": False, "hint": "数据库检查超时"})
    return {"ok": all(item["ok"] for item in checks), "checks": checks,
            "paths": {"config": str(business_config_path()), "data": str(settings.app_data_dir),
                      "logs": str(settings.app_data_dir / "logs"), "snapshots": "PostgreSQL workspaces / run events"}}


async def migrate():
    """Hold the API lease throughout explicit schema changes."""
    from app.execution.lease import runtime_lease

    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    try:
        async with runtime_lease(engine):
            # Alembic owns an event loop, so execute in a separate process.
            result = await run_maintenance_command(subprocess.run,
                [sys.executable, "-m", "alembic", "-c", str(BACKEND_ROOT / "alembic.ini"), "upgrade", "head"],
                cwd=BACKEND_ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            if result.returncode:
                raise ValueError("数据库迁移失败，请检查配置和迁移条件")
    finally:
        await engine.dispose()
    return {"migrated": True}
