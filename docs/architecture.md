# Architecture Notes

The first version keeps four boundaries explicit:

```text
React client
    |
FastAPI API / SSE
    |
LangGraph agent runtime ---- MemoryService ---- PostgreSQL + pgvector
    |                                |
Qwen adapters                 extraction_jobs Worker
```

首版请求链路保持单向且可观测：`POST /v1/chat` 脱敏用户输入，写入 `messages`，检索 active 记忆，调用 LangGraph，保存助手消息和 `runs` 状态，最后创建 `extraction_jobs`。Worker 使用 `SELECT ... FOR UPDATE SKIP LOCKED` 领取任务，结构化提取候选后交给确定性的记忆策略，按 `active/pending/superseded/deleted` 管理生命周期。

记忆面板通过 `GET /v1/memories` 查询，并用 `PATCH /v1/memories/{id}` 将 pending 记忆设为 active 或删除。默认列表只返回记忆正文和治理字段，解释信息保存在 `metadata.reason`，前端可以折叠显示。

The business tables remain owned by the application. LangGraph checkpoints are a separate persistence concern and are introduced only after the basic graph is testable.
