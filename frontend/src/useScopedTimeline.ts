import { useCallback, useRef, useState } from "react";
import { fetchTimeline, generateReview } from "./api";
import type { TimelineItem } from "./api";

// Each visit has its own identity, including when returning to the same project.
export function useScopedTimeline(projectId: string | null) {
  const scope = useRef({ projectId });
  if (scope.current.projectId !== projectId) scope.current = { projectId };
  const timelineRequest = useRef(0);
  const reviewRequest = useRef(0);
  const [timeline, setTimeline] = useState({ scope: scope.current, items: [] as TimelineItem[], loading: false, error: "" });
  const [review, setReview] = useState({ scope: scope.current, content: null as string | null, loading: false, error: "" });

  const loadTimeline = useCallback(async () => {
    const current = scope.current;
    const sequence = ++timelineRequest.current;
    setTimeline((state) => ({ scope: current, items: state.scope === current ? state.items : [], loading: true, error: "" }));
    try {
      const items = await fetchTimeline(current.projectId);
      if (current === scope.current && sequence === timelineRequest.current) {
        setTimeline({ scope: current, items, loading: false, error: "" });
      }
    } catch (error) {
      if (current === scope.current && sequence === timelineRequest.current) {
        setTimeline((state) => ({ ...state, loading: false, error: error instanceof Error ? error.message : "时间线加载失败" }));
      }
    }
  }, []);

  const loadReview = useCallback(async (periodDays: number) => {
    const current = scope.current;
    const sequence = ++reviewRequest.current;
    setReview((state) => ({ scope: current, content: state.scope === current ? state.content : null, loading: true, error: "" }));
    try {
      const result = await generateReview(periodDays, current.projectId);
      if (current === scope.current && sequence === reviewRequest.current) {
        setReview({ scope: current, content: result.content, loading: false, error: "" });
      }
    } catch (error) {
      if (current === scope.current && sequence === reviewRequest.current) {
        setReview((state) => ({ ...state, loading: false, error: error instanceof Error ? error.message : "回顾生成失败" }));
      }
    }
  }, []);

  return {
    timeline: timeline.scope === scope.current ? timeline.items : [],
    timelineLoading: timeline.scope === scope.current && timeline.loading,
    review: review.scope === scope.current ? review.content : null,
    reviewLoading: review.scope === scope.current && review.loading,
    error: (timeline.scope === scope.current ? timeline.error : "") || (review.scope === scope.current ? review.error : ""),
    loadTimeline, loadReview,
  };
}
