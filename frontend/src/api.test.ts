import { afterEach, expect, it, vi } from "vitest";

import { streamChat } from "./api";

afterEach(() => vi.unstubAllGlobals());

it.each(["run.cancelled", "run.interrupted"])("stops on %s without polling recovery", async (eventType) => {
  vi.stubGlobal("sessionStorage", { getItem: () => null });
  const event = { run_id: "run-1", sequence: 2, event_type: eventType, payload: { message: "任务已停止" } };
  const fetch = vi.fn().mockResolvedValue(new Response(`event: ${eventType}\ndata: ${JSON.stringify(event)}\n\n`));
  vi.stubGlobal("fetch", fetch);
  await expect(streamChat("hello", null)).rejects.toThrow("任务已停止");
  expect(fetch).toHaveBeenCalledTimes(1);
});
