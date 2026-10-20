// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import type { Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import * as api from "./api";
import * as tasks from "./taskApi";
import App from "./App";

vi.mock("./api");
vi.mock("./taskApi");
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
let host: HTMLDivElement;
let root: Root;
const project = { id: "project-a", name: "demo", directory: "C:/项目/demo", created_at: "today" };
const conversation = { id: "conversation-a", project_id: project.id, title: "项目旧对话", created_at: "2026-10-18", updated_at: "2026-10-18" };
const run = { run_id: "run-a", conversation_id: conversation.id, status: "completed", project_id: project.id, execution_mode: "original" as const, execution_directory: project.directory, worktree_id: null };
const publicConfig = { environment: "local", provider_configured: true,
  models: { chat: "test", embedding: "test", embedding_dimensions: 1024 },
  memory: { auto_retrieve_limit: 5, weights: {} }, agent: { context_token_budget: 6000, max_tool_rounds: 4, project_max_tool_rounds: 24 },
  checkpoint: { ready: true, error: null }, local: { enabled: true }, tools: { enabled: true } };

async function click(label: string) {
  const button = [...host.querySelectorAll("button")].find((item) => item.getAttribute("aria-label") === label || item.textContent?.trim() === label);
  expect(button, label).toBeDefined();
  await act(async () => button!.click());
}
async function input(text: string) {
  await act(async () => {
    const element = host.querySelector("textarea")!;
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(element, text);
    element.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
beforeEach(() => {
  vi.resetAllMocks();
  localStorage.clear();
  vi.mocked(api.fetchConversations).mockResolvedValue([]);
  vi.mocked(api.fetchMemories).mockResolvedValue([]);
  vi.mocked(api.fetchPublicConfig).mockResolvedValue(publicConfig);
  vi.mocked(api.fetchRunEvents).mockResolvedValue([]);
  vi.mocked(api.fetchConversationMessages).mockResolvedValue([]);
  vi.mocked(tasks.fetchProjects).mockResolvedValue([project]);
  vi.mocked(tasks.fetchWorktrees).mockResolvedValue([]);
  vi.mocked(tasks.fetchConversationRuns).mockResolvedValue([]);
  vi.mocked(tasks.fetchRun).mockResolvedValue(run);
  vi.mocked(tasks.fetchApprovals).mockResolvedValue([]);
  vi.mocked(tasks.fetchTaskChanges).mockResolvedValue({ fingerprint: "token", changes: [], conflicts: [], external_effects: false });
  host = document.createElement("div"); document.body.appendChild(host); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });

it("renders streamed Markdown before completion and replaces it with one final reply", async () => {
  let publish!: (event: api.RunEvent) => void;
  let finish!: (response: Awaited<ReturnType<typeof api.streamChat>>) => void;
  vi.mocked(api.streamChat).mockImplementation(async (_content, _id, onEvent) => {
    publish = onEvent!;
    return new Promise((resolve) => { finish = resolve; });
  });
  await act(async () => root.render(<App />));
  await input("生成一段代码"); await click("发送消息");
  await act(async () => {
    publish({ run_id: run.run_id, sequence: 1, event_type: "run.started", payload: { conversation_id: conversation.id } });
    publish({ run_id: run.run_id, sequence: 2, event_type: "model.output.started", payload: { output_id: "one" } });
    const delta = { run_id: run.run_id, sequence: 3, event_type: "model.output.delta", payload: { output_id: "one", text: "## 实时结果\n\n```python\nprint('好')" } };
    publish(delta); publish(delta);
  });
  expect(host.querySelector("h2")?.textContent).toBe("实时结果");
  expect(host.querySelector("pre code")?.textContent).toContain("print('好')");
  expect(host.querySelectorAll(".message-row.assistant h2")).toHaveLength(1);
  expect(host.querySelector('[aria-label="停止任务"]')).not.toBeNull();
  await act(async () => finish({ conversation_id: conversation.id, run_id: run.run_id,
    message: "## 最终结果\n\n```python\nprint('好')\n```", redacted: false, redaction_categories: [], memory_count: 0 }));
  expect(host.querySelector("h2")?.textContent).toBe("最终结果");
  expect(host.querySelectorAll(".message-row.assistant h2")).toHaveLength(1);
  expect(host.querySelector('[aria-label="发送消息"]')).not.toBeNull();
});

it("restores partial Markdown from persisted events for an interrupted run", async () => {
  vi.mocked(api.fetchConversations).mockResolvedValue([conversation]);
  vi.mocked(tasks.fetchConversationRuns).mockResolvedValue([{ ...run, status: "interrupted" }]);
  vi.mocked(tasks.fetchRun).mockResolvedValue({ ...run, status: "interrupted" });
  vi.mocked(api.fetchRunEvents).mockResolvedValue([
    { run_id: run.run_id, sequence: 1, event_type: "model.output.started", payload: { output_id: "one" } },
    { run_id: run.run_id, sequence: 2, event_type: "model.output.delta", payload: { output_id: "one", text: "## 已生成的部分\n\n- 保留输出" } },
    { run_id: run.run_id, sequence: 3, event_type: "run.interrupted", payload: {} },
  ]);
  await act(async () => root.render(<App />));
  expect(host.querySelector("h2")?.textContent).toBe("已生成的部分");
  expect(host.textContent).toContain("运行已中断");
  expect(api.streamChat).not.toHaveBeenCalled();
});

it("renders Markdown in a new assistant reply while keeping user input literal", async () => {
  vi.mocked(api.streamChat).mockResolvedValue({ conversation_id: conversation.id, run_id: run.run_id, message: "## 检查结果\n\n**已完成**\n\n| 项目 | 状态 |\n| --- | --- |\n| 格式 | 正常 |", redacted: false, redaction_categories: [], memory_count: 0 });
  await act(async () => root.render(<App />));
  await input("**保留用户原文**"); await click("发送消息");
  expect(host.querySelector(".message-row.assistant h2")?.textContent).toBe("检查结果");
  expect(host.querySelector(".message-row.assistant strong")?.textContent).toBe("已完成");
  expect(host.querySelector(".message-row.assistant td")?.textContent).toBe("格式");
  expect(host.querySelector(".message-row.user .message-text")?.textContent).toBe("**保留用户原文**");
  expect(host.querySelector(".message-row.user strong")).toBeNull();
});

it("renders Markdown when reopening stored assistant messages", async () => {
  vi.mocked(api.fetchConversations).mockResolvedValue([conversation]);
  vi.mocked(api.fetchConversationMessages).mockResolvedValue([
    { id: "stored-reply", role: "assistant", content: "## 历史结果\n\n- **修复完成**", sequence: 1, is_redacted: false, created_at: "2026-10-18", run_id: null, run_events: [] },
  ]);
  await act(async () => root.render(<App />));
  expect(host.querySelector(".message-row.assistant h2")?.textContent).toBe("历史结果");
  expect(host.querySelector(".message-row.assistant li strong")?.textContent).toBe("修复完成");
  expect(api.streamChat).not.toHaveBeenCalled();
});

it("adds a folder directly from the composer and sends the selected project", async () => {
  vi.mocked(tasks.chooseProjectDirectory).mockResolvedValue({ directory: project.directory });
  vi.mocked(tasks.createProject).mockResolvedValue(project);
  vi.mocked(api.streamChat).mockResolvedValue({ conversation_id: conversation.id, run_id: run.run_id, message: "done", redacted: false, redaction_categories: [], memory_count: 0 });
  await act(async () => root.render(<App />));
  expect(host.querySelector('.workspace-nav')!.textContent).not.toContain("编码任务");
  await click("选择项目"); await click("添加本地项目…");
  expect(tasks.createProject).toHaveBeenCalledWith("demo", project.directory);
  expect(host.querySelector('.project-trigger')!.textContent).toContain("demo");
  expect(host.textContent).not.toContain("绑定项目");
  await input("读取代码"); await click("发送消息");
  expect(api.streamChat).toHaveBeenCalledWith("读取代码", null, expect.any(Function), expect.any(AbortSignal), { projectId: project.id, executionMode: "original", worktreeId: null });
});

it("restores the history project and starts a fresh conversation when choosing a different project", async () => {
  vi.mocked(api.fetchConversations).mockResolvedValue([conversation]);
  vi.mocked(tasks.fetchConversationRuns).mockResolvedValue([run]);
  await act(async () => root.render(<App />));
  expect(host.querySelector('.project-trigger')!.textContent).toContain("demo");
  await click("选择项目");
  expect(host.textContent).toContain("切换项目将开启新对话");
  await click("普通聊天 · 不使用项目");
  expect(localStorage.getItem("memory-agent:selected-conversation")).toBeNull();
  vi.mocked(api.streamChat).mockResolvedValue({ conversation_id: "new-conversation", run_id: "new-run", message: "hello", redacted: false, redaction_categories: [], memory_count: 0 });
  await input("你好"); await click("发送消息");
  expect(api.streamChat).toHaveBeenLastCalledWith("你好", null, expect.any(Function), expect.any(AbortSignal), { projectId: null, executionMode: "original", worktreeId: null });
});

it("shows approval in the live reply and stopping waits for the backend terminal event", async () => {
  const approval = { id: "approval", run_id: run.run_id, call_id: "call", tool_id: "builtin.local.command", fingerprint: "token", binding: { directory: project.directory, arguments: { executable: "python", args: ["-m", "pytest"] } }, status: "pending", session_allowed: true, expires_at: "later", warning: "以本机用户权限运行" };
  vi.mocked(tasks.fetchRun).mockResolvedValue({ ...run, status: "waiting_approval" });
  vi.mocked(tasks.fetchApprovals).mockResolvedValue([approval]);
  let rejectStream!: (error: Error) => void;
  vi.mocked(api.streamChat).mockImplementation(async (_content, _id, onEvent) => {
    onEvent?.({ run_id: run.run_id, sequence: 1, event_type: "run.started", payload: {} });
    return new Promise((_resolve, reject) => { rejectStream = reject; });
  });
  vi.mocked(api.cancelRun).mockResolvedValue({ run_id: run.run_id, status: "waiting_approval", cancellation_requested: true });
  await act(async () => root.render(<App />));
  await input("运行测试"); await click("发送消息");
  expect(host.querySelector('.chat-approval')!.textContent).toContain('"python" "-m" "pytest"');
  expect(host.querySelector('.chat-approval')!.textContent).toContain(project.directory);
  await click("批准本次");
  expect(tasks.decideApproval).toHaveBeenCalledWith(approval.id, approval.fingerprint, "approve_once");
  await click("停止任务"); expect(api.cancelRun).toHaveBeenCalledWith(run.run_id);
  expect(host.querySelector('[aria-label="发送消息"]')).toBeNull();
  vi.mocked(tasks.fetchRun).mockResolvedValue({ ...run, status: "cancelled" });
  await act(async () => rejectStream(new Error("任务已停止")));
  expect(host.querySelector('[aria-label="发送消息"]')).not.toBeNull();
  expect(host.textContent).toContain("任务已停止");
});

it("recovers a pending approval after reopening a conversation", async () => {
  vi.mocked(api.fetchConversations).mockResolvedValue([conversation]);
  vi.mocked(tasks.fetchConversationRuns).mockResolvedValue([{ ...run, status: "waiting_approval" }]);
  vi.mocked(tasks.fetchRun).mockResolvedValue({ ...run, status: "waiting_approval" });
  vi.mocked(tasks.fetchApprovals).mockResolvedValue([{ id: "approval", run_id: run.run_id, call_id: "call", tool_id: "builtin.local.delete", fingerprint: "token", binding: { directory: project.directory, arguments: { path: "old.py" } }, status: "pending", session_allowed: false, expires_at: "later", warning: "删除文件" }]);
  await act(async () => root.render(<App />));
  expect(host.querySelector('.chat-approval')!.textContent).toContain("old.py");
  await click("拒绝");
  expect(tasks.decideApproval).toHaveBeenCalledWith("approval", "token", "reject");
  expect(api.streamChat).not.toHaveBeenCalled();
});

it("leaves the project unchanged when the native folder dialog is cancelled", async () => {
  vi.mocked(tasks.chooseProjectDirectory).mockResolvedValue({ directory: null });
  await act(async () => root.render(<App />));
  await click("选择项目"); await click("添加本地项目…");
  expect(tasks.createProject).not.toHaveBeenCalled();
  expect(host.querySelector('.project-trigger')!.textContent).toContain("选择项目");
});

it("filters projects and can switch before sending without creating a conversation", async () => {
  vi.mocked(tasks.fetchProjects).mockResolvedValue([project, { ...project, id: "project-b", name: "another", directory: "C:/another" }]);
  await act(async () => root.render(<App />));
  await click("选择项目");
  await act(async () => {
    const element = host.querySelector('[aria-label="搜索项目"]')!;
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(element, "demo");
    element.dispatchEvent(new Event("input", { bubbles: true }));
  });
  expect(host.querySelector('.project-options')!.textContent).toBe("demo");
  await click("demo");
  expect(host.querySelector('.project-trigger')!.textContent).toContain("demo");
  await click("选择项目"); await click("普通聊天 · 不使用项目");
  expect(host.querySelector('.project-trigger')!.textContent).toContain("选择项目");
  expect(api.streamChat).not.toHaveBeenCalled();
});

it("queues an early stop until the backend run ID arrives", async () => {
  vi.mocked(api.cancelRun).mockResolvedValue({ run_id: run.run_id, status: "running", cancellation_requested: true });
  let receiveEvent!: NonNullable<Parameters<typeof api.streamChat>[2]>;
  let rejectStream!: (error: Error) => void;
  let streamSignal!: AbortSignal;
  vi.mocked(api.streamChat).mockImplementation(async (_content, _id, onEvent, signal) => {
    receiveEvent = onEvent!;
    streamSignal = signal!;
    return new Promise((_resolve, reject) => { rejectStream = reject; });
  });
  await act(async () => root.render(<App />));
  await input("开始任务"); await click("发送消息"); await click("停止任务");
  expect(streamSignal.aborted).toBe(false);
  expect(api.cancelRun).not.toHaveBeenCalled();
  await act(async () => receiveEvent({ run_id: run.run_id, sequence: 1, event_type: "run.started", payload: {} }));
  expect(api.cancelRun).toHaveBeenCalledWith(run.run_id);
  await act(async () => rejectStream(new Error("任务已停止")));
});

it("waits for folder selection before allowing a message to use the project", async () => {
  let finishPicker!: (value: { directory: string | null }) => void;
  vi.mocked(tasks.chooseProjectDirectory).mockImplementation(() => new Promise((resolve) => { finishPicker = resolve; }));
  await act(async () => root.render(<App />));
  await input("读取代码"); await click("选择项目"); await click("添加本地项目…");
  expect((host.querySelector('[aria-label="发送消息"]') as HTMLButtonElement).disabled).toBe(true);
  await act(async () => host.querySelector('textarea')!.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })));
  expect(api.streamChat).not.toHaveBeenCalled();
  await act(async () => finishPicker({ directory: null }));
  expect((host.querySelector('[aria-label="发送消息"]') as HTMLButtonElement).disabled).toBe(false);
});

it.each([{ local: { enabled: false } }, { tools: { enabled: false } }])("explains disabled project file access instead of implying the folder is readable (%j)", async (disabled) => {
  vi.mocked(api.fetchPublicConfig).mockResolvedValue({ ...publicConfig, ...disabled });
  await act(async () => root.render(<App />));
  expect(host.querySelector('.project-access-warning')).toBeNull();
  await click("选择项目"); await click("demo");
  expect(host.querySelector('.project-access-warning')!.textContent).toContain("暂时无法读取项目");
  expect(host.querySelector('.project-trigger')!.textContent).toContain("demo");
});
