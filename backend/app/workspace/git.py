"""Local Git metadata only. Never stash, commit, reset, fetch or push a project."""

import hashlib
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

from app.execution.process import process_environment
from app.security.redaction import redact_secrets
from app.tools.local.file_access import locked_file
from app.tools.local.files import FileWorkspace, LocalFileError
from app.workspace.paths import canonical_directory


class GitRepository:
    def __init__(self, directory, config):
        self.directory = canonical_directory(directory)
        self.root, self.config = Path(self.directory), config
        safe_path = os.pathsep.join(part for part in os.get_exec_path() if part and Path(part).is_absolute()
                                   and not Path(part).resolve().is_relative_to(self.root))
        self.executable = shutil.which("git", path=safe_path)
        if not self.executable:
            raise LocalFileError("需要安装 Git 才能使用 worktree")

    def git(self, *args, data=None):
        # No shell, hooks, fsmonitor, global/system config, inherited Git env or
        # project-selected executable. Checkout filters are never invoked: tree
        # blobs and the index are materialized explicitly below.
        environment = process_environment()
        environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_OPTIONAL_LOCKS="0")
        argv = [self.executable, "-c", f"core.hooksPath={os.devnull}", "-c", "core.fsmonitor=false",
                "-c", "core.quotePath=false", "-C", self.directory, *args]
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            try:
                result = subprocess.run(argv, input=data, stdin=subprocess.DEVNULL if data is None else None,
                                        stdout=output, stderr=errors, env=environment,
                                        timeout=self.config.command_timeout_seconds, check=False,
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            except (OSError, subprocess.TimeoutExpired) as error:
                raise LocalFileError("本地 Git 操作失败或超时") from error
            if result.returncode:
                raise LocalFileError("Git 操作被拒绝；请检查仓库、未合并冲突及目录状态")
            if output.tell() > self.config.max_snapshot_bytes * 2:
                raise LocalFileError("Git 数据超过快照容量上限")
            output.seek(0)
            return output.read()

    def common_directory(self):
        value = self.git("rev-parse", "--path-format=absolute", "--git-common-dir").decode().strip()
        return canonical_directory(value)

    def head(self):
        if canonical_directory(self.git("rev-parse", "--show-toplevel").decode().strip()) != self.directory:
            raise LocalFileError("worktree 项目必须绑定 Git 仓库根目录")
        return self.git("rev-parse", "--verify", "HEAD^{commit}").decode().strip()

    def index(self):
        raw = self.git("ls-files", "--stage", "-z")
        result = {}
        for item in raw.split(b"\0"):
            if not item:
                continue
            metadata, name = item.split(b"\t", 1)
            mode, oid, stage = metadata.decode().split()
            if stage != "0" or mode not in {"100644", "100755"}:
                raise LocalFileError("暂不支持含未合并、子模块或链接条目的 worktree")
            result[name.decode("utf-8")] = (mode, oid)
        return raw, result

    def manifest(self, names):
        result, total = {}, 0
        if len(names) > self.config.max_search_files:
            raise LocalFileError("Git 文件数量超过快照上限")
        for name in sorted(set(names)):
            path = self.root / name
            # Full state includes protected/binary paths by hash only, so an
            # unsupported change can block delivery without storing its bytes.
            if not path.exists() and not path.is_symlink():
                result[name] = None
                continue
            relative = Path(name)
            if relative.is_absolute() or relative.drive or ".." in relative.parts:
                raise LocalFileError("Git 路径无效")
            current = self.root
            for part in relative.parts:
                current /= part
                if current.is_symlink() or os.path.normcase(str(current.resolve())) != os.path.normcase(str(current)):
                    raise LocalFileError("Git 目录含链接，无法证明路径安全")
            info = path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise LocalFileError("Git 目录含不支持的特殊文件或硬链接")
            total += info.st_size
            if total > self.config.max_snapshot_bytes:
                raise LocalFileError("Git 文件总量超过快照上限")
            with locked_file(path) as source:
                opened = os.fstat(source.fileno())
                if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                    raise LocalFileError("Git 文件身份已改变")
                digest = hashlib.sha256()
                while chunk := source.read(65536):
                    digest.update(chunk)
            result[name] = {"version": digest.hexdigest(), "mode": stat.S_IMODE(info.st_mode)}
        return result

    def state(self, *, include_ignored=False):
        head = self.head()
        raw, indexed = self.index()
        args = ["ls-files", "--others", "-z"]
        if not include_ignored:
            args.append("--exclude-standard")
        others = [name.decode("utf-8") for name in self.git(*args).split(b"\0") if name]
        return {"head": head, "index": hashlib.sha256(raw).hexdigest(),
                "files": self.manifest([*indexed, *others])}

    def tree(self, head):
        entries = []
        for item in self.git("ls-tree", "-r", "-z", "--full-tree", head).split(b"\0"):
            if not item:
                continue
            metadata, name = item.split(b"\t", 1)
            mode, kind, oid = metadata.decode().split()
            if kind != "blob" or mode not in {"100644", "100755"}:
                raise LocalFileError("暂不支持含子模块或链接条目的 worktree")
            entries.append((name.decode("utf-8"), mode, oid))
        if len(entries) > self.config.max_search_files:
            raise LocalFileError("Git 文件数量超过快照上限")
        payload = "".join(f"{oid}\n" for _, _, oid in entries).encode()
        blobs = self.git("cat-file", "--batch", data=payload)
        offset, result = 0, []
        for name, mode, oid in entries:
            end = blobs.index(b"\n", offset)
            metadata = blobs[offset:end].decode().split()
            if metadata[:2] != [oid, "blob"]:
                raise LocalFileError("Git 对象不完整")
            size = int(metadata[2])
            data = blobs[end + 1:end + 1 + size]
            if len(data) != size:
                raise LocalFileError("Git 对象不完整")
            result.append((name, mode, data))
            offset = end + 2 + size
        return result

    def create(self, directory, start_mode, untracked):
        if start_mode not in {"head", "snapshot"} or (start_mode == "head" and untracked):
            raise LocalFileError("仅支持 head 或显式 snapshot 起点；未跟踪文件必须显式选择")
        destination = Path(directory)
        if not destination.is_absolute() or destination.exists() or destination.is_symlink():
            raise LocalFileError("worktree 目标必须是新的独立目录")
        if destination.resolve().is_relative_to(self.root) or self.root.is_relative_to(destination.resolve()):
            raise LocalFileError("worktree 不能与原目录嵌套")
        before = self.state()
        raw, indexed = self.index()
        source = FileWorkspace(self.directory, self.config)
        selected = set(untracked)
        candidates = {name.decode("utf-8") for name in self.git("ls-files", "--others", "--exclude-standard", "-z").split(b"\0") if name}
        if not selected.issubset(candidates):
            raise LocalFileError("选择的文件不是可带入的未跟踪文件")
        entries, omitted = [], []
        if start_mode == "head":
            entries = self.tree(before["head"])
        else:
            for name in sorted(indexed.keys() | selected):
                try:
                    path = source.path(name, directory=True)
                except LocalFileError:
                    if name in selected:
                        raise
                    omitted.append(name)
                    continue
                if not path.exists():
                    continue
                data = source._read(path)
                if redact_secrets(data.decode("utf-8-sig")).redacted:
                    if name in selected:
                        raise LocalFileError("显式选择的文件包含可识别秘密，无法带入 worktree")
                    omitted.append(name)
                    continue
                mode = indexed.get(name, ("100644",))[0] if os.name == "nt" else (
                    "100755" if path.stat().st_mode & stat.S_IXUSR else "100644")
                entries.append((name, mode, data))
        # Explicit unsafe untracked selections fail; tracked credential/dependency
        # paths and recognized secrets are omitted and reported for both starts.
        permitted = []
        for name, mode, data in entries:
            try:
                source.path(name, directory=True)
                text = data.decode("utf-8-sig")
                if redact_secrets(text).redacted:
                    raise LocalFileError("秘密文件")
            except UnicodeError:
                pass  # Committed binary assets may be checked out, not diffed.
            except LocalFileError:
                if start_mode == "snapshot":
                    raise LocalFileError("快照包含受保护路径") from None
                omitted.append(name)
                continue
            permitted.append((name, mode, data))
        if sum(len(data) for _, _, data in permitted) > self.config.max_snapshot_bytes:
            raise LocalFileError("worktree 内容超过快照容量上限")
        if self.state() != before:
            raise LocalFileError("创建前原目录已改变，请重试")
        self.git("worktree", "add", "--detach", "--no-checkout", str(destination), before["head"])
        # If a later step fails, preserve the registered directory for explicit
        # review/recovery instead of recursively deleting possible user changes.
        target = GitRepository(str(destination), self.config)
        if start_mode == "snapshot":
            target.git("read-tree", "--empty")
            if raw:
                target.git("update-index", "-z", "--index-info", data=raw)
        else:
            target.git("read-tree", before["head"])
        for name, mode, data in permitted:
            path = destination / name
            with locked_file(path, write=True, create=True, parents=True) as output:
                output.write(data)
                if os.name != "nt":
                    os.fchmod(output.fileno(), int(mode, 8) & 0o777)
        if self.state() != before:
            raise LocalFileError("创建期间原目录发生变化；保留 worktree 供检查，未执行任务")
        return {"base_commit": before["head"], "source_state": before, "omitted": omitted}
