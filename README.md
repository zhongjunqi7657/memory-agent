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

当前支持 stdio 与 Streamable HTTP MCP 客户端，以及工具、静态资源和提示词接入；文件知识库、语音、渠道接入、子 Agent 与多供应商切换不在本次范围。

## 目录

```text
backend/app/api/          HTTP and SSE endpoints
backend/app/agent/        LangGraph state and nodes
backend/app/execution/    Run lifecycle, cancellation and runtime coordination
backend/app/local_runtime/ Native startup, lifecycle, diagnostics and database archives
backend/app/workspace/    Project bindings, task snapshots and managed Git worktrees
backend/app/config/       Configuration loading
backend/app/protocol/     Protocol-independent tool contracts
backend/app/tools/        Unified tool registry, execution and built-ins
backend/app/permission/   Explicit capability authorization
backend/app/audit/        Tool execution audit sinks
backend/app/mcp/          Official SDK MCP clients, authentication and connection management
backend/app/cli/          Model-independent human commands
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
- 本机 API 默认要求 `LOCAL_API_TOKEN`、loopback 与受信任 Origin；演示部署显式使用 `APP_ENV=demo`、配置 `DEMO_SHARED_PASSWORD` 并关闭本地执行。
- 演示接口按用户限流并计算每日估算 Token 预算，超长输入会在计入配额前拒绝；多副本部署需替换为共享限流存储。

## 当前阶段

开发文档入口：[最新进度与阶段导航](docs/development-status.md)、[桌面化前实施与验收方案](docs/pre-desktop-implementation-plan.md)、[主分支合并检查记录](docs/main-merge-2026-10-19.md)。截至 2026-11-05，阶段 0 至阶段 3、阶段 5 已验收；[阶段 6 MCP 客户端扩展](docs/pre-desktop-stage6-mcp-client.md)已开发完成并通过提交前审计、自动化与隔离界面验证，待用户验收。阶段 4 的完整真实界面/命令演示仍待验收，阶段 7 本地交付代码已落地，整体验收状态见[阶段 7 记录](docs/pre-desktop-stage7-local-delivery.md)。最新工作区完整回归为后端 361 项（零跳过）、前端 60 项通过，迁移往返、Ruff、生产构建与离线基线一致性通过；记忆离线评测仍保留 4 条既有质量失败。

首版主链路已完成：数据库模型与迁移、千问适配器、多节点 LangGraph、五因子混合召回、记忆治理、显式记忆指令、SSE 运行事件和提取 Worker 均已落地。统一 Tool 执行核心、官方 MCP stdio Client、legacy HTTP 兼容入口与最小 CLI 已完成。重复事实会合并来源；用户确认后才替换旧版本，并可撤销最近修改。Worker 具备租约、幂等键、失败重试、超时重新领取，以及绑定内容和模型版本的 Embedding 补偿任务。

Graph 使用三个请求级内置记忆工具与 MCP 远程工具快照，通过同一 Function Calling 循环和 ToolExecutor 执行。统一管线校验 JSON Schema、显式能力权限、超时与取消，并写入 `tool.execution.*` 运行事件。系统使用 PostgreSQL Checkpoint 保存会话图状态；长对话使用分批持久摘要与统一 Token 预算。Embedding 降级原因、补偿版本和上下文裁剪状态均写入可重放运行事件。前端包含聊天、独立记忆治理、时间线/周期回顾和非敏感设置四个工作区。详细验收见 [改造记录](docs/tool-mcp-implementation.md)；架构和使用说明见 [架构文档](docs/architecture.md) 与 [演示流程](docs/demo-script.md)。

桌面化前阶段 1/2/3 已完成项目目录绑定、任务状态/取消、运行中事件回放、重启中断、目录互斥、本地文本文件工具、命令审批、Windows 子进程树回收，以及任务基线 Diff、安全撤销和显式 Git worktree 的创建、带回与清理。接口与限制见[阶段 1 验收记录](docs/pre-desktop-stage1-run-control.md)、[阶段 2 验收记录](docs/pre-desktop-stage2-controlled-execution.md)和[阶段 3 验收记录](docs/pre-desktop-stage3-workspace-changes.md)。本地执行默认关闭，仅在可信本机设置 `[local].enabled = true` 并选择项目目录后启用；命令与删除需要审批，MCP 连接配置不等于调用批准。默认使用原目录，用户显式选择时才创建 worktree；命令或 MCP 执行后不承诺自动撤销。阶段 4 已将项目选择、审批与 Diff 整合进对话，操作方式见下文及[阶段 4 记录](docs/pre-desktop-stage4-agent-task-ui.md)。API 同一数据库只允许一个运行实例，请使用单进程启动；这不是操作系统沙箱。

阶段 6 实现和审计修复已通过英文提交 [4d3d903](https://github.com/zhongjunqi7657/memory-agent/commit/4d3d90381a9600b7d90ad4bf716fe3f6a88f8d86) 推送到 `main`，包含 75 项 MCP 后端测试。本次交付未重启开发 API/Worker；启动或重启到该提交后才能加载最新修复。阶段 6 仍待用户验收，后续安排以[开发状态](docs/development-status.md)为准。

### 分层长期记忆

普通聊天保留个人记忆，所有项目均可召回；项目对话只增加当前项目的长期记忆，原目录与受管 worktree 共用同一个项目 ID。项目中发送“记住项目测试约定使用 pytest”会经过既有治理保存到该项目；不同项目的同名约定不会互相替换。项目聊天、工具日志和临时尝试默认只保留为会话上下文，不自动成为长期记忆。新增个人偏好请在不绑定项目的普通聊天中表达。

长期记忆页和时间线可选择个人或某个项目，确认、编辑、删除、撤销和周期回顾均限定所选范围；聊天侧栏同时展示个人与当前项目记忆。在项目中发送“查看我的记忆”“忘记…”或更正只操作该项目。历史记忆保留为个人层，不自动重分类。阶段 5 的实现与验收见[分层长期记忆](docs/pre-desktop-stage5-layered-memory.md)。升级前停止 API/Worker 并备份，在 backend 执行 `uv run alembic upgrade head` 后重启；已有项目记忆时会拒绝直接降级，避免失去隔离。

### 在对话中使用项目

新建对话后，点击输入框左下角“选择项目”→“添加本地项目…”，选择文件夹即可自动添加并选中，也可搜索已有项目。普通聊天无需项目。发送后项目与执行目录固定，切换项目会开启新对话；历史会话会恢复原项目及等待中的审批。

项目身份与执行目录会传入模型上下文，审计时根据工具实际读取的文件回答。若提示“本地文件访问未启用”，请在实际加载的本机 `config.toml` 中设置 `[local].enabled = true`，并确保 `[tools].enabled = true`，重启 API 后重新发送任务；仅选择文件夹不会覆盖部署配置。设置页可查看项目文件访问状态。此前未启用工具时的旧回复及缺失文件快照保持历史状态，不会自动重跑。

普通聊天默认最多执行 4 轮工具，项目任务单独使用 `[agent].project_max_tool_rounds = 24`。设置页可查看两个上限；达到上限时停止执行新工具，根据已有文件证据生成阶段性结果并标出未检查范围，可发送“继续”处理剩余部分。上限不会绕过命令或删除审批。

审批直接出现在对应回复中，确认目录和命令后选择“批准本次”或“拒绝”。命令输出和“修改了 N 个文件”默认折叠，展开可查看退出码、Diff 与可用的撤销操作。Worktree 放在项目菜单“高级选项”；停止按钮请求后端取消并等待结束。Windows 本机支持原生文件夹窗口，其他环境可展开手工路径备用入口；远端页面不能打开服务器本机窗口。真实模型的项目识别/读取已验证，完整命令失败修复与重连演示仍待阶段 4 验收。

助手新回复、历史回复和阶段回顾共用 Markdown 渲染，支持标题、列表、GFM 表格、引用、链接和代码块；用户输入保持原文。宽表格在内容区内独立滚动，代码保留缩进；不解析原始 HTML，也暂不提供代码高亮、公式和复制按钮。

## 本地运行

推荐使用阶段 7 新增的原生入口。Python 3.10—3.13、Node 20+、Git 和外置 PostgreSQL + pgvector 须事先准备，不会自动安装数据库或修改系统配置。

1. 复制配置：`Copy-Item .env.example .env`，填写 `DASHSCOPE_API_KEY`、`DASHSCOPE_BASE_URL`、`POSTGRES_PASSWORD` 和正确的 `DATABASE_URL`。设置 `APP_ENV=local`、`APP_HOST=127.0.0.1`，生成并填入 `LOCAL_API_TOKEN`（`python -c "import secrets; print(secrets.token_urlsafe(32))"`）。已有 `.env` 请手工更新，不覆盖原凭据。
2. 复制业务配置：`Copy-Item config.toml.example config.toml`，按需调整模型名和阈值。
3. 准备数据库；已安装 PostgreSQL 可直接使用，也可运行 `docker compose up -d postgres`。
4. 安装项目依赖：`uv sync --project backend --locked`、`npm --prefix frontend ci`，构建界面：`npm --prefix frontend run build`。
5. 停止使用同一数据库的旧 API/Worker、备份后，显式执行 `uv run --project backend memory-agent local migrate`。启动不会自动迁移。
6. 运行 `uv run --project backend memory-agent local doctor`，确认环境、配置、凭据、数据库、pgvector 和迁移检查通过。
7. 运行 `uv run --project backend memory-agent local start`，访问 `http://127.0.0.1:8000/`，首次请求输入 `.env` 中的本机访问令牌。MCP OAuth 回调配置为该根地址。

