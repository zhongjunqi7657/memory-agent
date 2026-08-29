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

第 1 阶段已完成：数据库模型与迁移、千问适配器、LangGraph 对话闭环、记忆治理、记忆查询/修改接口和提取 Worker 骨架均已落地。真实 PostgreSQL 迁移需要 Docker Desktop 的 Linux 引擎可用。

## 本地运行

1. 复制配置：`Copy-Item .env.example .env`，填写 `DASHSCOPE_API_KEY`、`DASHSCOPE_BASE_URL` 和 `POSTGRES_PASSWORD`。
2. 复制业务配置：`Copy-Item config.toml.example config.toml`，按需调整模型名和阈值。
3. 启动数据库：`docker compose up -d postgres`。
4. 执行迁移：`cd backend; uv run alembic upgrade head`。
5. 启动 API：`uv run uvicorn app.main:app --reload`。
6. 单独启动记忆 Worker：`uv run python -m app.jobs.worker`。

### API 示例

```bash
curl -X POST http://127.0.0.1:8000/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"user_key":"demo-user","content":"我计划两个月内完成 Agent 项目"}'

curl "http://127.0.0.1:8000/v1/memories?user_key=demo-user"
```

聊天请求会先在本机脱敏，再写入消息并排队记忆提取任务；API 响应中的 `redacted` 和 `redaction_categories` 可直接用于前端的折叠提示。记忆候选由 Worker 通过结构化输出提取，普通明确信息自动生效，敏感/推断信息进入 `pending`，秘密直接丢弃。
