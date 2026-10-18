"""Real temporary repositories: source index/worktree are never rewritten."""

import subprocess

import pytest

from app.config.local import LocalExecutionConfig
from app.tools.local.files import LocalFileError
from app.workspace.git import GitRepository


def git(root, *args):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True)
    return result.stdout


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    git(root, "init")
    git(root, "config", "user.name", "Workspace Tests")
    git(root, "config", "user.email", "workspace@example.test")
    git(root, "config", "core.autocrlf", "false")
    (root / "code.py").write_bytes(b"committed\n")
    (root / "deleted.txt").write_bytes(b"keep\n")
    (root / ".gitignore").write_text(".env\nignored.txt\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "Fixture baseline")
    return root


def test_head_and_snapshot_start_preserve_source_and_share_repository(repo, tmp_path):
    (repo / "code.py").write_bytes(b"staged\n")
    git(repo, "add", "code.py")
    (repo / "code.py").write_bytes(b"unstaged\r\n")
    (repo / "new.txt").write_bytes(b"selected\n")
    (repo / "omitted.txt").write_bytes(b"not selected\n")
    (repo / "ignored.txt").write_bytes(b"ignored\n")
    (repo / ".env").write_bytes(b"password=private\n")
    (repo / "deleted.txt").unlink()
    source = GitRepository(str(repo), LocalExecutionConfig())
    before = source.state()
    head = tmp_path / "head"
    source.create(str(head), "head", [])
    assert (head / "code.py").read_bytes() == b"committed\n"
    assert (head / "deleted.txt").exists()
    assert not (head / "new.txt").exists()
    snapshot = tmp_path / "snapshot"
    source.create(str(snapshot), "snapshot", ["new.txt"])
    assert (snapshot / "code.py").read_bytes() == b"unstaged\r\n"
    assert git(snapshot, "show", ":code.py") == b"staged\n"
    assert not (snapshot / "deleted.txt").exists()
    assert (snapshot / "new.txt").read_bytes() == b"selected\n"
    assert not (snapshot / "omitted.txt").exists()
    assert not (snapshot / "ignored.txt").exists() and not (snapshot / ".env").exists()
    assert source.state() == before
    assert GitRepository(str(snapshot), LocalExecutionConfig()).common_directory() == source.common_directory()


def test_invalid_modes_non_git_and_ignored_selection_have_no_side_effects(repo, tmp_path):
    source = GitRepository(str(repo), LocalExecutionConfig())
    before = source.state()
    (repo / "ignored.txt").write_text("keep")
    for mode, selected in (("other", []), ("head", ["new.txt"]), ("snapshot", ["ignored.txt"]), ("snapshot", ["../escape.txt"])):
        target = tmp_path / "bad"
        with pytest.raises(LocalFileError):
            source.create(str(target), mode, selected)
        assert not target.exists()
    assert source.state() == before
    empty = tmp_path / "not-git"
    empty.mkdir()
    with pytest.raises(LocalFileError):
        GitRepository(str(empty), LocalExecutionConfig()).state()
    assert not (empty / ".git").exists()


@pytest.mark.parametrize("mode", ["head", "snapshot"])
def test_tracked_credentials_and_dependencies_are_omitted_and_reported(repo, tmp_path, mode):
    (repo / ".env.example").write_text("placeholder")
    (repo / "settings.txt").write_text("api_key=sk-abcdefghijklmnop")
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "a.js").write_text("dependency")
    git(repo, "add", "-f", ".env.example", "settings.txt", "node_modules/a.js")
    git(repo, "commit", "-m", "Fixture protected paths")
    source = GitRepository(str(repo), LocalExecutionConfig())
    before = source.state()
    target = tmp_path / mode
    result = source.create(str(target), mode, [])
    assert set(result["omitted"]) == {".env.example", "settings.txt", "node_modules/a.js"}
    assert not (target / ".env.example").exists() and not (target / "settings.txt").exists()
    assert not (target / "node_modules").exists()
    assert source.state() == before
    (repo / "selected.txt").write_text("password=secret")
    with pytest.raises(LocalFileError, match="秘密"):
        source.create(str(tmp_path / "unsafe"), "snapshot", ["selected.txt"])
    assert not (tmp_path / "unsafe").exists()