Ctrl+C 会关闭本实例 API、Worker、任务与 MCP，并释放 Checkpoint 和数据库租约；外置 PostgreSQL 保持运行。Worker 异常退出时应用会关闭并报告失败，不自动重启或重放调用。日志在 `APP_DATA_DIR/logs/`，MCP 覆盖在 `APP_DATA_DIR/mcp-connections.json`，worktree 在 `APP_DATA_DIR/worktrees/`；任务快照、事件和 Checkpoint 在 PostgreSQL。`APP_DATA_DIR` 默认仓库 `.runtime`，覆盖时必须是绝对路径；配置路径默认基于源码位置，`MEMORY_AGENT_CONFIG` 覆盖也要求绝对路径。

开发界面仍可单独运行 `npm --prefix frontend run dev`，访问 `http://127.0.0.1:5173`，本机令牌验证方式相同。独立调试 API 可在 backend 执行 `uv run uvicorn app.main:app --host 127.0.0.1 --loop app.runtime:event_loop --reload`，另开 Worker；这两个调试进程由各自终端关闭。完整本机安全和恢复边界见[阶段 7 记录](docs/pre-desktop-stage7-local-delivery.md)。

也可以用 Compose 启动受保护的演示部署（需要先准备配置，设置 `DEMO_SHARED_PASSWORD`，并关闭 `[local].enabled`；Compose API 固定使用 demo 模式，端口仅发布到 loopback）：

