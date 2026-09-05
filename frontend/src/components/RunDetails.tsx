import { BookOpen, ChevronDown } from "lucide-react";
import type { RunEvent } from "../api";

type RetrievedMemory = {
  id: string;
  content: string;
  kind: string;
  score: number;
  reason: string;
};

const eventLabels: Record<string, string> = {
  "run.started": "运行已创建",
  "memory.embedding_ready": "语义向量已生成",
  "memory.embedding_fallback": "已降级为关键词召回",
  "memory.retrieved": "长期记忆召回",
  "model.completed": "回答已生成",
  "memory.extraction_queued": "记忆提取已入队",
  "memory.command_applied": "记忆指令已处理",
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
  return null;
}

export function RunDetails({ events }: { events: RunEvent[] }) {
  const retrieval = events.find((event) => event.event_type === "memory.retrieved");
  const memories = Array.isArray(retrieval?.payload.memories)
    ? (retrieval.payload.memories as RetrievedMemory[])
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
              </div>
            ))}
          </section>
        )}
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
