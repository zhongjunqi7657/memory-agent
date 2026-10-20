import type { RunEvent } from "./api";

/** Events are ordered and deduplicated by runEvents before reaching this reducer. */
export function getRunOutput(events: RunEvent[]): string {
  let outputId: string | null = null;
  let parts: string[] = [];
  for (const event of events) {
    if (event.event_type === "model.output.started" && typeof event.payload.output_id === "string") {
      outputId = event.payload.output_id;
      parts = [];
    } else if (event.event_type === "model.output.delta" && event.payload.output_id === outputId
      && typeof event.payload.text === "string") {
      parts.push(event.payload.text);
    } else if (event.event_type === "run.completed" && typeof event.payload.message === "string") {
      parts = [event.payload.message];
    }
  }
  return parts.join("");
}
