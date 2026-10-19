import os
import subprocess
from dataclasses import replace

import pytest

from app.config.local import LocalExecutionConfig
from app.protocol.tools import ToolCall, ToolExecutionContext, ToolStatus
from app.tools.executor import ToolExecutor
from app.tools.local.file_access import locked_file
from app.tools.local.files import FileWorkspace, LocalFileError
from app.tools.local.registry import create_local_tools
from app.tools.registry import ToolRegistry
from app.workspace.paths import canonical_directory


def test_versioned_edits_preserve_crlf_and_reject_external_changes(tmp_path):
    path = tmp_path / "code.py"
    path.write_bytes(b"print(1)\r\n")
    workspace = FileWorkspace(str(tmp_path), LocalExecutionConfig())
    before = workspace.read("code.py")
    changed = workspace.write("code.py", "print(2)\n", before["version"])
    assert path.read_bytes() == b"print(2)\r\n"
    assert changed["version"] != before["version"]
    with pytest.raises(LocalFileError, match="版本"):
        workspace.write("code.py", "bad", before["version"])
    assert path.read_bytes() == b"print(2)\r\n"
    with pytest.raises(LocalFileError):
        workspace.delete("code.py", before["version"])
    workspace.delete("code.py", changed["version"])
    assert not path.exists()
    workspace.write("new.txt", "new", None)
    with pytest.raises(LocalFileError):
        workspace.write("new.txt", "overwrite", None)


def test_sensitive_binary_and_escape_paths_are_rejected(tmp_path):
    workspace = FileWorkspace(str(tmp_path), LocalExecutionConfig())
    for name in (".env", "private.pem", "credentials.json", "binary.py"):
        (tmp_path / name).write_bytes(b"secret\x00")
        with pytest.raises(LocalFileError):
            workspace.read(name)
    for value in ("../outside.py", str(tmp_path.parent / "outside.py"), ".git/config", "file.txt:stream", "NUL.txt"):
        with pytest.raises(LocalFileError):
            workspace.write(value, "bad", None)
    (tmp_path / "data.txt").write_text("api_key=sk-abcdefghijklmnop", encoding="utf-8")
    assert "sk-abcdefghijklmnop" not in workspace.read("data.txt")["content"]
    with pytest.raises(LocalFileError):
        workspace.write("data.txt", "password=secret", workspace.read("data.txt")["version"])


def test_junction_and_hardlink_cannot_bypass_directory_authorization(tmp_path):
    outside = tmp_path / "outside"
    root = tmp_path / "project"
    outside.mkdir()
    root.mkdir()
    (outside / "secret.txt").write_text("private", encoding="utf-8")
    alias = root / "linked"
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(outside)], check=True, capture_output=True)
    else:
        alias.symlink_to(outside, target_is_directory=True)
    workspace = FileWorkspace(str(root), LocalExecutionConfig())
    with pytest.raises(LocalFileError):
        workspace.read("linked/secret.txt")
    os.link(outside / "secret.txt", root / "hard.txt")
    with pytest.raises(LocalFileError):
        workspace.write("hard.txt", "bad", workspace.read("hard.txt")["version"])
    assert (outside / "secret.txt").read_text(encoding="utf-8") == "private"


def test_locked_handle_prevents_windows_path_replacement(tmp_path):
    if os.name != "nt":
        # Portable equivalent: a no-follow open cannot traverse a symlink.
        target = tmp_path / "target.txt"
        target.write_text("original")
        alias = tmp_path / "alias.txt"
        alias.symlink_to(target)
        with pytest.raises(OSError), locked_file(alias):
            pytest.fail("Must not follow link")
        return
    directory = tmp_path / "directory"
    directory.mkdir()
    path = directory / "code.txt"
    path.write_text("original")
    with locked_file(path):
        with pytest.raises(OSError):
            path.rename(directory / "replaced.txt")
        with pytest.raises(OSError):
            directory.rename(tmp_path / "moved")
    assert path.read_text() == "original"


def test_listing_search_limits_and_bom_preservation(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("hidden needle")
    (tmp_path / ".env").write_text("hidden needle")
    (tmp_path / "a.txt").write_bytes(b"\xef\xbb\xbfneedle\r\n")
    (tmp_path / "b.py").write_text("needle\n", encoding="utf-8")
    workspace = FileWorkspace(str(tmp_path), LocalExecutionConfig(max_entries=1))
    assert workspace.list()["truncated"]
    found = workspace.search("needle")
    assert len(found["matches"]) == 1 and found["truncated"]
    assert found["matches"][0]["path"] in {"a.txt", "b.py"}
    original = workspace.read("a.txt")
    workspace.write("a.txt", "changed\n", original["version"])
    assert (tmp_path / "a.txt").read_bytes() == b"\xef\xbb\xbfchanged\r\n"


@pytest.mark.asyncio
async def test_file_tools_use_executor_and_require_directory_capabilities(tmp_path):
    tools = create_local_tools(LocalExecutionConfig(enabled=True))
    executor = ToolExecutor(ToolRegistry(tools))
    context = ToolExecutionContext("run", "test", project_id="project", execution_directory=canonical_directory(str(tmp_path)))
    call = ToolCall("write", "builtin.local.write", {"path": "a.txt", "content": "hello", "expected_version": None})
    result = await executor.execute(call, context)
    assert result.status is ToolStatus.DENIED and not (tmp_path / "a.txt").exists()
    result = await executor.execute(call, replace(context, granted_permissions=frozenset({"filesystem.write"})))
    assert result.status is ToolStatus.SUCCESS
    # Deletion always requires an actual call approval, regardless of static capabilities.
    result = await executor.execute(ToolCall("delete", "builtin.local.delete", {"path": "a.txt", "expected_version": result.content["version"]}),
                                    replace(context, granted_permissions=frozenset({"filesystem.write"})))
    assert result.status is ToolStatus.DENIED and (tmp_path / "a.txt").exists()
