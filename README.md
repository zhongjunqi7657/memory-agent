# Memory Agent Demo

一个中文优先、记忆驱动的个人成长与学习伙伴 Agent。项目用于学习 Agent 工程，并形成可公开演示的作品。

## 当前目标

首版只解决一个核心闭环：

```text
跨会话交流 -> 提取用户记忆 -> 风险治理 -> 混合召回 -> 个性化回答
```

记忆必须可查看、可解释、可编辑、可删除，并且有离线评测覆盖提取、召回、冲突和删除行为。

## 技术边界

- Python + FastAPI
- LangChain 组件，LangGraph 自定义 StateGraph
- PostgreSQL + pgvector
- Qwen OpenAI-compatible API
- React + Vite + TypeScript 薄客户端
- API 与记忆提取 Worker 分进程运行
- SSE 传输主 Agent 运行事件

首版不包含文件知识库、外部系统、语音、渠道接入、真正的子 Agent 或多供应商切换。

## 目录

```text
backend/app/api/          HTTP and SSE endpoints
backend/app/agent/        LangGraph state and nodes
backend/app/config/       Configuration loading
backend/app/jobs/         Persistent extraction worker
backend/app/memory/       Memory extraction, policy and retrieval
backend/app/models/       Qwen model adapters
backend/app/persistence/  SQLAlchemy models and repositories
backend/app/security/     Secret redaction and input policy
backend/app/evaluation/   Offline evaluation runner and cases
backend/tests/            Automated tests
frontend/                 React thin client (added in the UI phase)
docs/                     Architecture and interview notes
```

## 开发约定

- API Key、数据库密码等只放环境变量。
- 业务阈值、模型名和功能开关放 `config.toml`。
- 协议值、枚举和不可变规则放代码常量。
- LLM 负责提取和标注；代码负责风险策略、版本和持久化。
- 任何秘密先在本机脱敏，再发送给模型或写入数据库。
- 真实模型调用不进入默认 CI，使用 Fake Provider 测试。

## 当前阶段

首版主链路已完成：数据库模型与迁移、千问适配器、LangGraph 对话闭环、混合召回、记忆治理、显式记忆指令、SSE 运行事件和提取 Worker 均已落地。Worker 具备租约、幂等键、失败重试和超时任务重新领取字段。PostgreSQL 16 + pgvector 0.8.6 的迁移、向量读写、Worker 跳锁抢占、SSE 事件重放和跨会话记忆召回已经过真实数据库集成测试；千问 `qwen-plus` 对话、结构化记忆提取及 `text-embedding-v3` 1024 维向量链路已经过真实 API 联调。

## 本地运行

1. 复制配置：`Copy-Item .env.example .env`，填写 `DASHSCOPE_API_KEY`、`DASHSCOPE_BASE_URL` 和 `POSTGRES_PASSWORD`。
2. 复制业务配置：`Copy-Item config.toml.example config.toml`，按需调整模型名和阈值。
3. 启动数据库：`docker compose up -d postgres`。
4. 执行迁移：`cd backend; uv run alembic upgrade head`。
5. 启动 API：`uv run uvicorn app.main:app --reload`。
6. 单独启动记忆 Worker：`uv run python -m app.jobs.worker`。

另开一个终端启动前端：`cd frontend; npm install; npm run dev`，然后访问 `http://localhost:5173`。前端会将 `/v1` 请求代理到 FastAPI；也可以通过 `VITE_API_BASE_URL` 指向已部署的 API。

### 测试

常规测试不调用真实模型；没有配置测试库时，PostgreSQL 集成用例会自动跳过：

```powershell
cd backend
uv run ruff check app tests alembic
uv run pytest -q
```

首次执行真实数据库集成测试时，创建名称以 `_test` 结尾的隔离数据库并迁移。下面的密码应与本机 `.env` 保持一致：

```powershell
docker exec memory-agent-demo-postgres-1 createdb -U memory_agent memory_agent_test
$env:DATABASE_URL='postgresql+asyncpg://memory_agent:replace-me@localhost:5432/memory_agent_test'
uv run alembic upgrade head
$env:TEST_DATABASE_URL=$env:DATABASE_URL
uv run pytest tests/test_postgres_integration.py -q
```

集成测试会拒绝连接名称不以 `_test` 结尾的数据库，避免清理测试数据时误碰开发库。

### API 示例

```bash
curl -X POST http://127.0.0.1:8000/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"user_key":"demo-user","content":"我计划两个月内完成 Agent 项目"}'

curl "http://127.0.0.1:8000/v1/memories?user_key=demo-user"

# 显式记忆控制（不会经过普通聊天提取队列）
curl -X POST http://127.0.0.1:8000/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"user_key":"demo-user","content":"请记住我喜欢先理解原理再看代码"}'

curl -X POST http://127.0.0.1:8000/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"user_key":"demo-user","content":"忘记我之前说的考研计划"}'

curl -X POST http://127.0.0.1:8000/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"user_key":"demo-user","content":"你记住了我什么？"}'
```

聊天请求会先在本机脱敏，再写入消息并排队记忆提取任务；前端通过 `POST /v1/chat/stream` 消费 SSE 运行事件，最终事件携带回答和 `redacted` 元数据，可直接用于折叠提示。记忆候选由 Worker 通过结构化输出提取，普通明确信息自动生效，敏感/推断信息进入 `pending`，秘密直接丢弃。

以“请记住/忘记/把……改成……”开头的显式指令会直接进入记忆治理服务：普通事实立即生效，敏感内容进入待确认，凭据和个人号码在本机脱敏后不写入长期记忆。更正产生新版本时，旧记忆标记为 `superseded`；待确认的新版本不会立即淘汰旧记忆。
