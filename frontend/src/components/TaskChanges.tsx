import { useState } from "react";
import { undoTask } from "../taskApi";
import type { TaskChanges as Changes } from "../taskApi";

export function TaskChanges({ runId, initial }: { runId: string; initial: Changes }) {
  const [changes, setChanges] = useState(initial);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canUndo = !changes.external_effects && changes.conflicts.length === 0 && changes.changes.some((item) => item.undoable);
  async function undo() {
    setBusy(true);
    setError(null);
    try { setChanges(await undoTask(runId, changes.fingerprint)); setConfirm(false); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "撤销失败"); }
    finally { setBusy(false); }
  }
  return <details className="chat-changes"><summary>修改了 {changes.changes.length} 个文件 · 查看 Diff</summary>
    {changes.changes.map((change) => <details className="diff-row" key={change.path}><summary>{change.path}<small>{!change.before_version ? "新增" : !change.after_version ? "删除" : "修改"}</small></summary><pre>{change.diff || "此文件没有可展示的文本 Diff"}</pre>{change.truncated && <p>此文件 Diff 已截断。</p>}</details>)}
    {changes.external_effects && <p>本轮执行过命令或外部工具，无法确认全部修改归属，不支持自动撤销。</p>}
    {changes.conflicts.map((conflict) => <p key={conflict}>{conflict}</p>)}
    {changes.truncated && <p>Diff 已截断，仍可能存在未展示的修改。</p>}
    {changes.excluded && changes.excluded.length > 0 && <p>未展示：{changes.excluded.join("、")}</p>}
    {!canUndo && !changes.external_effects && changes.conflicts.length === 0 && changes.changes.length > 0 && <p>当前修改无法安全归属于本轮，不支持自动撤销。</p>}
    {canUndo && !confirm && <button className="secondary-button" onClick={() => setConfirm(true)}>撤销本轮修改</button>}
    {confirm && <div className="inline-confirm"><span>确认撤销本轮文件修改？执行前会重新检查版本。</span><button disabled={busy} onClick={() => void undo()}>确认撤销</button><button disabled={busy} onClick={() => setConfirm(false)}>取消</button></div>}
    {error && <p role="alert">{error}</p>}
  </details>;
}
