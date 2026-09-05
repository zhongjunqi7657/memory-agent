export type MemoryStatus = "active" | "pending" | "superseded" | "deleted" | "rejected";
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

export interface ConversationSummary {
  id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
}

export interface StoredMessage {
  id: string;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  sequence: number;
  is_redacted: boolean;
  created_at: string;
}

export interface ChatResponse {
  conversation_id: string;
  run_id: string;
  message: string;
  redacted: boolean;
  redaction_categories: string[];
  memory_count: number;
  memory_command?: string | null;
}

export interface RunEvent {
  run_id?: string;
  sequence?: number;
  event_type: string;
  payload: Record<string, unknown>;
}

const apiBase = import.meta.env.VITE_API_BASE_URL ?? "";
const userKey = "demo-user";
const replayTimeoutMs = 60_000;
const authStorageKey = "memory-agent:demo-basic-auth";
let authPrompt: Promise<string | null> | null = null;

function getAuthHeader(): string | null {
  return sessionStorage.getItem(authStorageKey);
}

async function requestWithDemoAuth(
  input: RequestInfo | URL,
  init: RequestInit = {},
): Promise<Response> {
  const send = (authorization: string | null) => {
    const headers = new Headers(init.headers);
    if (authorization) headers.set("Authorization", authorization);
    return fetch(input, { ...init, headers });
  };

  let response = await send(getAuthHeader());
  if (response.status !== 401 || typeof window === "undefined") return response;

  if (!authPrompt) {
    authPrompt = Promise.resolve(window.prompt("请输入演示环境共享密码"));
  }
  const password = await authPrompt;
  authPrompt = null;
  if (!password) return response;

  const authorization = `Basic ${btoa(`demo:${password}`)}`;
  sessionStorage.setItem(authStorageKey, authorization);
  response = await send(authorization);
  if (response.status === 401) sessionStorage.removeItem(authStorageKey);
  return response;
}

async function parseResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(body?.detail ?? "请求失败，请稍后重试");
  }
  return response.json() as Promise<T>;
}

function parseFrames(buffer: string): { events: RunEvent[]; remainder: string } {
  const frames = buffer.split(/\r?\n\r?\n/);
  const remainder = frames.pop() ?? "";
  const events = frames.flatMap((frame) => {
    const dataLine = frame.split(/\r?\n/).find((line) => line.startsWith("data: "));
    return dataLine ? [JSON.parse(dataLine.slice(6)) as RunEvent] : [];
  });
  return { events, remainder };
}

function completedResponse(event: RunEvent): ChatResponse | null {
  return event.event_type === "run.completed"
    ? (event.payload as unknown as ChatResponse)
    : null;
}

async function fetchRunEvents(runId: string, afterSequence: number): Promise<RunEvent[]> {
  const params = new URLSearchParams({
    user_key: userKey,
    after_sequence: String(afterSequence),
    limit: "200",
  });
  const response = await requestWithDemoAuth(
    `${apiBase}/v1/runs/${encodeURIComponent(runId)}/events?${params}`,
  );
  return parseResponse<RunEvent[]>(response);
}

async function recoverRun(
  runId: string,
  afterSequence: number,
  onEvent?: (event: RunEvent) => void,
): Promise<ChatResponse> {
  const deadline = Date.now() + replayTimeoutMs;
  let sequence = afterSequence;
  while (Date.now() < deadline) {
    const events = await fetchRunEvents(runId, sequence);
    for (const event of events) {
      sequence = Math.max(sequence, event.sequence ?? sequence);
      onEvent?.(event);
      if (event.event_type === "run.failed") {
        throw new Error(String(event.payload.message ?? "请求处理失败"));
      }
      const completed = completedResponse(event);
      if (completed) return completed;
    }
    await new Promise((resolve) => window.setTimeout(resolve, 800));
  }
  throw new Error("连接已中断，本次运行仍可稍后从运行记录中恢复");
}

export async function streamChat(
  content: string,
  conversationId: string | null,
  onEvent?: (event: RunEvent) => void,
): Promise<ChatResponse> {
  const response = await requestWithDemoAuth(`${apiBase}/v1/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_key: userKey, content, conversation_id: conversationId }),
  });
  if (!response.ok || !response.body) return parseResponse<ChatResponse>(response);

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let completed: ChatResponse | null = null;
  let runId: string | null = null;
  let lastSequence = 0;
  let terminalError: Error | null = null;

  try {
    while (true) {
      const chunk = await reader.read();
      buffer += decoder.decode(chunk.value ?? new Uint8Array(), { stream: !chunk.done });
      const parsed = parseFrames(buffer);
      buffer = parsed.remainder;
      for (const event of parsed.events) {
        runId = event.run_id ?? runId;
        lastSequence = Math.max(lastSequence, event.sequence ?? lastSequence);
        onEvent?.(event);
        if (event.event_type === "error" || event.event_type === "run.failed") {
          terminalError = new Error(String(event.payload.message ?? "请求处理失败"));
          throw terminalError;
        }
        completed = completedResponse(event) ?? completed;
      }
      if (chunk.done) break;
    }
  } catch (error) {
    if (terminalError) throw terminalError;
    if (!runId) throw error;
  }

  if (completed) return completed;
  if (runId) return recoverRun(runId, lastSequence, onEvent);
  throw new Error("流式响应未正常开始");
}

export async function fetchConversations(): Promise<ConversationSummary[]> {
  const response = await requestWithDemoAuth(`${apiBase}/v1/conversations?user_key=${userKey}&limit=50`);
  return parseResponse<ConversationSummary[]>(response);
}

export async function fetchConversationMessages(
  conversationId: string,
): Promise<StoredMessage[]> {
  const response = await requestWithDemoAuth(
    `${apiBase}/v1/conversations/${encodeURIComponent(conversationId)}/messages?user_key=${userKey}`,
  );
  return parseResponse<StoredMessage[]>(response);
}

export async function fetchMemories(): Promise<Memory[]> {
  const response = await requestWithDemoAuth(`${apiBase}/v1/memories?user_key=${userKey}&limit=50`);
  return parseResponse<Memory[]>(response);
}

export async function updateMemory(id: string, status: "active" | "rejected" | "deleted") {
  const response = await requestWithDemoAuth(
    `${apiBase}/v1/memories/${encodeURIComponent(id)}?user_key=${userKey}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    },
  );
  return parseResponse<Memory>(response);
}
