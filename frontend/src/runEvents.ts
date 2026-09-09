import { fetchRunEvents } from "./api";
import type { RunEvent } from "./api";

const extractionTerminalEvents = new Set([
  "memory.extraction_completed",
  "memory.extraction_failed",
]);

export function mergeRunEvents(
  runId: string,
  ...groups: RunEvent[][]
): RunEvent[] {
  const bySequence = new Map<number, RunEvent>();
  for (const event of groups.flat()) {
    if (event.run_id === runId && typeof event.sequence === "number") {
      bySequence.set(event.sequence, event);
    }
  }
  return [...bySequence.values()].sort(
    (left, right) => (left.sequence ?? 0) - (right.sequence ?? 0),
  );
}

export function needsExtractionFollow(events: RunEvent[]): boolean {
  return events.some((event) => event.event_type === "memory.extraction_queued")
    && !events.some((event) => extractionTerminalEvents.has(event.event_type));
}

function wait(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(signal.reason ?? new DOMException("Aborted", "AbortError"));
      return;
    }
    const onAbort = () => {
      globalThis.clearTimeout(timeout);
      reject(signal.reason ?? new DOMException("Aborted", "AbortError"));
    };
    const timeout = globalThis.setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, milliseconds);
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

type FollowRunEventsOptions = {
  runId: string;
  initialEvents: RunEvent[];
  signal: AbortSignal;
  onUpdate: (events: RunEvent[]) => void;
  pollIntervalMs?: number;
  fetchEvents?: typeof fetchRunEvents;
};

export async function followRunEvents({
  runId,
  initialEvents,
  signal,
  onUpdate,
  pollIntervalMs = 800,
  fetchEvents = fetchRunEvents,
}: FollowRunEventsOptions): Promise<RunEvent[]> {
  let events = mergeRunEvents(runId, initialEvents);
  let failedRequests = 0;
  while (needsExtractionFollow(events)) {
    try {
      const lastSequence = events.at(-1)?.sequence ?? 0;
      const incoming = await fetchEvents(runId, lastSequence, signal);
      const merged = mergeRunEvents(runId, events, incoming);
      if (merged.length !== events.length) {
        events = merged;
        onUpdate(events);
      }
      failedRequests = 0;
    } catch (error) {
      if (signal.aborted) throw error;
      failedRequests += 1;
    }
    if (needsExtractionFollow(events)) {
      const backoff = Math.min(pollIntervalMs * (2 ** failedRequests), 5_000);
      await wait(backoff, signal);
    }
  }
  return events;
}
