"""Prepare immutable command snapshots; execute argv directly without shell parsing."""

import hashlib
import os
import shutil
from pathlib import Path

from app.execution.process import run_process
from app.security.redaction import redact_secrets
from app.security.tool_secrets import contains_secret
from app.tools.local.files import LocalFileError
from app.workspace.paths import canonical_directory

SESSION_COMMANDS = frozenset({"python", "python.exe", "python3", "node", "node.exe", "pytest", "pytest.exe", "git", "git.exe"})
SESSION_GIT = frozenset({"status", "diff", "log", "show", "ls-files", "rev-parse"})
SESSION_OPTIONS = frozenset({"-q", "-v", "-x", "--short", "--porcelain", "--branch", "--stat", "--name-only", "--cached", "--oneline", "--show-toplevel", "--version", "--"})


def command_snapshot(arguments: dict, directory: str) -> dict:
    cwd = canonical_directory(directory)
    requested = arguments["executable"]
    if not Path(requested).is_absolute() and ("/" in requested or "\\" in requested):
        raise LocalFileError("可执行文件需使用完整路径或 PATH 中的名称")
    # Resolve only explicit absolute executables or PATH entries outside the project.
    safe_path = os.pathsep.join(part for part in os.get_exec_path() if part and Path(part).is_absolute()
                               and not Path(part).resolve().is_relative_to(Path(cwd)))
    executable = requested if Path(requested).is_absolute() else shutil.which(requested, path=safe_path)
    if not executable or not Path(executable).is_file():
        raise LocalFileError("可执行文件不存在，请提供完整路径")
    executable = str(Path(executable).resolve())
    if not Path(requested).is_absolute() and Path(executable).is_relative_to(Path(cwd)):
        raise LocalFileError("项目内可执行文件必须提供完整路径")
    if Path(executable).suffix.lower() in {".cmd", ".bat", ".ps1", ".sh"}:
        raise LocalFileError("脚本需显式指定解释器和完整参数，不隐式启动 Shell")
    argv = arguments.get("args", [])
    if contains_secret(argv) or redact_secrets(" ".join([executable, *argv])).redacted:
        raise LocalFileError("命令参数包含秘密，请使用本机凭据机制")
    with open(executable, "rb") as source:
        fingerprint = hashlib.sha256()
        while chunk := source.read(1_048_576):
            fingerprint.update(chunk)
    name = Path(executable).name.casefold()
    safe = name in SESSION_COMMANDS
    if name.startswith("python"):
        safe = argv[:2] in (["-m", "pytest"], ["-m", "unittest"]) or argv == ["--version"]
    elif name in {"node", "node.exe"}:
        safe = argv == ["--version"]
    if name in {"git", "git.exe"}:
        safe = bool(argv) and argv[0] in SESSION_GIT
    remaining = argv[2:] if name.startswith("python") and argv[:1] == ["-m"] else argv[1:] if name in {"git", "git.exe"} else argv
    for argument in remaining:
        if argument.startswith("-"):
            safe = safe and argument in SESSION_OPTIONS
        elif argument:
            path = Path(argument)
            candidate = path if path.is_absolute() else Path(cwd) / path
            safe = safe and candidate.resolve().is_relative_to(Path(cwd)) and not any(char in argument for char in (";", "|", "&", "`", "$", ">", "<"))
    safe = safe and not Path(executable).is_relative_to(Path(cwd))
    # Session reuse covers only these narrow exact command snapshots. Inline code,
    # shell interpreters and unknown/destructive programs need one-time approval.
    return {"executable": executable, "args": list(argv), "_directory": cwd,
            "_executable_version": fingerprint.hexdigest(), "_session_safe": safe}


async def execute_command(arguments, config):
    current = command_snapshot(arguments, arguments["_directory"])
    if current != arguments:
        raise LocalFileError("命令或执行目录已改变，原批准失效")
    return await run_process([arguments["executable"], *arguments["args"]], arguments["_directory"],
                             timeout=config.command_timeout_seconds, output_limit=config.max_output_bytes)