```powershell
Copy-Item .env.example .env
Copy-Item config.toml.example config.toml
docker compose up -d --build
```

演示部署所有 `/v1` 路由要求 HTTP Basic 共享密码；本机模式额外要求独立访问令牌，密码和 `user_key` 均不能替代它。凭据只保存在当前标签页的会话存储，刷新可恢复，关闭标签页后重新验证。本机模式会拒绝非 loopback、恶意 Origin、跨站请求和非 loopback Host；本地其他进程、浏览器扩展或同一操作系统账户仍不属于隔离范围。不要将本机控制 API 开放到公网。

原生数据库备份与恢复（需要 PATH 中有匹配数据库版本的 `pg_dump` / `pg_restore`；先停止 API 与全部 Worker）：

```powershell
uv run --project backend memory-agent local backup --output F:/memory-agent-backups/before-upgrade.dump
uv run --project backend memory-agent local restore --input F:/memory-agent-backups/before-upgrade.dump --confirm-restore
```

备份包含业务数据、审批/事件、任务快照及 Checkpoint；不会覆盖已有归档，失败不报告成功。恢复在单事务中替换目标数据库对象，失败回滚；运行中的 API 租约会拒绝迁移、备份和恢复。请另外保存 `config.toml`、`APP_DATA_DIR/mcp-connections.json`、项目文件和受管 worktree；令牌、`.env` 和 OAuth 凭据不进入数据库归档，凭据须自行安全保存，OAuth 重启后重新授权。项目路径或 worktree 缺失时，数据库恢复不能重建其文件。`scripts/backup.ps1` / `restore.ps1` 继续支持旧 Compose SQL 归档，已补退出码检查，不能与原生 custom 格式混用。

