import { afterEach, expect, it, vi } from "vitest";
import { requestWithDemoAuth } from "./api";

afterEach(() => vi.unstubAllGlobals());

function storage() {
  const values = new Map<string, string>();
  vi.stubGlobal("sessionStorage", {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
  });
  return values;
}

it("pairs after the local challenge and sends the token only in a header", async () => {
  const values = storage();
  vi.stubGlobal("window", { prompt: vi.fn().mockReturnValue("local-secret") });
  const fetch = vi.fn().mockResolvedValueOnce(new Response("", { status: 401, headers: { "WWW-Authenticate": "LocalToken" } }))
    .mockResolvedValueOnce(new Response("{}"));
  vi.stubGlobal("fetch", fetch);
  expect((await requestWithDemoAuth("/v1/control", { method: "POST" })).ok).toBe(true);
  expect(fetch.mock.calls[1][1].headers.get("X-Local-Token")).toBe("local-secret");
  expect(fetch.mock.calls[1][1].redirect).toBe("error");
  expect(fetch.mock.calls[1][0]).toBe("/v1/control");
  expect(values.has("memory-agent:demo-basic-auth")).toBe(false);
});

it("clears a rejected local credential without prompting for a demo password", async () => {
  const values = storage();
  const prompt = vi.fn().mockReturnValue("wrong");
  vi.stubGlobal("window", { prompt });
  vi.stubGlobal("fetch", vi.fn().mockImplementation(() => Promise.resolve(new Response("", {
    status: 401, headers: { "WWW-Authenticate": "LocalToken" },
  }))));
  expect((await requestWithDemoAuth("/config")).status).toBe(401);
  expect(prompt).toHaveBeenCalledTimes(1);
  expect(values.size).toBe(0);
});

it("does not replay a request after a transport failure", async () => {
  storage();
  const fetch = vi.fn().mockRejectedValue(new TypeError("connection lost"));
  vi.stubGlobal("fetch", fetch);
  await expect(requestWithDemoAuth("/v1/control", { method: "POST" })).rejects.toThrow("connection lost");
  expect(fetch).toHaveBeenCalledTimes(1);
});

it("never sends a stored local token to a remote API or pairs with one", async () => {
  const values = storage();
  values.set("memory-agent:local-api-token", "secret");
  const prompt = vi.fn();
  vi.stubGlobal("window", { prompt });
  const fetch = vi.fn().mockResolvedValue(new Response("", {
    status: 401, headers: { "WWW-Authenticate": "LocalToken" },
  }));
  vi.stubGlobal("fetch", fetch);
  await expect(requestWithDemoAuth("https://example.com/config")).rejects.toThrow("只能用于本机");
  expect(fetch.mock.calls[0][1].headers.has("X-Local-Token")).toBe(false);
  expect(prompt).not.toHaveBeenCalled();
});
