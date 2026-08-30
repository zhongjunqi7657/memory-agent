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
}

export interface ChatResponse {
  conversation_id: string;
  run_id: string;
  message: string;
  redacted: boolean;
  redaction_categories: string[];
  memory_count: number;
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
