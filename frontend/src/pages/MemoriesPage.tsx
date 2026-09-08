import { useMemo, useState } from "react";
import { Check, Pencil, RotateCcw, Save, Trash2, X } from "lucide-react";
import type { Memory, MemoryKind, MemoryStatus, MemoryUpdate } from "../api";

type Props = {
  memories: Memory[];
  loading: boolean;
  onRefresh: () => void;
  onUpdate: (id: string, update: MemoryUpdate) => Promise<void>;
  onUndo: (id: string) => Promise<void>;
};

const statusLabels: Record<MemoryStatus, string> = {
  active: "已生效",
  pending: "待确认",
  superseded: "已替代",
  deleted: "已删除",
  rejected: "已拒绝",
};

export function MemoriesPage({ memories, loading, onRefresh, onUpdate, onUndo }: Props) {
  const [status, setStatus] = useState<MemoryStatus | "all">("all");
  const [kind, setKind] = useState<MemoryKind | "all">("all");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [importance, setImportance] = useState(0.5);

  const visible = useMemo(
    () => memories.filter((memory) => (
      (status === "all" || memory.status === status)
      && (kind === "all" || memory.kind === kind)
    )),
    [kind, memories, status],
  );

  function beginEdit(memory: Memory) {
    setEditingId(memory.id);
    setDraft(memory.content);
    setImportance(memory.importance);
  }

  async function save(memory: Memory) {
    await onUpdate(memory.id, { content: draft.trim(), importance });
    setEditingId(null);
  }

  return (
    <section className="workspace-page memories-page">
      <header className="page-heading">
        <div><h1>长期记忆</h1><p>查看来源、处理冲突，并控制哪些信息会用于后续回答。</p></div>
        <button className="secondary-button" onClick={onRefresh}><RotateCcw size={15} />刷新</button>
      </header>

      <div className="filter-bar" aria-label="记忆筛选">
        <div className="segmented-control">
          {(["all", "semantic", "episodic"] as const).map((value) => (
            <button key={value} className={kind === value ? "selected" : ""} onClick={() => setKind(value)}>
              {value === "all" ? "全部类型" : value === "semantic" ? "长期偏好" : "阶段经历"}
            </button>
          ))}
        </div>
        <label className="select-field">状态
          <select value={status} onChange={(event) => setStatus(event.target.value as MemoryStatus | "all")}>
            <option value="all">全部</option>
            {Object.entries(statusLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>
      </div>

      {loading ? <div className="page-empty">正在加载记忆…</div> : visible.length === 0 ? (
        <div className="page-empty">当前筛选条件下没有记忆。</div>
      ) : (
        <div className="governance-list">
          {visible.map((memory) => {
            const editing = editingId === memory.id;
            return (
              <article className="governance-row" key={memory.id}>
                <div className="memory-row-meta">
                  <span>{memory.kind === "semantic" ? "长期偏好" : "阶段经历"}</span>
                  <span className={`status-${memory.status}`}>{statusLabels[memory.status]}</span>
                  <span>重要度 {Math.round(memory.importance * 100)}%</span>
                </div>
                {editing ? (
                  <div className="memory-editor">
                    <textarea value={draft} onChange={(event) => setDraft(event.target.value)} rows={3} />
                    <label>重要度
                      <input type="range" min="0" max="1" step="0.05" value={importance} onChange={(event) => setImportance(Number(event.target.value))} />
                      <span>{Math.round(importance * 100)}%</span>
                    </label>
                  </div>
                ) : <p className="governance-content">{memory.content}</p>}
                <div className="memory-source-line">
                  来源 {memory.source_message_ids.length || (memory.source_message_id ? 1 : 0)} 条消息
                  {memory.conversation_id ? ` · 会话 ${memory.conversation_id.slice(0, 8)}` : ""}
                  {typeof memory.metadata.reason === "string" ? ` · ${memory.metadata.reason}` : ""}
                </div>
                <div className="row-actions">
                  {editing ? (
                    <><button onClick={() => void save(memory)} disabled={!draft.trim()}><Save size={15} />保存</button><button onClick={() => setEditingId(null)}><X size={15} />取消</button></>
                  ) : <button onClick={() => beginEdit(memory)}><Pencil size={15} />编辑</button>}
                  {memory.status === "pending" && <button className="confirm-action" onClick={() => void onUpdate(memory.id, { status: "active" })}><Check size={15} />确认</button>}
                  {memory.status === "pending" && <button onClick={() => void onUpdate(memory.id, { status: "rejected" })}><X size={15} />拒绝</button>}
                  {memory.status === "active" && <button className="danger-action" onClick={() => void onUpdate(memory.id, { status: "deleted" })}><Trash2 size={15} />删除</button>}
                  {typeof memory.metadata.undo === "object" && <button onClick={() => void onUndo(memory.id)}><RotateCcw size={15} />撤销</button>}
                </div>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
