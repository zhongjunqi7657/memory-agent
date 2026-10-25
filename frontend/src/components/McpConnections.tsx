import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { beginAuthorization, connectionAction, fetchConnections, saveConnection } from "../mcpApi";
import type { Connection, ServerConfig } from "../mcpApi";
import "./McpConnections.css";

const permissions = ["filesystem.read", "filesystem.write", "network.access", "external.side_effect", "process.execute", "memory.read", "memory.write.propose"];
const permissionLabels = ["读取文件", "修改文件", "访问网络", "外部写入", "执行进程", "读取记忆", "提出记忆变更"];
const statusLabels: Record<string, string> = { ready: "已连接", failed: "连接失败", pending: "等待连接", stopped: "已断开" };
const fresh = (): ServerConfig => ({ id: "", enabled: true, transport: "streamable_http", url: "", command: null,
  args: [], env_allowlist: [], command_timeout_seconds: 10, auth: { type: "none" },
  default_permissions: { capabilities: [] }, tool_permissions: {} });

export function McpConnections() {
  const [connections, setConnections] = useState<Connection[]>([]);
  const [draft, setDraft] = useState<ServerConfig | null>(null);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [authorization, setAuthorization] = useState<{ id: string; url: string } | null>(null);
  const [enabled, setEnabled] = useState(true);
  const active = useRef(false);
  const revision = useRef(0);

  async function refresh() {
    const current = ++revision.current;
    const response = await fetchConnections();
    if (active.current && current === revision.current) {
      setConnections(response.servers); setEnabled(response.enabled); setLoading(false);
    }
  }
  useEffect(() => {
    active.current = true;
    void refresh().catch(() => { if (active.current) { setError("无法读取本机 MCP 连接，请检查服务或访问权限"); setLoading(false); } });
    return () => { active.current = false; ++revision.current; };
  }, []);

  async function perform(operation: () => Promise<unknown>, message = "") {
    setBusy(true); setError(""); setNotice(""); setAuthorization(null);
    try {
      await operation();
      await refresh();
      if (active.current) setNotice(message);
    } catch (failure) {
      if (active.current) setError(failure instanceof Error ? failure.message : "连接操作失败");
    } finally { if (active.current) setBusy(false); }
  }
  function change<K extends keyof ServerConfig>(key: K, value: ServerConfig[K]) {
    setDraft((current) => current && { ...current, [key]: value });
  }
  function oauth(key: string, value: string) {
    if (!draft) return;
    change("auth", { type: "oauth", oauth: { client_id: "", authorization_url: "", token_url: "",
      redirect_uri: window.location.origin + "/", scope: "", ...draft.auth.oauth, [key]: value } });
  }
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!draft) return;
    await perform(async () => { await saveConnection(draft); if (active.current) setDraft(null); }, "已保存。点击连接后生效；连接配置不等于批准调用。");
  }

  return <section className="mcp-connections">
    <div className="mcp-heading"><div><h2>MCP 连接</h2><p>接入可信服务；工具、资源和提示词的使用仍需逐次审批。</p></div>
      <button className="secondary-button" disabled={busy} onClick={() => { setDraft(fresh()); setEditing(false); }}>添加连接</button>
      <button className="secondary-button" disabled={busy} onClick={() => void perform(refresh)}>刷新连接</button></div>
    {!enabled && <p>远程 MCP 已在配置文件中全局关闭。</p>}
    {loading && <p>正在读取连接…</p>}
    {error && <p role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {authorization && <p><a href={authorization.url} onClick={() => {
      sessionStorage.setItem("mcp-oauth-state", new URL(authorization.url).searchParams.get("state") ?? "");
    }}>继续授权 {authorization.id}</a> · 完成后将返回当前页面</p>}
    {!loading && !connections.length && <p>尚未配置连接。可添加本机 stdio 或 Streamable HTTP 服务。</p>}
    {connections.map(({ config, state }) => <div className="mcp-row" key={state.id}>
      <div><h3>{state.id}</h3><p>{statusLabels[state.status] ?? state.status} · {config?.transport === "stdio" ? "本机进程" : "Streamable HTTP"}</p>
        <p>工具 {state.tool_count} · 资源 {state.resource_count ?? 0} · 提示词 {state.prompt_count ?? 0}</p>
        {state.error_category && <p role="status">{state.error_category === "permission" ? "凭据缺失或已失效，请检查环境变量或重新授权" : `连接错误（${state.error_category}），可手动重连`}</p>}
        {!config && <p>配置包含疑似凭据，请在本机配置文件中改为环境变量引用。</p>}</div>
      {config && <div className="mcp-actions">
        <button disabled={busy} onClick={() => { setDraft(structuredClone(config)); setEditing(true); }}>编辑</button>
        <button disabled={busy} onClick={() => void perform(() => saveConnection({ ...config, enabled: !config.enabled }), "已更新启用状态。启用后可手动连接。")}>{config.enabled ? "禁用" : "启用"}</button>
        <button disabled={busy || !enabled || !config.enabled} onClick={() => void perform(() => connectionAction(config.id, "connect"))}>{state.status === "ready" ? "重连" : "连接"}</button>
        <button disabled={busy || state.status !== "ready"} onClick={() => void perform(() => connectionAction(config.id, "disconnect"))}>断开</button>
        {config.auth.type === "oauth" && <>
          <button disabled={busy || !enabled || !config.enabled} onClick={() => void perform(async () => {
            const result = await beginAuthorization(config.id); if (active.current) setAuthorization({ id: config.id, url: result.authorization_url });
          })}>授权</button>
          <button disabled={busy} onClick={() => void perform(() => connectionAction(config.id, "oauth/revoke"), "已断开并清除本机令牌。远端授权可在服务商页面撤销。")}>清除授权</button>
        </>}
      </div>}
    </div>)}
    {draft && <form className="mcp-form" onSubmit={(event) => void save(event)}>
      <h3>{editing ? "编辑连接" : "添加连接"}</h3>
      <label>连接名称<input required disabled={editing || busy} value={draft.id} onChange={(event) => change("id", event.target.value)} pattern="[A-Za-z0-9_-]+" placeholder="例如 local-files" /></label>
      <label>传输方式<select disabled={busy} value={draft.transport} onChange={(event) => {
        const transport = event.target.value as ServerConfig["transport"];
        setDraft({ ...draft, transport, command: transport === "stdio" ? "python" : null,
          url: transport === "stdio" ? null : "", args: [], env_allowlist: [], auth: { type: "none" } });
      }}><option value="streamable_http">Streamable HTTP</option><option value="stdio">本机 stdio</option></select></label>
      {draft.transport === "stdio" ? <>
        <label>启动命令<input required value={draft.command ?? ""} onChange={(event) => change("command", event.target.value)} /></label>
        <label>命令参数（每行一项）<textarea value={draft.args.join("\n")} onChange={(event) => change("args", event.target.value.split("\n"))} /></label>
        <label>允许继承的环境变量（逗号分隔）<input value={draft.env_allowlist.join(",")} onChange={(event) => change("env_allowlist", event.target.value.split(",").map((item) => item.trim()).filter(Boolean))} /></label>
      </> : <>
        <label>服务地址<input required type="url" value={draft.url ?? ""} onChange={(event) => change("url", event.target.value)} placeholder="https://example.com/mcp" /></label>
        <label>鉴权<select value={draft.auth.type} onChange={(event) => {
          const type = event.target.value as ServerConfig["auth"]["type"];
          change("auth", type === "oauth" ? { type, oauth: { client_id: "", authorization_url: "", token_url: "", redirect_uri: window.location.origin + "/", scope: "" } } : type === "bearer" ? { type, token_env: "" } : { type });
        }}><option value="none">无需鉴权</option><option value="bearer">环境变量中的 Bearer 令牌</option><option value="oauth">OAuth 授权码 + PKCE</option></select></label>
        {draft.auth.type === "bearer" && <label>令牌环境变量名<input required value={draft.auth.token_env ?? ""} onChange={(event) => change("auth", { type: "bearer", token_env: event.target.value })} placeholder="MCP_SERVICE_TOKEN" /></label>}
        {draft.auth.type === "oauth" && <>
          <p>支持预注册公共客户端。令牌仅保存在内存中，重启后重新授权。</p>
          {([ ["client_id", "客户端 ID"], ["authorization_url", "授权地址"], ["token_url", "令牌地址"], ["redirect_uri", "回调地址"], ["scope", "授权范围"] ] as const).map(([key, label]) =>
            <label key={key}>{label}<input required={key !== "scope"} value={draft.auth.oauth?.[key] ?? ""} onChange={(event) => oauth(key, event.target.value)} /></label>)}
        </>}
      </>}
      <fieldset><legend>默认所需权限</legend><p>这是调用时展示的权限声明，每次调用仍需用户批准。</p><div className="mcp-permissions">{permissions.map((permission, index) => <label key={permission}>
        <input type="checkbox" checked={draft.default_permissions?.capabilities.includes(permission) ?? false} onChange={(event) => {
          const current = draft.default_permissions?.capabilities ?? [];
          change("default_permissions", { capabilities: event.target.checked ? [...current, permission] : current.filter((item) => item !== permission) });
        }} />{permissionLabels[index]}</label>)}</div></fieldset>
      <details><summary>发现范围与权限覆盖</summary><p>白名单每行一项；* 表示全部，留空表示不加载。</p>
        {([ ["allowed_tools", "工具白名单"], ["allowed_resources", "资源 URI 白名单"], ["allowed_prompts", "提示词白名单"] ] as const).map(([key, label]) => <label key={key}>{label}<textarea value={draft[key] == null ? "*" : draft[key]!.join("\n")} onChange={(event) => change(key, event.target.value === "*" ? null : event.target.value.split("\n").filter(Boolean))} /></label>)}
        <p>已配置的逐项权限覆盖会保留；复杂权限声明可在本机配置文件中维护。</p>
      </details>
      <p>只连接可信服务。不要在地址、命令参数或字段中填写密钥；本机进程以当前用户权限运行。</p>
      <div className="mcp-actions"><button className="primary-button" disabled={busy} type="submit">保存连接</button><button disabled={busy} type="button" onClick={() => setDraft(null)}>取消</button></div>
    </form>}
  </section>;
}
