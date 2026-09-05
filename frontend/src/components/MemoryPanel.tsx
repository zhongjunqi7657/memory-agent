import { BookOpen, Check, Clock3, RotateCcw, Trash2, X } from "lucide-react";
import type { Memory } from "../api";

function formatKind(kind: Memory["kind"]) {
  return kind === "semantic" ? "长期偏好" : "阶段经历";
}

function formatStatus(status: Memory["status"]) {
  if (status === "pending") return "待确认";
  if (status === "active") return "已生效";
  if (status === "rejected") return "已拒绝";
  return status;
}

type MemoryPanelProps = {
  memories: Memory[];
  onClose: () => void;
  onRefresh: () => void;
  onUpdate: (id: string, status: "active" | "rejected" | "deleted") => void;
};

export function MemoryPanel({ memories, onClose, onRefresh, onUpdate }: MemoryPanelProps) {
  const pendingCount = memories.filter((memory) => memory.status === "pending").length;

  return (
    <aside className="memory-panel">
      <div className="memory-header">
        <div><div className="section-kicker">长期上下文</div><h2>我的记忆</h2></div>
        <div className="memory-header-actions">
          <span className="memory-count">{memories.length}</span>
          <button className="icon-button memory-close" aria-label="关闭记忆面板" onClick={onClose}><X size={17} /></button>
        </div>
      </div>
      <p className="memory-intro">这里保存的是你明确表达、且未来可能有帮助的信息。</p>
      {pendingCount > 0 && <div className="pending-summary"><Clock3 size={15} /><span>{pendingCount} 条记忆等待确认</span></div>}
      <div className="memory-list">
        {memories.length === 0 ? (
          <div className="memory-empty"><BookOpen size={20} /><strong>还没有长期记忆</strong><span>聊几次之后，这里会出现可复用的目标、偏好和阶段经历。</span></div>
        ) : memories.map((memory) => (
          <article className="memory-item" key={memory.id}>
            <div className="memory-item-top"><span className="memory-kind">{formatKind(memory.kind)}</span><span className={`memory-status status-${memory.status}`}>{formatStatus(memory.status)}</span></div>
            <p>{memory.content}</p>
            {typeof memory.metadata.reason === "string" && <div className="memory-reason">{memory.metadata.reason}</div>}
            {memory.status === "pending" && <div className="memory-actions"><button onClick={() => onUpdate(memory.id, "active")}><Check size={14} />确认</button><button onClick={() => onUpdate(memory.id, "rejected")}><Trash2 size={14} />拒绝</button></div>}
            {memory.status === "active" && <button className="memory-delete" aria-label="删除记忆" onClick={() => onUpdate(memory.id, "deleted")}><Trash2 size={14} /></button>}
          </article>
        ))}
      </div>
      <button className="refresh-button" onClick={onRefresh}><RotateCcw size={14} />刷新记忆</button>
    </aside>
  );
}