### 测试

常规测试不调用真实模型；没有配置测试库时，PostgreSQL 集成用例会自动跳过：

```powershell
cd backend
uv run ruff check app tests alembic
uv run pytest -q
uv run python -m app.evaluation.runner

cd ../frontend
npm test
npm run build
```

离线评测包含 36 条中文样本，默认读取已保存的千问预测快照，不调用真实 API。基线报告及刷新方式见 `backend/app/evaluation/README.md`。

敏感信息识别是尽力而为，用户不应把密码、API Key、Token、身份证号或其他秘密发送给系统。线上演示使用独立数据库和 Key，不与本地开发数据共用。

首次执行真实数据库集成测试时，创建名称以 `_test` 结尾的隔离数据库并迁移。下面的密码应与本机 `.env` 保持一致：

```powershell
docker exec memory-agent-demo-postgres-1 createdb -U memory_agent memory_agent_test
$env:DATABASE_URL='postgresql+asyncpg://memory_agent:replace-me@localhost:5432/memory_agent_test'
uv run alembic upgrade head
$env:TEST_DATABASE_URL=$env:DATABASE_URL
uv run pytest tests/test_postgres_integration.py -q
```

集成测试会拒绝连接名称不以 `_test` 结尾的数据库，避免清理测试数据时误碰开发库。

Windows 完整回归可运行 `./scripts/verify-baseline.ps1`。先在当前 worktree 的 `backend` 执行 `uv sync --locked`，在 `frontend` 执行 `npm ci`，并启动 Docker 引擎。脚本使用单独的 Compose 文件和随机项目名，在 loopback 随机端口创建临时 PostgreSQL + pgvector，执行迁移、完整后端测试、离线评测及前端测试/构建；后端有跳过或评测偏离已提交基线时门禁失败。临时数据库在退出时回收，报告保存在忽略目录 `.runtime/baseline/`，不操作开发数据库，也不覆盖预测快照或评测基线。Docker 仅用于这一回归流程，不是产品原生运行的强制依赖。

### API 示例

本机示例先将 `.env` 中的 `LOCAL_API_TOKEN` 导入当前 Shell 的同名环境变量；演示部署另外追加 Basic 密码。

