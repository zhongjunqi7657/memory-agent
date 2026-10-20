// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { fetchTimeline, generateReview } from "./api";
import type { TimelineItem } from "./api";
import { useScopedTimeline } from "./useScopedTimeline";

vi.mock("./api");
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
afterEach(() => vi.resetAllMocks());

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

it("keeps a newer timeline loading when an earlier refresh completes", async () => {
  const first = deferred<TimelineItem[]>();
  const second = deferred<TimelineItem[]>();
  vi.mocked(fetchTimeline).mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
  const root = createRoot(document.createElement("div"));
  let state!: ReturnType<typeof useScopedTimeline>;
  function View() { state = useScopedTimeline("a"); return null; }
  try {
    await act(async () => root.render(<View />));
    let earlier!: Promise<void>;
    let latest!: Promise<void>;
    await act(async () => { earlier = state.loadTimeline(); latest = state.loadTimeline(); });
    await act(async () => { first.resolve([{ id: "old" } as TimelineItem]); await earlier; });
    expect(state.timelineLoading).toBe(true);
    expect(state.timeline).toEqual([]);
    await act(async () => { second.resolve([{ id: "new" } as TimelineItem]); await latest; });
    expect(state.timelineLoading).toBe(false);
    expect(state.timeline.map((item) => item.id)).toEqual(["new"]);
  } finally { await act(async () => root.unmount()); }
});

it("shows the current timeline error and clears it after a successful retry", async () => {
  vi.mocked(fetchTimeline).mockRejectedValueOnce(new Error("当前项目时间线不可用"))
    .mockResolvedValueOnce([{ id: "restored" } as TimelineItem]);
  const root = createRoot(document.createElement("div"));
  let state!: ReturnType<typeof useScopedTimeline>;
  function View() { state = useScopedTimeline("a"); return null; }
  try {
    await act(async () => root.render(<View />));
    await act(async () => { await state.loadTimeline(); });
    expect(state.error).toBe("当前项目时间线不可用");
    expect(state.timelineLoading).toBe(false);
    await act(async () => { await state.loadTimeline(); });
    expect(state.error).toBe("");
    expect(state.timeline.map((item) => item.id)).toEqual(["restored"]);
  } finally { await act(async () => root.unmount()); }
});

it("allows a review in the new scope while ignoring the previous scope completion", async () => {
  const first = deferred<{ content: string; period_days: number }>();
  const second = deferred<{ content: string; period_days: number }>();
  vi.mocked(generateReview).mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
  const root = createRoot(document.createElement("div"));
  let state!: ReturnType<typeof useScopedTimeline>;
  function View({ project }: { project: string }) { state = useScopedTimeline(project); return null; }
  try {
    await act(async () => root.render(<View project="a" />));
    let earlier!: Promise<void>;
    let latest!: Promise<void>;
    await act(async () => { earlier = state.loadReview(7); });
    await act(async () => root.render(<View project="b" />));
    expect(state.reviewLoading).toBe(false);
    expect(state.review).toBeNull();
    await act(async () => { latest = state.loadReview(30); });
    await act(async () => { first.resolve({ content: "项目 A 回顾", period_days: 7 }); await earlier; });
    expect(state.review).toBeNull();
    expect(state.reviewLoading).toBe(true);
    await act(async () => { second.resolve({ content: "项目 B 回顾", period_days: 30 }); await latest; });
    expect(state.review).toBe("项目 B 回顾");
    expect(generateReview).toHaveBeenLastCalledWith(30, "b");
  } finally { await act(async () => root.unmount()); }
});

it("discards a successful old review after switching A to B and back to A", async () => {
  const first = deferred<{ content: string; period_days: number }>();
  vi.mocked(generateReview).mockReturnValueOnce(first.promise)
    .mockResolvedValueOnce({ content: "最新回顾", period_days: 7 });
  const root = createRoot(document.createElement("div"));
  let state!: ReturnType<typeof useScopedTimeline>;
  function View({ project }: { project: string }) { state = useScopedTimeline(project); return null; }
  try {
    await act(async () => root.render(<View project="a" />));
    let earlier!: Promise<void>;
    await act(async () => { earlier = state.loadReview(7); });
    await act(async () => root.render(<View project="b" />));
    await act(async () => root.render(<View project="a" />));
    await act(async () => { await state.loadReview(7); });
    await act(async () => { first.resolve({ content: "旧回顾", period_days: 7 }); await earlier; });
    expect(state.review).toBe("最新回顾");
    expect(state.reviewLoading).toBe(false);
  } finally { await act(async () => root.unmount()); }
});

it("reports only the newest review error and supports retry", async () => {
  const first = deferred<{ content: string; period_days: number }>();
  vi.mocked(generateReview).mockReturnValueOnce(first.promise)
    .mockRejectedValueOnce(new Error("本次回顾失败"))
    .mockResolvedValueOnce({ content: "重试成功", period_days: 30 });
  const root = createRoot(document.createElement("div"));
  let state!: ReturnType<typeof useScopedTimeline>;
  function View() { state = useScopedTimeline("a"); return null; }
  try {
    await act(async () => root.render(<View />));
    let earlier!: Promise<void>;
    await act(async () => { earlier = state.loadReview(7); });
    await act(async () => { await state.loadReview(30); });
    await act(async () => { first.reject(new Error("旧回顾失败")); await earlier; });
    expect(state.error).toBe("本次回顾失败");
    expect(state.reviewLoading).toBe(false);
    await act(async () => { await state.loadReview(30); });
    expect(state.error).toBe("");
    expect(state.review).toBe("重试成功");
  } finally { await act(async () => root.unmount()); }
});
