import asyncio
import os
import shutil
import sys
from dataclasses import replace

import pytest

from app.audit.sink import CallbackAuditSink
from app.config.local import LocalExecutionConfig
from app.execution.process import run_process
from app.protocol.tools import ToolCall, ToolExecutionContext, ToolStatus
from app.tools.executor import ToolExecutor
from app.tools.local.commands import command_snapshot
from app.tools.local.registry import LOCAL_PERMISSIONS, create_local_tools
from app.tools.registry import ToolRegistry
from app.workspace.paths import canonical_directory
from tests.test_mcp_stdio_integration import process_alive


@pytest.mark.asyncio
async def test_native_process_environment_output_and_nonzero_exit(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "never-inherit")
    result = await run_process([sys.executable, "-c", "import os,sys; print(os.getcwd()); print(os.getenv('DASHSCOPE_API_KEY')); print('api_key=sk-abcdefghijklmnop'); print('x'*10000); sys.stderr.write('failure'); sys.exit(7)"],
                               str(tmp_path), timeout=5, output_limit=512)
    assert result["exit_code"] == 7 and not result["timed_out"]
    assert "None" in result["stdout"] and "never-inherit" not in str(result)
    assert "sk-abcdefghijklmnop" not in str(result)
    assert result["truncated"]
    assert len(result["stdout"]) + len(result["stderr"]) <= 512


async def wait_marker(path):
    for _ in range(300):
        if path.exists() and path.read_text(encoding="utf-8").strip():
            return int(path.read_text(encoding="utf-8"))
        await asyncio.sleep(0.02)
    pytest.fail("Child process did not start")


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["timeout", "cancel", "parent_exit"])
async def test_process_tree_reaped_including_after_parent_exits(tmp_path, operation):
    marker = tmp_path / "pid.txt"
    child = "import os,time,pathlib; pathlib.Path('pid.txt').write_text(str(os.getpid())); time.sleep(60)"
    parent = f"import subprocess,sys,time; subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep({0.3 if operation == 'parent_exit' else 60})"
    task = asyncio.create_task(run_process([sys.executable, "-c", parent], str(tmp_path), timeout=2, output_limit=1000))
    pid = await wait_marker(marker)
    assert process_alive(pid)
    if operation == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 10)
    else:
        result = await asyncio.wait_for(task, 10)
        assert result["timed_out"] == (operation == "timeout")
    assert not process_alive(pid)


@pytest.mark.asyncio
async def test_command_approval_snapshot_mutation_and_permission_revocation(tmp_path):
    marker = tmp_path / "side-effect.txt"
    arguments = {"executable": sys.executable, "args": ["-c", "from pathlib import Path; Path('side-effect.txt').write_text('original')"]}
    config = LocalExecutionConfig(enabled=True)
    registry = ToolRegistry(create_local_tools(config))
    context = ToolExecutionContext("run", "test", project_id="project", execution_directory=canonical_directory(str(tmp_path)), granted_permissions=LOCAL_PERMISSIONS)
    call = ToolCall("call", "builtin.local.command", arguments)
    denied = await ToolExecutor(registry).execute(call, context)
    assert denied.status is ToolStatus.DENIED and not marker.exists()
    received = []

    async def approve(tool, prepared, ctx):
        received.append(prepared.arguments.copy())
        arguments["args"][1] = "raise RuntimeError('changed')"
        return True

    result = await ToolExecutor(registry).execute(call, replace(context, authorize=approve))
    assert result.status is ToolStatus.SUCCESS
    assert marker.read_text() == "original" and "_executable_version" in received[0]
    marker.unlink()

    async def revoke(tool, prepared, ctx):
        config.enabled = False
        return True

    result = await ToolExecutor(registry).execute(call, replace(context, authorize=revoke))
    assert result.status is not ToolStatus.SUCCESS and not marker.exists()


def test_session_rules_never_include_shell_inline_code_or_git_push(tmp_path):
    inline = command_snapshot({"executable": sys.executable, "args": ["-c", "print('hello')"]}, str(tmp_path))
    assert not inline["_session_safe"]
    version = command_snapshot({"executable": sys.executable, "args": ["--version"]}, str(tmp_path))
    assert version["_session_safe"]
    assert version["_directory"] == canonical_directory(str(tmp_path))
    outside = command_snapshot({"executable": sys.executable, "args": ["-m", "pytest", str(tmp_path.parent / "outside.py")]}, str(tmp_path))
    assert not outside["_session_safe"]
    if executable := shutil.which("git"):
        push = command_snapshot({"executable": executable, "args": ["push", "origin", "main"]}, str(tmp_path))
        assert not push["_session_safe"]
    if os.name == "nt":
        shell = command_snapshot({"executable": os.environ["COMSPEC"], "args": ["/c", "echo hello"]}, str(tmp_path))
        assert not shell["_session_safe"]


@pytest.mark.asyncio
@pytest.mark.parametrize("code,timeout,expected", [
    ("import sys; print('test failed'); sys.exit(4)", 5, "command_failed"),
    ("import time; time.sleep(30)", 0.15, "command_timeout"),
])
async def test_executor_preserves_command_failure_output_and_exit_details(tmp_path, code, timeout, expected):
    config = LocalExecutionConfig(enabled=True, command_timeout_seconds=timeout)
    executor = ToolExecutor(ToolRegistry(create_local_tools(config)))
    events = []

    async def approve(*args):
        return True

    async def emit(name, payload):
        events.append((name, payload))

    context = ToolExecutionContext("run", "test", project_id="project", execution_directory=canonical_directory(str(tmp_path)),
                                   granted_permissions=LOCAL_PERMISSIONS, authorize=approve, audit_sink=CallbackAuditSink(emit))
    result = await executor.execute(ToolCall("call", "builtin.local.command", {"executable": sys.executable, "args": ["-c", code]}), context)
    assert result.status is ToolStatus.FAILED and result.error.code == expected
    assert events[-1][1]["command_result"] == result.content
    if expected == "command_failed":
        assert result.content["exit_code"] == 4 and "test failed" in result.content["stdout"]


@pytest.mark.asyncio
async def test_cancellation_signal_interrupts_approval_before_process_starts(tmp_path):
    entered, cleaned, cancellation = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def approve(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    context = ToolExecutionContext("run", "test", project_id="project", execution_directory=canonical_directory(str(tmp_path)),
                                   granted_permissions=LOCAL_PERMISSIONS, authorize=approve, cancellation=cancellation)
    executor = ToolExecutor(ToolRegistry(create_local_tools(LocalExecutionConfig(enabled=True))))
    task = asyncio.create_task(executor.execute(ToolCall("call", "builtin.local.command", {
        "executable": sys.executable, "args": ["-c", "from pathlib import Path; Path('bad.txt').touch()"],
    }), context))
    await asyncio.wait_for(entered.wait(), 5)
    cancellation.set()
    assert (await asyncio.wait_for(task, 5)).status is ToolStatus.CANCELLED
    assert cleaned.is_set() and not (tmp_path / "bad.txt").exists()
