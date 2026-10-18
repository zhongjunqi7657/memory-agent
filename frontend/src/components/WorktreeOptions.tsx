import { useEffect, useState } from "react";
import type { TaskExecutionConfig } from "../api";
import { applyWorktree, createWorktree, fetchWorktrees, previewWorktree } from "../taskApi";
import type { Worktree, WorktreePreview } from "../taskApi";

type Props = {
  config: TaskExecutionConfig;
  locked: boolean;
  onChange: (config: TaskExecutionConfig) => void;
  onError: (message: string) => void;
};

export function WorktreeOptions({ config, locked, onChange, onError }: Props) {
  const [worktrees, setWorktrees] = useState<Worktree[]>([]);
  const [busy, setBusy] = useState(false);
  const [startMode, setStartMode] = useState<"head" | "snapshot">("head");
  const [preview, setPreview] = useState<{ id: string; operation: "delivery" | "cleanup"; data: WorktreePreview } | null>(null);
  const selected = worktrees.find((item) => item.id === config.worktreeId);

  useEffect(() => {
    let active = true;
    setPreview(null);
    setWorktrees([]);
    if (config.projectId) void fetchWorktrees(config.projectId).then((loaded) => { if (active) setWorktrees(loaded); })
      .catch((error: Error) => { if (active) onError(error.message); });
    return () => { active = false; };
  }, [config.projectId, config.worktreeId, onError]);

  async function perform(action: () => Promise<void>) {
    setBusy(true);
    try { await action(); }
    catch (error) { onError(error instanceof Error ? error.message : "Worktree 操作失败"); }
    finally { setBusy(false); }
  }

  return <details className="worktree-options"><summary>高级选项{config.executionMode === "worktree" ? " · Worktree" : " · 原目录"}</summary>
    <p>默认在原目录工作。隔离修改时选择 Worktree；发送后执行目录固定。</p>
    <label>执行目录<select aria-label="执行目录" disabled={locked || busy} value={config.worktreeId ?? ""} onChange={(event) => onChange({ ...config, executionMode: event.target.value ? "worktree" : "original", worktreeId: event.target.value || null })}>
      <option value="">原目录</option>{worktrees.filter((item) => ["ready", "delivered"].includes(item.status)).map((item) => <option key={item.id} value={item.id}>{item.directory}</option>)}
    </select></label>
    {!locked && <><label>新建起点<select aria-label="Worktree 起点" disabled={busy} value={startMode} onChange={(event) => setStartMode(event.target.value as "head" | "snapshot")}><option value="head">最新提交</option><option value="snapshot">当前已跟踪文件快照</option></select></label><p>快照不带入未跟踪文件；省略的文件会在创建后列出。</p><button disabled={busy} onClick={() => void perform(async () => {
      const created = await createWorktree(config.projectId!, startMode);
      setWorktrees((items) => [created, ...items]);
      onChange({ ...config, executionMode: "worktree", worktreeId: created.id });
    })}>新建并使用 Worktree</button></>}
    {selected && <><p>{selected.directory}</p>{selected.omitted.length > 0 && <p>未带入：{selected.omitted.join("、")}</p>}
      <button disabled={busy} onClick={() => void perform(async () => setPreview({ id: selected.id, operation: "delivery", data: await previewWorktree(selected.id, "delivery") }))}>预览带回原目录</button>
      <button disabled={busy || locked} title={locked ? "新建对话后才能清理当前 Worktree" : undefined} onClick={() => void perform(async () => setPreview({ id: selected.id, operation: "cleanup", data: await previewWorktree(selected.id, "cleanup") }))}>预览清理</button>
    </>}
    {preview && <div className="worktree-preview"><p>{preview.operation === "delivery" ? "带回原目录" : "清理 Worktree"}预览</p>
      {Array.isArray(preview.data.changes) && (preview.data.changes as {path:string;diff:string}[]).map((change) => <details key={change.path}><summary>{change.path}</summary><pre>{change.diff}</pre></details>)}
      {preview.data.conflicts.map((conflict) => <p key={conflict}>{conflict}</p>)}
      {preview.data.conflicts.length === 0 && <button disabled={busy} onClick={() => void perform(async () => {
        await applyWorktree(preview.id, preview.operation, preview.data.fingerprint);
        if (preview.operation === "cleanup") onChange({ ...config, executionMode: "original", worktreeId: null });
        setPreview(null);
        setWorktrees(await fetchWorktrees(config.projectId!));
      })}>确认{preview.operation === "delivery" ? "带回" : "清理"}</button>}
      <button disabled={busy} onClick={() => setPreview(null)}>取消</button>
    </div>}
  </details>;
}
