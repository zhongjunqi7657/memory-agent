import { useEffect, useRef, useState } from "react";
import { completeAuthorization } from "../mcpApi";

export function McpOAuthCallback() {
  const started = useRef(false);
  const [message, setMessage] = useState("");
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const params = new URLSearchParams(window.location.search);
    const expected = sessionStorage.getItem("mcp-oauth-state");
    if (!expected || (!params.has("code") && !params.has("error"))) return;
    // Remove short-lived credentials before issuing any requests or rendering links.
    window.history.replaceState(null, "", window.location.pathname);
    sessionStorage.removeItem("mcp-oauth-state");
    if (params.get("state") !== expected || params.has("error") || !params.get("code")) {
      setMessage("MCP 授权未完成或状态不匹配，请在设置中重新授权。"); return;
    }
    setMessage("正在完成 MCP 授权…");
    void completeAuthorization(expected, params.get("code")!, window.location.origin + window.location.pathname)
      .then(() => setMessage("MCP 授权已完成，可在设置中查看连接状态。"))
      .catch(() => setMessage("MCP 授权失败或已过期，请在设置中重新授权。"));
  }, []);
  return message ? <p role="status" className="mcp-oauth-status">{message}</p> : null;
}
