import { expect, it } from "vitest";
import type { RunEvent } from "./api";
import { getRunOutput } from "./modelOutput";
import { mergeRunEvents } from "./runEvents";

function event(sequence: number, event_type: string, payload: Record<string, unknown>): RunEvent {
  return { run_id: "run", sequence, event_type, payload };
}

it("replays overlapping chunks without duplicating Markdown whitespace", () => {
  const start = event(1, "model.output.started", { output_id: "one" });
  const first = event(2, "model.output.delta", { output_id: "one", text: "```ts\n" });
  const last = event(3, "model.output.delta", { output_id: "one", text: "  const n = 1;\n```" });
  expect(getRunOutput(mergeRunEvents("run", [start, first], [last, first]))).toBe("```ts\n  const n = 1;\n```");
});

it("replaces tool-round drafts and ignores chunks from a previous output", () => {
  expect(getRunOutput([
    event(1, "model.output.started", { output_id: "one" }),
    event(2, "model.output.delta", { output_id: "one", text: "checking" }),
    event(3, "model.output.started", { output_id: "two" }),
    event(4, "model.output.delta", { output_id: "one", text: "old chunk" }),
    event(5, "model.output.delta", { output_id: "two", text: "## Final" }),
  ])).toBe("## Final");
});

it("uses the final persisted answer when a tool-limit fallback replaces draft text", () => {
  expect(getRunOutput([
    event(1, "model.output.started", { output_id: "one" }),
    event(2, "model.output.delta", { output_id: "one", text: "draft" }),
    event(3, "run.completed", { message: "Final summary with budget notice" }),
  ])).toBe("Final summary with budget notice");
});
