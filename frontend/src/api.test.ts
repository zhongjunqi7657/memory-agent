import { afterEach, expect, it, vi } from "vitest";

import { cancelRun, streamChat } from "./api";

afterEach(() => vi.unstubAllGlobals());

it("delivers deltas across fragmented UTF-8 frames before the response finishes", async () => {
  vi.stubGlobal("sessionStorage", { getItem: () => null });
  let source!: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream<Uint8Array>({ start(controller) { source = controller; } });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(stream)));
  let delivered!: () => void;
  const firstDelta = new Promise<void>((resolve) => { delivered = resolve; });
  const onEvent = vi.fn(() => delivered());
  let finished = false;
  const request = streamChat("hello", null, onEvent).then((response) => { finished = true; return response; });
  const delta = { run_id: "run-1", sequence: 2, event_type: "model.output.delta", payload: { output_id: "one", text: "中文 **正文**" } };
  const bytes = new TextEncoder().encode(`event: model.output.delta\ndata: ${JSON.stringify(delta)}\n\n`);
  const chineseByte = bytes.indexOf(0xe4);
  source.enqueue(bytes.slice(0, chineseByte + 1));
  source.enqueue(bytes.slice(chineseByte + 1));
  await firstDelta;
  expect(onEvent).toHaveBeenCalledWith(delta);
  expect(finished).toBe(false);
  const result = { conversation_id: "conversation-1", run_id: "run-1", message: delta.payload.text,
    redacted: false, redaction_categories: [], memory_count: 0 };
  source.enqueue(new TextEncoder().encode(`event: run.completed\ndata: ${JSON.stringify({ event_type: "run.completed", payload: result })}\n\n`));
  source.close();
  await expect(request).resolves.toEqual(result);
});

it.each(["run.cancelled", "run.interrupted"])("stops on %s without polling recovery", async (eventType) => {
  vi.stubGlobal("sessionStorage", { getItem: () => null });
  const event = { run_id: "run-1", sequence: 2, event_type: eventType, payload: { message: "任务已停止" } };
  const fetch = vi.fn().mockResolvedValue(new Response(`event: ${eventType}\ndata: ${JSON.stringify(event)}\n\n`));
  vi.stubGlobal("fetch", fetch);
  await expect(streamChat("hello", null)).rejects.toThrow("任务已停止");
  expect(fetch).toHaveBeenCalledTimes(1);
});

it("sends the selected project execution mode and worktree", async () => {
  vi.stubGlobal("sessionStorage", { getItem: () => null });
  const result = {
    conversation_id: "conversation-1",
    run_id: "run-1",
    message: "done",
    redacted: false,
    redaction_categories: [],
    memory_count: 0,
  };
  const event = { run_id: "run-1", sequence: 1, event_type: "run.completed", payload: result };
  const fetch = vi.fn().mockResolvedValue(new Response(`event: run.completed\ndata: ${JSON.stringify(event)}\n\n`));
  vi.stubGlobal("fetch", fetch);

  await expect(streamChat("edit code", null, undefined, undefined, {
    projectId: "project-1",
    executionMode: "worktree",
    worktreeId: "worktree-1",
  })).resolves.toEqual(result);
  expect(JSON.parse(fetch.mock.calls[0][1].body as string)).toMatchObject({
    project_id: "project-1",
    execution_mode: "worktree",
    worktree_id: "worktree-1",
  });
});

it("posts a cancellation request for an active run", async () => {
  vi.stubGlobal("sessionStorage", { getItem: () => null });
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ run_id: "run-1", status: "cancelled", cancellation_requested: true }), { status: 200 }));
  vi.stubGlobal("fetch", fetch);
  await expect(cancelRun("run-1")).resolves.toMatchObject({ status: "cancelled" });
  expect(fetch.mock.calls[0][0]).toContain("/v1/runs/run-1/cancel");
});
