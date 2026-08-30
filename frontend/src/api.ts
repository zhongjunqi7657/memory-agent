export type MemoryStatus = "active" | "pending" | "superseded" | "deleted";
export type MemoryKind = "semantic" | "episodic";

export interface Memory {
  id: string;
  kind: MemoryKind;
  status: MemoryStatus;
  sensitivity: "normal" | "sensitive" | "secret";
  content: string;
  confidence: number;
  canonical_key: string | null;
  metadata: Record<string, unknown>;
  source_message_id: string | null;
}

export interface ChatResponse {
  conversation_id: string;
  run_id: string;
  message: string;
  redacted: boolean;
  redaction_categories: string[];
  memory_count: number;
}

export interface RunEvent {
  run_id?: string;
  sequence?: number;
  event_type: string;
  payload: Record<string, unknown>;
}

const apiBase = import.meta.env.VITE_API_BASE_URL ?? "";

async function parseResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(body?.detail ?? "请求失败，请稍后重试");
  }
  return response.json() as Promise<T>;
}

export async function sendChat(
  content: string,
  conversationId: string | null,
): Promise<ChatResponse> {
  const response = await fetch(`${apiBase}/v1/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      user_key: "demo-user",
      content,
      conversation_id: conversationId,
    }),
  });
  return parseResponse<ChatResponse>(response);
}

export async function streamChat(
  content: string,
  conversationId: string | null,
  onEvent?: (event: RunEvent) => void,
): Promise<ChatResponse> {
  const response = await fetch(`${apiBase}/v1/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      user_key: "demo-user",
      content,
      conversation_id: conversationId,
    }),
  });
  if (!response.ok || !response.body) {
    return parseResponse<ChatResponse>(response);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let completed: ChatResponse | null = null;
  while (true) {
    const chunk = await reader.read();
    buffer += decoder.decode(chunk.value ?? new Uint8Array(), { stream: !chunk.done });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const dataLine = frame.split("\n").find((line) => line.startsWith("data: "));
      if (!dataLine) continue;
      const event = JSON.parse(dataLine.slice(6)) as RunEvent;
      onEvent?.(event);
      if (event.event_type === "error") {
        throw new Error(String(event.payload.message ?? "请求处理失败"));
      }
      if (event.event_type === "run.completed") {
        completed = event.payload as unknown as ChatResponse;
      }
    }
    if (chunk.done) break;
  }
  if (!completed) throw new Error("流式响应未正常结束");
  return completed;
}

export async function fetchMemories(): Promise<Memory[]> {
  const response = await fetch(`${apiBase}/v1/memories?user_key=demo-user&limit=50`);
  return parseResponse<Memory[]>(response);
}

export async function updateMemory(id: string, status: "active" | "deleted") {
  const response = await fetch(
    `${apiBase}/v1/memories/${encodeURIComponent(id)}?user_key=demo-user`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    },
  );
  return parseResponse<Memory>(response);
}
