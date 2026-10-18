"""Bounded UTF-8 operations with real-path checks and optimistic versions."""

import hashlib
import os
import re
import stat
from pathlib import Path

from app.config.local import LocalExecutionConfig
from app.security.redaction import redact_secrets
from app.tools.local.file_access import locked_file
from app.workspace.paths import canonical_directory

BLOCKED_PARTS = frozenset({".git", ".ssh", ".aws", ".azure", ".gnupg", "node_modules", ".venv", "__pycache__"})
CREDENTIAL_NAMES = frozenset({"credentials", "credentials.json", "secrets.json", "secrets.toml", ".npmrc", ".pypirc", ".netrc", "id_rsa", "id_ed25519"})
DEVICE_NAME = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\.|$)", re.IGNORECASE)


class LocalFileError(ValueError):
    pass


def version(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FileWorkspace:
    def __init__(self, directory: str, config: LocalExecutionConfig):
        self.root = Path(canonical_directory(directory))
        self.config = config

    def path(self, value: str, *, directory: bool = False) -> Path:
        relative = Path(value)
        if relative.is_absolute() or relative.drive or not value or "\\" in value or ":" in value:
            raise LocalFileError("仅接受项目内使用 / 分隔的相对路径")
        for part in relative.parts:
            lower = part.casefold()
            if (part == ".." or lower in BLOCKED_PARTS or lower in CREDENTIAL_NAMES
                    or lower.startswith(".env") or lower.endswith((".pem", ".key", ".pfx", ".p12", ".kdbx"))
                    or part.endswith((".", " ")) or DEVICE_NAME.match(part)):
                raise LocalFileError("路径越界或属于受保护路径")
        path = self.root / relative
        current = self.root
        if canonical_directory(str(self.root)) != str(self.root):
            raise LocalFileError("项目目录已改变")
        for part in relative.parts:
            current = current / part
            if current.is_symlink() or (current.exists() and os.path.normcase(str(current.resolve())) != os.path.normcase(str(current))):
                raise LocalFileError("不允许通过链接路径操作文件")
        if not path.resolve().is_relative_to(self.root):
            raise LocalFileError("路径超出授权目录")
        if not directory and path.suffix.casefold() not in self.config.text_extensions:
            raise LocalFileError("不支持该文件类型")
        return path

    def _read(self, path: Path) -> bytes:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise LocalFileError("仅支持普通且没有硬链接的文件")
        if info.st_size > self.config.max_file_bytes:
            raise LocalFileError("文件超过大小限制")
        with locked_file(path) as stream:
            if os.fstat(stream.fileno()).st_nlink != 1:
                raise LocalFileError("不支持硬链接文件")
            data = stream.read(self.config.max_file_bytes + 1)
        if len(data) > self.config.max_file_bytes or b"\0" in data:
            raise LocalFileError("文件过大或为二进制内容")
        try:
            data.decode("utf-8-sig")
        except UnicodeError as error:
            raise LocalFileError("仅支持 UTF-8 文本") from error
        return data

    def read(self, value: str) -> dict:
        data = self._read(self.path(value))
        return {"path": value, "content": redact_secrets(data.decode("utf-8-sig")).text, "version": version(data)}

    def write(self, value: str, content: str, expected_version: str | None) -> dict:
        path = self.path(value)
        if "\0" in content or redact_secrets(content).redacted:
            raise LocalFileError("拒绝写入包含可识别秘密的内容")
        # No automatic mkdir: creation is confined to an existing checked parent.
        if not path.parent.is_dir():
            raise LocalFileError("父目录不存在")
        original = self._read(path) if path.exists() else None
        if (version(original) if original is not None else None) != expected_version:
            raise LocalFileError("文件版本已改变，请重新读取")
        if original is not None and redact_secrets(original.decode("utf-8-sig")).redacted:
            raise LocalFileError("包含秘密的文件不可通过脱敏副本覆盖")
        if original is not None and b"\r\n" in original and b"\n" not in original.replace(b"\r\n", b""):
            content = content.replace("\r\n", "\n").replace("\n", "\r\n")
        data = content.encode("utf-8")
        if original and original.startswith(b"\xef\xbb\xbf"):
            data = b"\xef\xbb\xbf" + data
        if len(data) > self.config.max_file_bytes:
            raise LocalFileError("文件超过大小限制")
        # Recheck immediately before writing; exclusive create never overwrites.
        self.path(value)
        with locked_file(path, write=True, create=original is None) as stream:
            info = os.fstat(stream.fileno())
            if info.st_nlink != 1 or not stat.S_ISREG(info.st_mode):
                raise LocalFileError("文件身份已改变")
            if original is not None and version(stream.read(self.config.max_file_bytes + 1)) != expected_version:
                raise LocalFileError("文件版本已改变，请重新读取")
            self.path(value)
            stream.seek(0)
            stream.write(data)
            stream.truncate()
            stream.flush()
            os.fsync(stream.fileno())
        return {"path": value, "version": version(data), "bytes": len(data)}

    def delete(self, value: str, expected_version: str) -> dict:
        path = self.path(value)
        with locked_file(path, delete=True) as stream:
            if os.fstat(stream.fileno()).st_nlink != 1 or version(stream.read(self.config.max_file_bytes + 1)) != expected_version:
                raise LocalFileError("文件版本已改变，请重新读取")
            self.path(value)
        return {"path": value, "deleted": True}

    def list(self, value: str = ".") -> dict:
        path = self.path(value, directory=True)
        items = []
        truncated = False
        # scandir consumes entries lazily, never materializes an unbounded tree.
        with os.scandir(path) as entries:
            for entry in entries:
                relative = (path / entry.name).relative_to(self.root).as_posix()
                try:
                    checked = self.path(relative, directory=entry.is_dir(follow_symlinks=False))
                    if checked.is_file():
                        self.path(relative)
                    items.append({"path": relative, "directory": checked.is_dir()})
                except (LocalFileError, OSError):
                    continue
                if len(items) >= self.config.max_entries:
                    truncated = True
                    break
        return {"entries": items, "truncated": truncated}

    def search(self, query: str) -> dict:
        if not query or len(query) > 500 or redact_secrets(query).redacted:
            raise LocalFileError("搜索内容无效")
        matches, scanned, visited = [], 0, 0
        for directory, children, files in os.walk(self.root, followlinks=False):
            visited += 1
            if visited > self.config.max_search_files:
                return {"matches": matches, "truncated": True}
            allowed = []
            for child in children:
                try:
                    self.path((Path(directory) / child).relative_to(self.root).as_posix(), directory=True)
                    allowed.append(child)
                except (LocalFileError, OSError):
                    pass
            children[:] = allowed
            for name in files:
                scanned += 1
                if scanned > self.config.max_search_files:
                    return {"matches": matches, "truncated": True}
                relative = (Path(directory) / name).relative_to(self.root).as_posix()
                try:
                    content = self.read(relative)["content"]
                except (LocalFileError, OSError):
                    continue
                for number, line in enumerate(content.splitlines(), 1):
                    if query in line:
                        matches.append({"path": relative, "line": number, "text": line[:1000]})
                        if len(matches) >= self.config.max_entries:
                            return {"matches": matches, "truncated": True}
        return {"matches": matches, "truncated": False}