```bash
curl -H "X-Local-Token: $LOCAL_API_TOKEN" -X POST http://127.0.0.1:8000/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"user_key":"demo-user","content":"我计划两个月内完成 Agent 项目"}'

curl -H "X-Local-Token: $LOCAL_API_TOKEN" "http://127.0.0.1:8000/v1/memories?user_key=demo-user"

curl -H "X-Local-Token: $LOCAL_API_TOKEN" "http://127.0.0.1:8000/config"

# 显式记忆控制（不会经过普通聊天提取队列）
curl -H "X-Local-Token: $LOCAL_API_TOKEN" -X POST http://127.0.0.1:8000/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"user_key":"demo-user","content":"请记住我喜欢先理解原理再看代码"}'

curl -H "X-Local-Token: $LOCAL_API_TOKEN" -X POST http://127.0.0.1:8000/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"user_key":"demo-user","content":"忘记我之前说的考研计划"}'

curl -H "X-Local-Token: $LOCAL_API_TOKEN" -X POST http://127.0.0.1:8000/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"user_key":"demo-user","content":"你记住了我什么？"}'

curl -H "X-Local-Token: $LOCAL_API_TOKEN" -X POST http://127.0.0.1:8000/v1/reviews \
  -H "Content-Type: application/json" \
  -d '{"user_key":"demo-user","period_days":7}'

# Legacy Tool HTTP 工具发现与调用（设置 DEMO_SHARED_PASSWORD 后追加 -u demo:<password>）
curl -H "X-Local-Token: $LOCAL_API_TOKEN" http://127.0.0.1:8000/v1/mcp/tools

curl -H "X-Local-Token: $LOCAL_API_TOKEN" -X POST http://127.0.0.1:8000/v1/mcp/call \
  -H "Content-Type: application/json" \
  -d '{"user_key":"demo-user","conversation_id":"<conversation-id>","tool":"search_memory","arguments":{"query":"我的学习目标"}}'
```

聊天请求会先在本机脱敏，再写入消息并排队记忆提取任务；前端通过 `POST /v1/chat/stream` 消费 SSE 运行事件，最终事件携带回答和 `redacted` 元数据，可直接用于折叠提示。Legacy 工具调用同样绑定用户和会话，记忆写入工具必须引用当前会话的用户消息，返回结果中的 `run_id` 可用于查看完整审计事件。记忆候选由 Worker 通过结构化输出提取，普通明确信息自动生效，敏感/推断信息进入 `pending`，秘密直接丢弃。

## 统一 Tool、MCP 与 CLI

`/v1/mcp/tools` 与 `/v1/mcp/call` 是兼容的 Tool HTTP API，不能作为标准 MCP Server 配置给客户端。应用通过官方 Python SDK 连接 stdio 或标准 Streamable HTTP Server，按协商能力发现工具、静态资源和提示词；资源和提示词的使用同样经过调用审批。

工具业务配置在 `[tools]`，远程连接配置在 `[mcp]` 与 `[[mcp.servers]]`。旧 `mcp.allowed_tools`、`mcp.tool_timeout_seconds` 会映射到新配置并发出弃用提示。`tools.enabled=false` 禁用全部工具；`mcp.enabled=false` 只停用远程连接。默认内置工具授予 `memory.read`、`memory.write.propose`；MCP 的 `default_permissions.capabilities` 和 `tool_permissions.<远程原名>` 均为调用所需权限声明，不是会话授予，连接成功也不等于批准工具调用。

在本机“设置 → MCP 连接”中添加/编辑服务，保存后点击“连接”。界面支持启停、手动重连、断开及鉴权状态；断开或编辑后旧快照不能继续调用，重连不重放先前操作。设置页连接覆盖保存于 `APP_DATA_DIR/mcp-connections.json`，默认 `.runtime/mcp-connections.json`；同 ID 优先于 TOML，命令白名单与全局开关仍由 TOML 管理。连接管理保留本机受信任 Origin 检查，并叠加阶段 7 的统一令牌和 Host/Origin 保护；整体验收仍需完成阶段 7 人工清单。开发 API 须重启到当前代码后才能使用新增接口。

HTTP 支持无鉴权、环境变量引用的 Bearer 和预注册公共客户端 OAuth 授权码 + PKCE。Bearer 变量必须存在于 API 进程环境，任意自定义变量仅写入 `.env` 不会自动导出；不要在配置或地址中填写原始令牌。OAuth 端点显式配置，回调为当前本机前端根地址，令牌仅在内存中，重启后重新授权。“清除授权”断开并清除本机令牌，远端授权须在服务商页面撤销。地址必须使用 HTTPS，HTTP 仅支持 loopback；禁止重定向和隐式环境代理，凭据缺失/失效不退回匿名请求，也不重试工具副作用。配置示例、接口、受支持的 OAuth 范围和验证证据见[阶段 6](docs/pre-desktop-stage6-mcp-client.md)。

