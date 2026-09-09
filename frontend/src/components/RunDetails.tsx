import { BookOpen, ChevronDown } from "lucide-react";
import type { RunEvent } from "../api";

type RetrievedMemory = {
  id: string;
  content: string;
  kind: string;
  score: number;
  keyword_score?: number;
  vector_score?: number;
  recency_score?: number;
  importance_score?: number;
  type_score?: number;
  reason: string;
};

type MemoryChange = {
  id: string;
  content: string;
  status: string;
  action: "created" | "pending" | "conflict_pending" | "evidence_merged";
  conflicts_with?: string[];
};

const memoryChangeLabels: Record<MemoryChange["action"], string> = {
  created: "已新增",
  pending: "待确认",
  conflict_pending: "冲突待确认",
  evidence_merged: "已合并证据",
};

const eventLabels: Record<string, string> = {
  "run.started": "运行已创建",
  "memory.embedding_ready": "语义向量已生成",
  "memory.embedding_fallback": "已降级为关键词召回",
  "memory.retrieved": "长期记忆召回",
  "session.loaded": "会话上下文已载入",
  "session.summary_updated": "会话摘要已更新",
  "tool.started": "工具开始执行",
  "tool.completed": "工具执行完成",
  "model.completed": "回答已生成",
  "memory.extraction_queued": "记忆提取已入队",
  "memory.command_applied": "记忆指令已处理",
  "memory.extraction_completed": "记忆提取已完成",
  "memory.extraction_failed": "记忆提取失败",
  "run.completed": "运行已完成",
  "run.failed": "运行失败",
};

function eventDescription(event: RunEvent) {
  if (event.event_type === "memory.embedding_ready") {
    return `${String(event.payload.dimensions ?? 0)} 维`;
  }
  if (event.event_type === "memory.command_applied") {
    return `${String(event.payload.command ?? "记忆操作")} · ${String(event.payload.outcome ?? "已处理")}`;
  }
  if (event.event_type === "memory.retrieved") {
    return `${String(event.payload.count ?? 0)} 条匹配`;
  }
  if (event.event_type === "tool.started" || event.event_type === "tool.completed") {
    return String(event.payload.tool ?? "记忆工具");
  }
  if (event.event_type === "session.loaded") {
    return `${String(event.payload.message_count ?? 0)} 条消息 · 约 ${String(event.payload.estimated_tokens ?? 0)} tokens`;
  }
  if (event.event_type === "memory.extraction_completed") {
    const changes = Array.isArray(event.payload.changes) ? event.payload.changes.length : 0;
    return changes > 0 ? `${changes} 项记忆变化` : "未发现需要保存的记忆";
  }
  if (event.event_type === "memory.extraction_failed") {
    return `${String(event.payload.message ?? "记忆提取失败")} · 已尝试 ${String(event.payload.attempts ?? 0)} 次`;
  }
  return null;
}

export function RunDetails({ events }: { events: RunEvent[] }) {
  const retrieval = events.find((event) => event.event_type === "memory.retrieved");
  const memories = Array.isArray(retrieval?.payload.memories)
    ? (retrieval.payload.memories as RetrievedMemory[])
    : [];
  const extraction = events.find((event) => event.event_type === "memory.extraction_completed");
  const changes = Array.isArray(extraction?.payload.changes)
    ? (extraction.payload.changes as MemoryChange[])
    : [];

  return (
    <details className="run-details">
      <summary>
        <BookOpen size={14} />
        <span>查看本轮上下文与运行事件</span>
        <ChevronDown className="details-chevron" size={14} />
      </summary>
      <div className="run-details-body">
        {memories.length > 0 && (
          <section className="retrieval-section">
            <h3>本轮使用的记忆</h3>
            {memories.map((memory) => (
              <div className="retrieved-memory" key={memory.id}>
                <p>{memory.content}</p>
                <span>{memory.reason} · 相关度 {(memory.score * 100).toFixed(0)}%</span>
                <span className="score-breakdown">向量 {Math.round((memory.vector_score ?? 0) * 100)} · 关键词 {Math.round((memory.keyword_score ?? 0) * 100)} · 时间 {Math.round((memory.recency_score ?? 0) * 100)} · 重要度 {Math.round((memory.importance_score ?? 0) * 100)} · 类型 {Math.round((memory.type_score ?? 0) * 100)}</span>
              </div>
            ))}
          </section>
        )}
        {changes.length > 0 && <section className="memory-change-section"><h3>本轮记忆变化</h3>{changes.map((change) => <div key={change.id}><span>{memoryChangeLabels[change.action] ?? change.status}</span><p>{change.content}</p></div>)}</section>}
        <ol className="event-list">
          {events.map((event, index) => (
            <li key={`${event.sequence ?? index}-${event.event_type}`}>
              <span>{eventLabels[event.event_type] ?? event.event_type}</span>
              {eventDescription(event) && <small>{eventDescription(event)}</small>}
            </li>
          ))}
        </ol>
      </div>
    </details>
  );
}
