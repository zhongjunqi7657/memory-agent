# Frontend

进度同步：2026-11-05，阶段 6 连接管理和审计修复已随 `4d3d903` 推送。阶段 7 最新完整门禁前端 10 个测试文件、60 项测试及 TypeScript/生产构建通过；阶段 4 的完整人工交互和阶段 6 用户验收仍待完成，详见[开发状态](../docs/development-status.md)。本次推送未重启开发 API。

React + Vite + TypeScript thin client.

阶段 7 原生入口可在 API 根地址提供 `npm run build` 产物。首次请求需输入本机 `LOCAL_API_TOKEN`，凭据仅保存在当前标签页会话存储，与演示密码分开；不会将本机令牌发送到远程 API，也不跟随重定向。开发 Vite 入口继续使用相同认证流程。启动和验收状态见[阶段 7 记录](../docs/pre-desktop-stage7-local-delivery.md)。
## Memory Agent Web

React + Vite 薄客户端，提供聊天主界面和可折叠的长期记忆面板。

```powershell
npm install
npm run dev
```

开发服务器默认运行在 `http://localhost:5173`，会将 `/v1` 请求代理到本地 FastAPI（`http://127.0.0.1:8000`）。如需连接其他 API 地址，设置 `VITE_API_BASE_URL`。

设置页提供 MCP 连接创建/编辑、启停、重连与 OAuth 授权。管理接口只接受本机受信任页面；OAuth 回调配置为当前前端根地址，令牌由后端保存在内存，浏览器返回后移除当前 URL 的临时代码。连接、资源与提示词边界见[阶段 6 记录](../docs/pre-desktop-stage6-mcp-client.md)。
