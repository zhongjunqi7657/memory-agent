"""Task diffs use the task baseline, including dirty and non-Git directories."""

import os
import subprocess

import pytest

from app.config.local import LocalExecutionConfig
from app.tools.local.files import FileWorkspace, LocalFileError
from app.workspace.snapshots import capture, changes, restore


def test_task_diff_preserves_preexisting_content_and_exact_bytes(tmp_path):
    path = tmp_path / "code.py"
    path.write_bytes(b"\xef\xbb\xbf# user edit\r\n")
    config = LocalExecutionConfig()
    baseline = capture(str(tmp_path), config)
    workspace = FileWorkspace(str(tmp_path), config)
    workspace.write("code.py", "# task edit\n", workspace.read("code.py")["version"])
    after = capture(str(tmp_path), config)
    diff = changes(baseline, after, config)
    assert len(diff) == 1
    assert "user edit" in diff[0]["diff"] and "task edit" in diff[0]["diff"]
    restore(workspace, "code.py", baseline["files"]["code.py"], after["files"]["code.py"])
    assert path.read_bytes() == b"\xef\xbb\xbf# user edit\r\n"


def test_restore_covers_new_deleted_and_external_conflicts(tmp_path):
    config = LocalExecutionConfig()
    workspace = FileWorkspace(str(tmp_path), config)
    baseline = capture(str(tmp_path), config)
    workspace.write("new.txt", "task", None)
    after = capture(str(tmp_path), config)
    (tmp_path / "new.txt").write_text("external")
    with pytest.raises(LocalFileError, match="版本"):
        restore(workspace, "new.txt", None, after["files"]["new.txt"])
    assert (tmp_path / "new.txt").read_text() == "external"
    (tmp_path / "new.txt").write_text("task")
    restore(workspace, "new.txt", None, after["files"]["new.txt"])
    assert not (tmp_path / "new.txt").exists()
    (tmp_path / "deleted.txt").write_bytes(b"keep\r\n")
    before_delete = capture(str(tmp_path), config)["files"]["deleted.txt"]
    workspace.delete("deleted.txt", before_delete["version"])
    restore(workspace, "deleted.txt", before_delete, None)
    assert (tmp_path / "deleted.txt").read_bytes() == b"keep\r\n"
    assert baseline["files"] == {}


def test_snapshots_omit_secrets_links_dependencies_and_bound_diff(tmp_path):
    (tmp_path / ".env").write_text("password=private")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "a.js").write_text("hidden")
    (tmp_path / "secret.txt").write_text("api_key=sk-abcdefghijklmnop")
    (tmp_path / "ok.py").write_text("a\n" * 2000)
    config = LocalExecutionConfig(max_diff_bytes=1024)
    before = capture(str(tmp_path), config)
    assert ".env" not in before["files"]
    assert before["files"]["secret.txt"]["content"] is None
    assert "sk-abcdefghijklmnop" not in str(before)
    (tmp_path / "ok.py").write_text("b\n" * 2000)
    diff = changes(before, capture(str(tmp_path), config), config)
    assert diff[0]["truncated"] and len(diff[0]["diff"].encode()) <= 1024


def test_snapshot_budget_fails_before_a_partial_baseline_is_used(tmp_path):
    (tmp_path / "a.txt").write_text("a" * 2048)
    with pytest.raises(LocalFileError, match="快照"):
        capture(str(tmp_path), LocalExecutionConfig(max_snapshot_bytes=1024))


def test_excluded_binary_is_not_mistaken_for_a_new_recoverable_text_file(tmp_path):
    config = LocalExecutionConfig()
    path = tmp_path / "binary.py"
    path.write_bytes(b"\0binary")
    before = capture(str(tmp_path), config)
    path.write_text("now text")
    after = capture(str(tmp_path), config)
    item, = changes(before, after, config)
    assert not item["recoverable"] and item["diff"] == ""
    item, = changes(after, before, config)
    assert not item["recoverable"]


def test_delivery_parent_creation_rejects_directory_links(tmp_path):
    source, outside = tmp_path / "source", tmp_path / "outside"
    source.mkdir()
    outside.mkdir()
    config = LocalExecutionConfig()
    workspace = FileWorkspace(str(source), config)
    workspace.write("result.txt", "result", None)
    target = capture(str(source), config)["files"]["result.txt"]
    alias = source / "alias"
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(outside)], capture_output=True, check=True)
    else:
        alias.symlink_to(outside, target_is_directory=True)
    with pytest.raises((LocalFileError, OSError)):
        restore(workspace, "alias/nested/result.txt", target, None, create_parents=True)
    assert not (outside / "nested").exists()
