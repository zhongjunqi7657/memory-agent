# Frontend

React + Vite + TypeScript thin client. It will be implemented after the backend contract and SSE event schema are stable.
## Memory Agent Web

React + Vite 薄客户端，提供聊天主界面和可折叠的长期记忆面板。

```powershell
npm install
npm run dev
```

开发服务器默认运行在 `http://localhost:5173`，会将 `/v1` 请求代理到本地 FastAPI（`http://127.0.0.1:8000`）。如需连接其他 API 地址，设置 `VITE_API_BASE_URL`。
