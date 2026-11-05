"""Local runtime commands, kept separate from model-independent MCP commands."""

import asyncio
import os
from pathlib import Path


def add_local_commands(groups):
    local = groups.add_parser("local", help="本机启动、诊断、迁移和数据库归档")
    commands = local.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor")
    commands.add_parser("migrate")
    commands.add_parser("start")
    backup = commands.add_parser("backup")
    backup.add_argument("--output", type=Path, required=True)
    restore = commands.add_parser("restore")
    restore.add_argument("--input", type=Path, required=True)
    restore.add_argument("--confirm-restore", action="store_true")


def run_local(args):
    from app.config.business import get_business_config
    from app.config.settings import get_settings
    from app.local_runtime.checks import doctor, migrate

    if args.config:
        os.environ["MEMORY_AGENT_CONFIG"] = str(args.config.resolve())
        get_business_config.cache_clear()
    if args.command == "doctor":
        result = asyncio.run(doctor())
        return result, 0 if result["ok"] else 1
    if args.command == "migrate":
        return asyncio.run(migrate()), 0
    if args.command == "backup":
        from app.local_runtime.backup import backup
        return asyncio.run(backup(args.output)), 0
    if args.command == "restore":
        from app.local_runtime.backup import restore
        return asyncio.run(restore(args.input, confirmed=args.confirm_restore)), 0
    settings = get_settings()
    if settings.app_env == "demo":
        raise ValueError("本机启动入口不能用于演示部署")
    result = asyncio.run(doctor())
    if not result["ok"]:
        return result, 1
    from app.local_runtime.service import serve
    print(f"本地应用：http://{settings.app_host}:{settings.app_port}/；Ctrl+C 关闭 API 与 Worker", flush=True)
    try:
        serve()
    except KeyboardInterrupt:
        pass
    return {"stopped": True}, 0