子进程仅继承 SDK 的必要系统变量和 `env_allowlist`，密钥仍由环境变量提供。命令必须逐项出现在 `allowed_commands`。Server 代码在当前用户权限下运行，目录限制由 Server 实现；只配置受信任的 Server。API 单个连接失败时继续提供内置记忆工具，`/v1/mcp/status` 返回连接状态。

每个 `[[mcp.servers]]` 可设置 `allowed_tools = ["get_datetime"]`，按远程原名仅加载选定工具；不设置时加载全部已发现工具，空列表不加载任何工具，未发现的白名单名称会使该 Server 加载失败。权限仍需显式声明。`local-mcp@latest` 当前支持 Windows/macOS，不支持 Linux；应使用本机 API。只测试时间时配置 `allowed_tools = ["get_datetime"]` 与空 `default_permissions.capabilities`，无需授予文件、进程或外部写入能力。

API/Worker 可用 `MEMORY_AGENT_CONFIG` 环境变量覆盖业务配置路径；默认兼容源码仓库和容器中的 `/app/config.toml`。Server 相对路径基于所选配置文件目录。

Windows 本地 API 使用上述 `--loop app.runtime:event_loop`，以满足 psycopg 异步 Checkpoint 的 Selector loop 要求；MCP SDK 会在独立 Proactor loop 中管理 stdio Session，避免阻塞管道拖延超时回收。Linux/容器可继续使用 Uvicorn 默认 loop。

在 `backend` 目录执行，无需模型 Key：

```powershell
uv run memory-agent tools list
uv run memory-agent mcp list
uv run memory-agent mcp status --api-url http://127.0.0.1:8000
uv run memory-agent --config ../config.toml mcp test filesystem
# 同样支持 uv run python -m app.cli tools list
```

`mcp status` 查询运行中 API，HTTP Basic 密码及本机令牌从相同 Settings 加载（环境变量/`.env`）；远程地址不接收本机令牌。`mcp test` 可测试配置中禁用的 Server，仅连接、握手、发现支持的工具/静态资源/提示词并立即回收，不执行调用；CLI 从 TOML 读取配置，不加载 API 的本机连接覆盖，OAuth 浏览器授权由设置页完成。显式 `--config` 时相对 Server 参数路径基于配置文件目录；应用默认基于仓库根目录。

复制配置示例后先创建独立的 `demo-workspace` 目录，再启用 filesystem Server。默认只授予读取能力；示例已明确覆盖 `write_file/edit_file/create_directory/move_file` 的写权限要求，因此普通 Agent 请求会拒绝写入。容器镜像默认只有 Python；使用 npx/Node Server 时需要自建含 Node 的镜像并挂载独立演示目录。

官方文件系统互操作演示（仓库根目录，需要 Node/npx，首次会下载固定版本 Server）：

```powershell
uv run --project backend python scripts/mcp-filesystem-demo.py
```

脚本只授权新建的临时演示目录，验证读取、显式授予写权限后的写入、拒绝时零远程调用和目录越界业务错误；输出唯一开始/终态审计，并回收 Server 与临时文件。

主回答结束后，前端会继续获取 Worker 写入的记忆提取终态事件，并展示新增记忆、冲突待确认或最终提取失败。运行事件与助手消息持久关联，刷新或切换会话后仍可恢复，重复回放按事件序号去重。

以“请记住/忘记/把……改成……”开头的显式指令会直接进入记忆治理服务：普通事实立即生效，敏感内容进入待确认，凭据和个人号码在本机脱敏后不写入长期记忆。冲突更正先产生 `pending` 新版本；只有用户确认后旧记忆才标记为 `superseded`，撤销会恢复这对版本状态。
