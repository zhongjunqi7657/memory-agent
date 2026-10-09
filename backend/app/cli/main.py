"""Minimal deterministic tools/MCP commands with JSON output."""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError

from app.cli.mcp import list_servers, server_status, test_server
from app.cli.tools import list_tools
from app.config.business import BusinessConfig, business_config_path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="memory-agent")
    parser.add_argument("--config", type=Path, help="Business TOML configuration path")
    groups = parser.add_subparsers(dest="group", required=True)
    tools = groups.add_parser("tools").add_subparsers(dest="command", required=True)
    tools.add_parser("list")
    mcp = groups.add_parser("mcp").add_subparsers(dest="command", required=True)
    mcp.add_parser("list")
    status = mcp.add_parser("status")
    status.add_argument("--api-url", default="http://127.0.0.1:8000")
    test = mcp.add_parser("test")
    test.add_argument("server_id")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        if args.config and not args.config.is_file():
            raise ValueError("配置文件不存在")
        business = BusinessConfig.load(args.config)
        exit_code = 0
        if args.group == "tools":
            result = list_tools(business)
        elif args.command == "list":
            result = list_servers(business)
        elif args.command == "status":
            result = server_status(args.api_url)
        else:
            result, exit_code = asyncio.run(test_server(business, args.server_id,
                cwd=str((args.config or business_config_path()).resolve().parent)))
    except (ValueError, OSError, HTTPError, URLError) as error:
        code = f"http_{error.code}" if isinstance(error, HTTPError) else "command_failed"
        result = {"error": {"code": code, "message": "命令失败，请检查配置或 API 连接"}}
        exit_code = 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return exit_code
