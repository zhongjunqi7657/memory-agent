import { parseResponse, requestWithDemoAuth } from "./api";

export type ServerConfig = {
  id: string;
  enabled: boolean;
  transport: "stdio" | "streamable_http";
  command?: string | null;
  args: string[];
  env_allowlist: string[];
  url?: string | null;
  command_timeout_seconds: number;
  auth: { type: "none" | "bearer" | "oauth"; token_env?: string | null; oauth?: {
    client_id: string; authorization_url: string; token_url: string; redirect_uri: string; scope: string;
  } | null };
  default_permissions: { capabilities: string[] } | null;
  tool_permissions: Record<string, { capabilities: string[] }>;
  allowed_tools?: string[] | null;
  allowed_resources?: string[] | null;
  allowed_prompts?: string[] | null;
};
export type Connection = {
  config: ServerConfig | null;
  state: { id: string; status: string; error_category?: string | null; tool_count: number;
    resource_count?: number; prompt_count?: number; capabilities?: string[] };
};
const base = `${import.meta.env.VITE_API_BASE_URL ?? ""}/v1/mcp/connections`;
async function json<T>(path: string, body?: unknown): Promise<T> {
  return parseResponse<T>(await requestWithDemoAuth(base + path, body === undefined ? undefined : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }));
}
export const fetchConnections = () => json<{ servers: Connection[]; enabled: boolean; allowed_commands: string[] }>("");
export const saveConnection = (server: ServerConfig) => json("", server);
export const connectionAction = (id: string, action: "connect" | "disconnect" | "oauth/revoke") =>
  json(`/${encodeURIComponent(id)}/${action}`, {});
export const beginAuthorization = (id: string) =>
  json<{ authorization_url: string }>(`/${encodeURIComponent(id)}/oauth/start`, {});
export const completeAuthorization = (state: string, code: string, callbackUri: string) =>
  json("/oauth/complete", { state, code, callback_uri: callbackUri });
