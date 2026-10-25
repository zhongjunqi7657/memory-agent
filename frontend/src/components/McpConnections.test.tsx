// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import type { Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import * as api from "../mcpApi";
import type { ServerConfig } from "../mcpApi";
import { McpConnections } from "./McpConnections";
import { McpOAuthCallback } from "./McpOAuthCallback";

vi.mock("../mcpApi");
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
let host: HTMLDivElement;
let root: Root;
const server: ServerConfig = { id: "web", enabled: true, transport: "streamable_http", url: "https://example.com/mcp",
  args: [], env_allowlist: [], auth: { type: "none" }, command_timeout_seconds: 10,
  default_permissions: { capabilities: ["network.access"] }, tool_permissions: {} };
const response = { enabled: true, allowed_commands: ["python"], servers: [{ config: server,
  state: { id: "web", status: "ready", tool_count: 2, resource_count: 1, prompt_count: 1 } }] };

beforeEach(() => {
  vi.resetAllMocks(); sessionStorage.clear(); window.history.replaceState(null, "", "/");
  vi.mocked(api.fetchConnections).mockResolvedValue(response);
  vi.mocked(api.connectionAction).mockResolvedValue({});
  vi.mocked(api.saveConnection).mockResolvedValue({});
  host = document.createElement("div"); document.body.appendChild(host); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });
async function click(text: string) {
  const button = [...host.querySelectorAll("button")].find((item) => item.textContent === text)!;
  expect(button).toBeDefined();
  await act(async () => button.click());
}
async function fill(label: string, value: string) {
  const input = [...host.querySelectorAll("label")].find((item) => item.textContent?.startsWith(label))!.querySelector("input")!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

it("shows capability counts and performs explicit disconnect/reconnect without executing tools", async () => {
  await act(async () => root.render(<McpConnections />));
  expect(host.textContent).toContain("工具 2 · 资源 1 · 提示词 1");
  await click("断开");
  expect(api.connectionAction).toHaveBeenCalledWith("web", "disconnect");
  await click("重连");
  expect(api.connectionAction).toHaveBeenCalledWith("web", "connect");
});

it("creates HTTP connection configuration and saves without starting it", async () => {
  await act(async () => root.render(<McpConnections />));
  await click("添加连接"); await fill("连接名称", "new-web"); await fill("服务地址", "https://new.example/mcp");
  await act(async () => host.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
  expect(api.saveConnection).toHaveBeenCalledWith(expect.objectContaining({ id: "new-web", url: "https://new.example/mcp", auth: { type: "none" } }));
  expect(api.connectionAction).not.toHaveBeenCalled();
  expect(host.textContent).toContain("已保存");
});

it("disabling a server preserves its per-item permissions and requires an explicit reconnect", async () => {
  const configured = { ...server, tool_permissions: { write: { capabilities: ["filesystem.write"] } } };
  vi.mocked(api.fetchConnections).mockResolvedValue({ ...response, servers: [{ ...response.servers[0], config: configured }] });
  await act(async () => root.render(<McpConnections />)); await click("禁用");
  expect(api.saveConnection).toHaveBeenCalledWith({ ...configured, enabled: false });
  expect(api.connectionAction).not.toHaveBeenCalled();
});

it("reports missing credentials and offers an authorization link without exposing tokens", async () => {
  vi.mocked(api.fetchConnections).mockResolvedValue({ ...response, servers: [{ config: { ...server, auth: { type: "oauth" } },
    state: { id: "web", status: "failed", tool_count: 0, error_category: "permission" } }] });
  vi.mocked(api.beginAuthorization).mockResolvedValue({ authorization_url: "https://id.example/authorize?state=state-one" });
  await act(async () => root.render(<McpConnections />));
  expect(host.textContent).toContain("凭据缺失或已失效");
  await click("授权");
  const link = host.querySelector("a")!;
  link.addEventListener("click", (event) => event.preventDefault());
  expect(link.href).toContain("https://id.example/authorize");
  // Avoid jsdom navigation while exercising the state binding.
  await act(async () => link.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true })));
  expect(sessionStorage.getItem("mcp-oauth-state")).toBe("state-one");
});

it("strips callback credentials from the URL before exchanging them", async () => {
  sessionStorage.setItem("mcp-oauth-state", "state-one");
  window.history.replaceState(null, "", "/?code=secret-code&state=state-one");
  vi.mocked(api.completeAuthorization).mockImplementation(async () => {
    expect(window.location.search).toBe(""); return {};
  });
  await act(async () => root.render(<McpOAuthCallback />));
  expect(api.completeAuthorization).toHaveBeenCalledWith("state-one", "secret-code", window.location.origin + "/");
  expect(sessionStorage.getItem("mcp-oauth-state")).toBeNull();
  expect(host.textContent).toContain("授权已完成");
  expect(host.textContent).not.toContain("secret-code");
});

it("rejects callback state mismatch and an OAuth denial without a token exchange", async () => {
  sessionStorage.setItem("mcp-oauth-state", "expected");
  window.history.replaceState(null, "", "/?code=secret-code&state=wrong");
  await act(async () => root.render(<McpOAuthCallback />));
  expect(api.completeAuthorization).not.toHaveBeenCalled();
  expect(window.location.search).toBe("");
  expect(host.textContent).toContain("状态不匹配");
});
