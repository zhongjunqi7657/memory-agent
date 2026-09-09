import { describe, expect, it, vi } from "vitest";

import type { RunEvent } from "./api";
import { followRunEvents, mergeRunEvents, needsExtractionFollow } from "./runEvents";

const runId = "00000000-0000-0000-0000-000000000001";
const otherRunId = "00000000-0000-0000-0000-000000000002";

function event(sequence: number, eventType: string, owner = runId): RunEvent {
  return { run_id: owner, sequence, event_type: eventType, payload: {} };
}

describe("mergeRunEvents", () => {
  it("deduplicates replayed sequences and rejects events from another run", () => {
    const merged = mergeRunEvents(
      runId,
      [event(1, "run.started"), event(2, "memory.extraction_queued")],
      [event(2, "memory.extraction_queued"), event(3, "run.completed")],
      [event(4, "memory.extraction_completed", otherRunId)],
    );

    expect(merged.map((item) => item.sequence)).toEqual([1, 2, 3]);
    expect(merged.every((item) => item.run_id === runId)).toBe(true);
  });
});

describe("extraction following", () => {
  it("continues queued runs until a completion event is replayed", async () => {
    const initial = [event(1, "memory.extraction_queued"), event(2, "run.completed")];
    const fetchEvents = vi.fn()
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([
        {
          ...event(3, "memory.extraction_completed"),
          payload: { changes: [{ id: "memory-1", action: "created" }] },
        },
      ]);
    const updates: RunEvent[][] = [];

    const result = await followRunEvents({
      runId,
      initialEvents: initial,
      signal: new AbortController().signal,
      onUpdate: (events) => updates.push(events),
      pollIntervalMs: 1,
      fetchEvents,
    });

    expect(fetchEvents).toHaveBeenNthCalledWith(1, runId, 2, expect.any(AbortSignal));
    expect(fetchEvents).toHaveBeenNthCalledWith(2, runId, 2, expect.any(AbortSignal));
    expect(result.at(-1)?.event_type).toBe("memory.extraction_completed");
    expect(updates).toHaveLength(1);
    expect(needsExtractionFollow(result)).toBe(false);
  });

  it("treats final extraction failure as terminal", async () => {
    const result = await followRunEvents({
      runId,
      initialEvents: [event(1, "memory.extraction_queued")],
      signal: new AbortController().signal,
      onUpdate: () => undefined,
      pollIntervalMs: 1,
      fetchEvents: vi.fn().mockResolvedValue([event(2, "memory.extraction_failed")]),
    });

    expect(result.at(-1)?.event_type).toBe("memory.extraction_failed");
    expect(needsExtractionFollow(result)).toBe(false);
  });

  it("stops polling when the owning conversation is cancelled", async () => {
    const controller = new AbortController();
    const fetchEvents = vi.fn(async () => {
      controller.abort();
      return [];
    });

    await expect(followRunEvents({
      runId,
      initialEvents: [event(1, "memory.extraction_queued")],
      signal: controller.signal,
      onUpdate: () => undefined,
      pollIntervalMs: 1,
      fetchEvents,
    })).rejects.toBeDefined();

    expect(fetchEvents).toHaveBeenCalledTimes(1);
  });
});
