import { useEffect, useState } from "react";
import { cancelRun, fetchRunEvents } from "../api";
import type { RunEvent } from "../api";
import { decideApproval, fetchApprovals, fetchRun, fetchTaskChanges } from "../taskApi";
import type { Approval, TaskChanges as Changes, TaskRun } from "../taskApi";
import { mergeRunEvents } from "../runEvents";
import { TaskChanges } from "./TaskChanges";
import { getRunOutput } from "../modelOutput";
import { MarkdownContent } from "./MarkdownContent";

const terminal = new Set(["completed", "failed", "cancelled", "interrupted"]);
const statuses: Record<string, string> = { queued: "等待执行", running: "正在执行", waiting_approval: "等待批准", completed: "已完成", failed: "执行失败", cancelled: "已停止", interrupted: "运行已中断" };

function ApprovalRequest({ approval, onDecided }: { approval: Approval; onDecided: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const args = approval.binding.arguments as { executable?: string; args?: string[]; path?: string } | undefined;
  const command = args?.executable ? [args.executable, ...(args.args ?? [])].map((part) => JSON.stringify(part)).join(" ") : null;
  async function decide(decision: "approve_once" | "approve_session" | "reject") {
    setBusy(true);
    setError(null);
    try { await decideApproval(approval.id, approval.fingerprint, decision); onDecided(); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "审批失败"); }
    finally { setBusy(false); }
  }
  return <section className="chat-approval" aria-label="工具审批"><p>{command ? "需要批准执行命令" : `需要批准：${approval.tool_id}`}</p>
    <span>执行目录：{String(approval.binding.directory ?? "无本地目录")}</span>
    <pre>{command ?? JSON.stringify(approval.binding.arguments, null, 2)}</pre>
    <p>{approval.warning}</p>
    <div className="row-actions"><button className="primary-text-button" disabled={busy} onClick={() => void decide("approve_once")}>批准本次</button><button className="secondary-button" disabled={busy} onClick={() => void decide("reject")}>拒绝</button></div>
    {approval.session_allowed && <details><summary>更多授权选项</summary><p>本次会话允许相同目录和参数的命令；取消任务或重启后失效。</p><button disabled={busy} onClick={() => void decide("approve_session")}>本次会话允许相同命令</button></details>}
    {error && <p role="alert">{error}</p>}
  </section>;
}

type Props = { runId: string; events?: RunEvent[]; live?: boolean; showOutput?: boolean; onStop?: () => void; onRecovered?: () => void };

export function ChatRun({ runId, events = [], live = false, showOutput = false, onStop, onRecovered }: Props) {
  const [run, setRun] = useState<TaskRun | null>(null);
  const [recorded, setRecorded] = useState<RunEvent[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [changes, setChanges] = useState<Changes | null>(null);
  const [changesError, setChangesError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    let sequence = 0;
    async function refresh() {
      try {
        const state = await fetchRun(runId);
        const incoming = await fetchRunEvents(runId, sequence);
        if (!active) return;
        sequence = Math.max(sequence, ...incoming.map((event) => event.sequence ?? 0));
        setRun(state);
        setRecorded((current) => mergeRunEvents(runId, current, incoming));
        setError(null);
        if (!terminal.has(state.status)) {
          const pending = await fetchApprovals(runId);
          if (active) setApprovals(pending);
        } else {
          setApprovals([]);
          if (state.project_id) {
            try { const diff = await fetchTaskChanges(runId); if (active) { setChanges(diff); setChangesError(null); } }
            catch (failure) { if (active) setChangesError(failure instanceof Error ? failure.message : "无法读取文件修改"); }
          }
          if (active) onRecovered?.();
        }
        if (active && (!terminal.has(state.status) || incoming.length === 200)) timer = setTimeout(() => void refresh(), 1000);
      } catch (failure) {
        if (active) {
          setError(failure instanceof Error ? failure.message : "运行状态恢复失败");
          timer = setTimeout(() => void refresh(), 3000);
        }
      }
    }
    void refresh();
    return () => { active = false; clearTimeout(timer); };
  }, [runId, revision, onRecovered]);

  const merged = mergeRunEvents(runId, recorded, events);
  const commands = merged.filter((event) => event.payload.command_result);
  async function stop() {
    if (onStop) { onStop(); return; }
    try { await cancelRun(runId); setRevision((value) => value + 1); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "停止失败"); }
  }
  return <>
    {showOutput && <MarkdownContent content={getRunOutput(merged)} />}
    <div className="chat-run" aria-label="本轮任务">
    {(live || (run && !terminal.has(run.status))) && <div className="chat-run-state"><span>{statuses[run?.status ?? "running"]}</span><button onClick={() => void stop()}>停止任务</button></div>}
    {run && ["failed", "cancelled", "interrupted"].includes(run.status) && <p>{statuses[run.status]}</p>}
    {approvals.map((approval) => <ApprovalRequest key={approval.id} approval={approval} onDecided={() => { setApprovals((current) => current.filter((item) => item.id !== approval.id)); setRevision((value) => value + 1); }} />)}
    {commands.length > 0 && <details className="command-results"><summary>执行了 {commands.length} 条命令 · 查看输出</summary>{commands.map((event, index) => {
      const result = event.payload.command_result as { exit_code?: number; stdout?: string; stderr?: string; truncated?: boolean; timed_out?: boolean };
      return <section key={event.sequence ?? index}><p>命令 {index + 1} · 退出码 {result.exit_code ?? "未返回"}{result.timed_out && " · 执行超时"}</p><pre>{[result.stdout, result.stderr].filter(Boolean).join("\n") || "无输出"}</pre>{result.truncated && <p>输出已截断</p>}</section>;
    })}</details>}
    {changes && (changes.changes.length > 0 || changes.external_effects || changes.conflicts.length > 0) && <TaskChanges runId={runId} initial={changes} />}
    {changesError && <details><summary>本轮文件修改不可用</summary><p>{changesError}</p></details>}
    {error && <p role="alert">{error}</p>}
  </div></>;
}
