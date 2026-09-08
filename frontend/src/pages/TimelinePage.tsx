import { CalendarRange, RotateCcw, Sparkles } from "lucide-react";
import type { TimelineItem } from "../api";

type Props = {
  items: TimelineItem[];
  loading: boolean;
  review: string | null;
  reviewLoading: boolean;
  periodDays: number;
  onPeriodChange: (days: number) => void;
  onRefresh: () => void;
  onGenerateReview: () => void;
};

function formatDate(value: string | null) {
  return new Date(value ?? Date.now()).toLocaleDateString("zh-CN", {
    year: "numeric", month: "short", day: "numeric",
  });
}

export function TimelinePage({
  items,
  loading,
  review,
  reviewLoading,
  periodDays,
  onPeriodChange,
  onRefresh,
  onGenerateReview,
}: Props) {
  return (
    <section className="workspace-page timeline-page">
      <header className="page-heading">
        <div><h1>时间线与回顾</h1><p>按时间查看重要经历，并在需要时生成阶段回顾。</p></div>
        <button className="secondary-button" onClick={onRefresh}><RotateCcw size={15} />刷新</button>
      </header>

      <div className="review-toolbar">
        <div className="segmented-control" aria-label="回顾周期">
          {[7, 30, 90].map((days) => <button key={days} className={periodDays === days ? "selected" : ""} onClick={() => onPeriodChange(days)}>最近 {days} 天</button>)}
        </div>
        <button className="primary-text-button" disabled={reviewLoading} onClick={onGenerateReview}><Sparkles size={15} />{reviewLoading ? "正在生成" : "生成回顾"}</button>
      </div>

      {review && <section className="review-output"><h2>阶段回顾</h2><div>{review}</div></section>}

      <div className="timeline-list">
        {loading ? <div className="page-empty">正在加载时间线…</div> : items.length === 0 ? (
          <div className="page-empty"><CalendarRange size={20} />还没有可展示的有效记忆。</div>
        ) : items.map((item) => (
          <article className="timeline-row" key={item.id}>
            <time>{formatDate(item.valid_from ?? item.created_at)}</time>
            <div><div className="timeline-kind">{item.kind === "semantic" ? "长期偏好" : "阶段经历"}</div><p>{item.content}</p></div>
          </article>
        ))}
      </div>
    </section>
  );
}
