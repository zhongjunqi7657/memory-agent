"""Persist intent before each file mutation; only proven writes can be undone."""

import base64
import hashlib
import json
from copy import deepcopy

from app.persistence.workspaces import TaskSnapshot
from app.tools.local.files import FileWorkspace, LocalFileError
from app.workspace.snapshots import capture, changes, file_state, matches, restore


def review_token(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


class ChangeJournal:
    def __init__(self, session, snapshot, config):
        self.session, self.snapshot, self.config = session, snapshot, config

    @classmethod
    async def start(cls, session, run, config):
        baseline = capture(run.execution_directory, config)
        snapshot = TaskSnapshot(run_id=run.id, directory=run.execution_directory, baseline=baseline, edits={})
        session.add(snapshot)
        await session.commit()
        return cls(session, snapshot, config)

    async def external_started(self):
        # Arbitrary commands/MCP can write concurrently or spawn remote effects.
        # No OS provenance is available, so do not promote observations to owned.
        self.snapshot.external_effects = True
        edits = deepcopy(self.snapshot.edits)
        for item in edits.values():
            if item["status"] == "applied":
                item["status"] = "unverified"
        self.snapshot.edits = edits
        await self.session.commit()

    async def mutate(self, workspace, operation, values):
        name = workspace.path(values["value"]).resolve().relative_to(workspace.root).as_posix()
        before = file_state(workspace, name)
        if (before["version"] if before else None) != values["expected_version"]:
            raise LocalFileError("文件版本已改变，请重新读取")
        if before and before["content"] is None:
            raise LocalFileError("含秘密的文件无法建立安全撤销快照")
        edits = deepcopy(self.snapshot.edits)
        previous = edits.get(name)
        proven = matches(before, previous["after"] if previous else self.snapshot.baseline["files"].get(name))
        proven = proven and (previous is None or previous["status"] == "applied")
        # Never claim ownership of changes after an arbitrary external executor.
        proven = proven and not self.snapshot.external_effects
        edits[name] = {"before": previous["before"] if previous else before, "after": before,
                       "status": "pending", "proven": proven}
        projected = deepcopy(edits)
        if operation == "write":
            data = workspace.prepare_write(**values)
            projected[name]["after"] = {"content": base64.b64encode(data).decode("ascii"),
                                         "version": "0" * 64, "mode": before["mode"] if before else 0o666}
        else:
            projected[name]["after"] = None
        if max(len(json.dumps(edits)), len(json.dumps(projected))) > self.config.max_snapshot_bytes:
            raise LocalFileError("任务撤销快照超过容量上限")
        self.snapshot.edits = edits
        await self.session.commit()
        # A crash or failed write leaves durable pending evidence, never a
        # falsely successful reversible edit. Each mutation rechecks its version.
        result = getattr(workspace, operation)(**values)
        after = file_state(workspace, name)
        own_after = after is None if operation == "delete" else bool(
            after and after["version"] == result["version"] and (before is None or after["mode"] == before["mode"]))
        edits = deepcopy(edits)
        edits[name].update(after=after, status="applied" if proven and own_after else "unverified")
        self.snapshot.edits = edits
        await self.session.commit()
        return result

    def preview(self):
        current = capture(self.snapshot.directory, self.config)
        result = changes(self.snapshot.baseline, current, self.config)
        for item in result:
            entry = self.snapshot.edits.get(item["path"])
            item["attribution"] = "file_tool" if entry and entry["status"] == "applied" else "unverified"
            item["undoable"] = bool(entry and entry["status"] == "applied" and
                                     matches(current["files"].get(item["path"]), entry["after"]))
            if entry and not matches(current["files"].get(item["path"]), entry["after"]):
                item["attribution"] = "external_conflict"
        return {"run_id": str(self.snapshot.run_id), "changes": result, "status": self.snapshot.status,
                "external_effects": self.snapshot.external_effects,
                "excluded": current["excluded"], "baseline_excluded": self.snapshot.baseline["excluded"],
                "fingerprint": review_token([self.snapshot.status, self.snapshot.external_effects, self.snapshot.edits, current]),
                "limitations": "仅覆盖受支持的本地文本文件；命令、MCP、网络及其他进程副作用不承诺撤销"}

    async def undo(self, token):
        preview = self.preview()
        if token != preview["fingerprint"]:
            raise LocalFileError("Diff 已改变，请重新检查后再撤销")
        if self.snapshot.external_effects:
            raise LocalFileError("任务包含归属不明的外部执行，不能确认安全撤销")
        workspace = FileWorkspace(self.snapshot.directory, self.config)
        edits = deepcopy(self.snapshot.edits)
        for name, item in edits.items():
            if item["status"] == "undone":
                continue
            if item["status"] != "applied" or not matches(file_state(workspace, name), item["after"]):
                raise LocalFileError("存在归属不明或外部修改冲突，停止撤销")
        self.snapshot.status = "undoing"
        await self.session.commit()
        for name, item in edits.items():
            if item["status"] == "undone":
                continue
            # Persist intent first. Interrupted undo is never silently repeated.
            item["status"] = "undo_pending"
            self.snapshot.edits = deepcopy(edits)
            await self.session.commit()
            restore(workspace, name, item["before"], item["after"])
            item["status"] = "undone"
            self.snapshot.edits = deepcopy(edits)
            await self.session.commit()
        self.snapshot.status = "undone"
        await self.session.commit()
        return self.preview()
