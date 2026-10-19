// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import type { Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { undoTask } from "../taskApi";
import { TaskChanges } from "./TaskChanges";

vi.mock("../taskApi");
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
let host: HTMLDivElement; let root: Root;
const changes = { fingerprint: "token", changes: [{ path: "code.py", before_version: "old", after_version: "new", diff: "-old\n+new", truncated: true, undoable: true, attribution: "file_tool" }], conflicts: [], external_effects: false };
beforeEach(() => { vi.resetAllMocks(); host = document.createElement("div"); document.body.appendChild(host); root = createRoot(host); });
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });

it("keeps the diff collapsed and requires confirmation before undo", async () => {
  vi.mocked(undoTask).mockResolvedValue({ ...changes, changes: [] });
  await act(async () => root.render(<TaskChanges runId="run" initial={changes} />));
  expect(host.querySelector('details')!.open).toBe(false);
  expect(host.textContent).toContain("修改了 1 个文件");
  expect(host.textContent).toContain("Diff 已截断");
  await act(async () => host.querySelector('button')!.click());
  expect(undoTask).not.toHaveBeenCalled();
  await act(async () => [...host.querySelectorAll('button')].find((button) => button.textContent === "确认撤销")!.click());
  expect(undoTask).toHaveBeenCalledWith("run", "token");
  expect(host.textContent).toContain("修改了 0 个文件");
});

it("explains external effects and prevents automatic undo", async () => {
  await act(async () => root.render(<TaskChanges runId="run" initial={{ ...changes, external_effects: true }} />));
  expect(host.textContent).toContain("不支持自动撤销");
  expect(host.querySelector('button')).toBeNull();
});
