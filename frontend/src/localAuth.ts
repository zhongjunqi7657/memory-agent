// Local credentials stay in the current tab, separate from the demo password.
const storageKey = "memory-agent:local-api-token";
let tokenPrompt: Promise<string | null> | null = null;

export function isLocalApi(input: RequestInfo | URL): boolean {
  const value = input instanceof Request ? input.url : String(input);
  const base = typeof window !== "undefined" ? window.location?.href : undefined;
  const url = new URL(value, base ?? "http://localhost");
  return ["http:", "https:"].includes(url.protocol) &&
    ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
}

export function localAuthHeaders(input: RequestInfo | URL, initial?: HeadersInit): Headers {
  const headers = new Headers(initial);
  const token = sessionStorage.getItem(storageKey);
  if (token && isLocalApi(input)) headers.set("X-Local-Token", token);
  return headers;
}

export async function retryLocalAuth(input: RequestInfo | URL, response: Response, send: () => Promise<Response>): Promise<Response> {
  if (response.status !== 401 || response.headers.get("www-authenticate") !== "LocalToken") return response;
  if (!isLocalApi(input)) throw new Error("本机访问令牌只能用于本机 API");
  if (!tokenPrompt) {
    tokenPrompt = Promise.resolve(window.prompt("请输入本机访问令牌（LOCAL_API_TOKEN）"));
  }
  const token = await tokenPrompt;
  tokenPrompt = null;
  if (!token) return response;
  sessionStorage.setItem(storageKey, token);
  const retried = await send();
  if (retried.status === 401) sessionStorage.removeItem(storageKey);
  return retried;
}
