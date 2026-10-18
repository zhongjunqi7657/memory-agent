"""Bounded task baselines. Secret bytes never enter snapshots or diffs."""

import base64
import difflib
import os
import stat
from pathlib import Path

from app.security.redaction import redact_secrets
from app.tools.local.files import FileWorkspace, LocalFileError, version


def file_state(workspace, name):
    path = workspace.path(name)
    if not path.exists():
        return None
    data = workspace._read(path)
    content = None if redact_secrets(data.decode("utf-8-sig")).redacted else base64.b64encode(data).decode("ascii")
    return {"version": version(data), "content": content, "mode": stat.S_IMODE(path.stat().st_mode)}


def capture(directory, config):
    workspace = FileWorkspace(directory, config)
    files, excluded, total, visited = {}, [], 0, 0
    for root, children, names in os.walk(workspace.root, followlinks=False):
        visited += 1
        if visited > config.max_search_files:
            raise LocalFileError("目录超过快照条目上限")
        allowed = []
        for child in children:
            name = (Path(root) / child).relative_to(workspace.root).as_posix()
            try:
                workspace.path(name, directory=True)
                allowed.append(child)
            except (LocalFileError, OSError):
                continue
        children[:] = allowed
        for filename in names:
            visited += 1
            if visited > config.max_search_files:
                raise LocalFileError("目录超过快照条目上限")
            name = (Path(root) / filename).relative_to(workspace.root).as_posix()
            try:
                item = file_state(workspace, name)
            except (LocalFileError, OSError):
                # Excluded paths have no recoverable content. This is reported
                # explicitly; command/MCP side effects are never inferred owned.
                excluded.append(name)
                continue
            if item:
                total += len(item["content"] or "")
                if total > config.max_snapshot_bytes:
                    raise LocalFileError("目录超过快照容量上限")
                files[name] = item
    return {"files": files, "excluded": excluded}


def fingerprint(item):
    return (item["version"], item["mode"]) if item else None


def matches(left, right):
    return fingerprint(left) == fingerprint(right)


def changes(before, after, config):
    result = []
    remaining = config.max_diff_bytes
    for name in sorted(before["files"].keys() | after["files"].keys()):
        old, new = before["files"].get(name), after["files"].get(name)
        if matches(old, new):
            continue
        safe = (name not in before["excluded"] and name not in after["excluded"]
                and all(item is None or item["content"] is not None for item in (old, new)))
        patch, truncated = "", False
        if safe:
            def lines(item):
                return base64.b64decode(item["content"]).decode("utf-8-sig").splitlines(keepends=True) if item else []
            for line in difflib.unified_diff(lines(old), lines(new), fromfile=f"before/{name}", tofile=f"after/{name}"):
                encoded = line.encode("utf-8")
                part = encoded[:remaining].decode("utf-8", errors="ignore")
                patch += part
                remaining -= len(part.encode("utf-8"))
                if len(encoded) > len(part.encode("utf-8")):
                    truncated = True
                    break
        result.append({"path": name, "before_version": old["version"] if old else None,
                       "after_version": new["version"] if new else None, "diff": patch,
                       "truncated": truncated, "recoverable": safe})
    return result


def restore(workspace, name, target, expected, *, create_parents=False):
    if not matches(file_state(workspace, name), expected):
        raise LocalFileError("文件版本或权限已改变，停止恢复")
    if target is None:
        if expected:
            workspace.delete(name, expected["version"])
        return
    if target["content"] is None:
        raise LocalFileError("该文件没有可恢复的安全快照")
    workspace.replace_bytes(name, base64.b64decode(target["content"]), expected["version"] if expected else None,
                            create_parents=create_parents, mode=target["mode"])
