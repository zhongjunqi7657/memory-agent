// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { fetchMemories } from "./api";
import type { Memory } from "./api";
import { useScopedMemories } from "./useScopedMemories";

vi.mock("./api");
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
afterEach(() => vi.resetAllMocks());

it("hides old project data immediately and discards late responses after switching", async () => {
  const host = document.createElement("div");
  const root = createRoot(host);
  let completeA!: (items: Memory[]) => void;
  vi.mocked(fetchMemories).mockImplementation(async (id) => id === "a"
    ? new Promise((resolve) => { completeA = resolve; })
    : [{ id: "b", content: "项目 B 约定" } as Memory]);
  function View({ project }: { project: string }) {
    const { memories } = useScopedMemories(project, true);
    return <p>{memories.map((item) => item.content).join("，")}</p>;
  }
  try {
    await act(async () => root.render(<View project="a" />));
    await act(async () => root.render(<View project="b" />));
    expect(host.textContent).toBe("项目 B 约定");
    await act(async () => completeA([{ id: "a", content: "项目 A 私有信息" } as Memory]));
    expect(host.textContent).toBe("项目 B 约定");
    expect(fetchMemories).toHaveBeenCalledWith("b", true);
  } finally {
    await act(async () => root.unmount());
  }
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

it("keeps the newest refresh when requests for the same scope finish out of order", async () => {
  const first = deferred<Memory[]>();
  const second = deferred<Memory[]>();
  vi.mocked(fetchMemories).mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
  const root = createRoot(document.createElement("div"));
  let state!: ReturnType<typeof useScopedMemories>;
  function View() { state = useScopedMemories("a", false); return null; }
  try {
    await act(async () => root.render(<View />));
    let refresh!: Promise<Memory[]>;
    await act(async () => { refresh = state.loadMemories(); });
    await act(async () => { second.resolve([{ id: "new" } as Memory]); await refresh; });
    await act(async () => first.resolve([{ id: "old" } as Memory]));
    expect(state.memories.map((item) => item.id)).toEqual(["new"]);
    expect(state.loading).toBe(false);
    expect(state.error).toBe("");
  } finally { await act(async () => root.unmount()); }
});

it("shows a load failure and allows a successful refresh to recover", async () => {
  vi.mocked(fetchMemories).mockRejectedValueOnce(new Error("数据库暂不可用"))
    .mockResolvedValueOnce([{ id: "recovered" } as Memory]);
  const root = createRoot(document.createElement("div"));
  let state!: ReturnType<typeof useScopedMemories>;
  function View() { state = useScopedMemories(null, false); return null; }
  try {
    await act(async () => root.render(<View />));
    expect(state.loading).toBe(false);
    expect(state.error).toBe("数据库暂不可用");
    await act(async () => { await state.loadMemories(); });
    expect(state.memories.map((item) => item.id)).toEqual(["recovered"]);
    expect(state.error).toBe("");
  } finally { await act(async () => root.unmount()); }
});

it("ignores an old scope failure after a new project has loaded", async () => {
  const old = deferred<Memory[]>();
  vi.mocked(fetchMemories).mockReturnValueOnce(old.promise).mockResolvedValueOnce([{ id: "b" } as Memory]);
  const root = createRoot(document.createElement("div"));
  let state!: ReturnType<typeof useScopedMemories>;
  function View({ project }: { project: string }) { state = useScopedMemories(project, true); return null; }
  try {
    await act(async () => root.render(<View project="a" />));
    await act(async () => root.render(<View project="b" />));
    await act(async () => old.reject(new Error("项目 A 加载失败")));
    expect(state.memories.map((item) => item.id)).toEqual(["b"]);
    expect(state.error).toBe("");
    expect(state.loading).toBe(false);
  } finally { await act(async () => root.unmount()); }
});

it("invalidates an earlier visit when switching A to B and back to A", async () => {
  const old = deferred<Memory[]>();
  vi.mocked(fetchMemories).mockReturnValueOnce(old.promise).mockResolvedValueOnce([])
    .mockResolvedValueOnce([{ id: "fresh-a" } as Memory]);
  const root = createRoot(document.createElement("div"));
  let state!: ReturnType<typeof useScopedMemories>;
  function View({ project }: { project: string }) { state = useScopedMemories(project, false); return null; }
  try {
    await act(async () => root.render(<View project="a" />));
    await act(async () => root.render(<View project="b" />));
    await act(async () => root.render(<View project="a" />));
    await act(async () => old.resolve([{ id: "stale-a" } as Memory]));
    expect(state.memories.map((item) => item.id)).toEqual(["fresh-a"]);
  } finally { await act(async () => root.unmount()); }
});

it("treats including personal memories as a scope change", async () => {
  const combined = deferred<Memory[]>();
  vi.mocked(fetchMemories).mockReturnValueOnce(combined.promise)
    .mockResolvedValueOnce([{ id: "project-only" } as Memory]);
  const root = createRoot(document.createElement("div"));
  let state!: ReturnType<typeof useScopedMemories>;
  function View({ combined }: { combined: boolean }) { state = useScopedMemories("a", combined); return null; }
  try {
    await act(async () => root.render(<View combined />));
    await act(async () => root.render(<View combined={false} />));
    await act(async () => combined.resolve([{ id: "personal" } as Memory]));
    expect(state.memories.map((item) => item.id)).toEqual(["project-only"]);
    expect(fetchMemories).toHaveBeenLastCalledWith("a", false);
  } finally { await act(async () => root.unmount()); }
});
