"""Run: uv run --project backend python scripts/mcp-filesystem-demo.py"""

import asyncio
import json
import tempfile
from pathlib import Path

from app.audit.sink import CallbackAuditSink
from app.mcp.config import MCPConfig
from app.mcp.manager import MCPConnectionManager
from app.protocol.tools import (
    ToolCall,
    ToolErrorCategory,
    ToolExecutionContext,
    ToolStatus,
)
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry

SERVER_PACKAGE = "@modelcontextprotocol/server-filesystem@2026.8.31"
WRITE_TOOLS = ("write_file", "edit_file", "create_directory", "move_file")
EXPECTED_TOOLS = frozenset({
    "read_file", "read_text_file", "read_media_file", "read_multiple_files", "write_file",
    "edit_file", "create_directory", "list_directory", "list_directory_with_sizes",
    "directory_tree", "move_file", "search_files", "get_file_info", "list_allowed_directories",
})


async def demo() -> dict:
    with tempfile.TemporaryDirectory(prefix="memory-agent-mcp-demo-") as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        workspace.mkdir()
        (workspace / "hello.txt").write_text("hello from memory-agent", encoding="utf-8")
        outside = root / "outside.txt"
        outside.write_text("outside allowed directory", encoding="utf-8")
        config = MCPConfig(allowed_commands=["npx"], servers=[{
            "id": "filesystem", "command": "npx", "args": ["-y", SERVER_PACKAGE, str(workspace)],
            "command_timeout_seconds": 120, "env_allowlist": [],
            "default_permissions": {"capabilities": ["filesystem.read"]},
            "tool_permissions": {name: {"capabilities": ["filesystem.write"]} for name in WRITE_TOOLS},
        }])
        manager = MCPConnectionManager(config)
        audit = []

        async def emit(event_type, payload):
            audit.append({"event_type": event_type, "payload": payload})

        try:
            await manager.start()
            if manager.status()[0]["status"] != "ready":
                raise RuntimeError(f"官方 Server 启动失败: {manager.status()}")
            tools = manager.snapshot()
            if {tool.remote_name for tool in tools} != EXPECTED_TOOLS:
                raise RuntimeError("官方 Server 工具集合已变化，需重新审查显式权限配置")
            executor = ToolExecutor(ToolRegistry(tools), audit_sink=CallbackAuditSink(emit))
            read_context = ToolExecutionContext("filesystem-demo", "cli-demo",
                granted_permissions=frozenset({"filesystem.read"}))
            write_context = ToolExecutionContext("filesystem-demo", "cli-demo",
                granted_permissions=frozenset({"filesystem.read", "filesystem.write"}))
            client = tools[0].client
            remote_calls = []
            original = client.call_tool

            async def record(name, arguments):
                remote_calls.append(name)
                return await original(name, arguments)

            client.call_tool = record
            read = await executor.execute(ToolCall("read", "mcp.filesystem.read_text_file",
                {"path": str(workspace / "hello.txt")}), read_context)
            assert read.status is ToolStatus.SUCCESS and "hello from memory-agent" in str(read.content)
            denied = await executor.execute(ToolCall("deny", "mcp.filesystem.write_file",
                {"path": str(workspace / "denied.txt"), "content": "must not write"}), read_context)
            assert denied.status is ToolStatus.DENIED and "write_file" not in remote_calls
            assert not (workspace / "denied.txt").exists()
            written = await executor.execute(ToolCall("write", "mcp.filesystem.write_file",
                {"path": str(workspace / "written.txt"), "content": "explicitly authorized"}), write_context)
            assert written.status is ToolStatus.SUCCESS
            assert (workspace / "written.txt").read_text() == "explicitly authorized"
            boundary = await executor.execute(ToolCall("boundary", "mcp.filesystem.read_text_file",
                {"path": str(outside)}), read_context)
            assert boundary.error.category is ToolErrorCategory.BUSINESS
            for call_id in ("read", "deny", "write", "boundary"):
                assert len([item for item in audit if item["payload"]["call_id"] == call_id]) == 2
            report = {"package": SERVER_PACKAGE, "server_status": manager.status()[0]["status"],
                "read": read.status.value, "write": written.status.value,
                "permission_denied": denied.status.value, "denied_remote_calls": 0,
                "outside_directory": boundary.error.category.value, "audit": audit}
        finally:
            await manager.close()
        report["closed_status"] = manager.status()[0]["status"]
        return report


if __name__ == "__main__":
    print(json.dumps(asyncio.run(demo()), ensure_ascii=False, indent=2))
