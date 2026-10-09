# Tool / MCP / CLI 改造验收记录

依据：桌面《Memory-Agent-Tool-MCP-CLI-文件级改造方案.md》。按六个阶段执行回归门禁。

## 基线

- 后端 97 passed、4 skipped；前端 4 passed；Ruff 通过。
- 保留已有未提交改动，不回滚用户工作。
- 当前会话未暴露 CodeGraph 工具，使用本地文件与测试检查。

## 阶段 1：统一执行核心

- 新增协议、ToolRegistry、PermissionPolicy、AuditSink、ToolExecutor。
- 参数校验、权限拒绝、超时、取消、内部异常统一处理。
- 所有调用记录唯一 started 和终态；参数仅记录数量；校验错误不回显原始输入。
- 禁止 Schema 外部引用，避免校验时访问外部网络。
- 回归：109 passed、4 skipped；Ruff 通过。未切换生产调用链。

## 阶段 2：内置记忆工具

- 新工具复用 MemoryService、MemoryRepository、embed_text 和 redact_secrets。
- 测试覆盖用户隔离、请求 Session 隔离、参数边界、来源消息、待确认状态与查询脱敏。
- 回归：111 passed、4 skipped；Ruff 通过。

## 阶段 3：LangGraph

- 注入 Function Calling Schema、统一执行器和请求上下文，自定义节点替代直接执行 ToolNode。
- 工具消息保留 call ID，失败明确标记 error，限流时补齐待响应工具调用。
- 请求级数据库工具顺序执行，避免共享 Session 并发。
- 回归：112 passed、4 skipped；Ruff 检查自动修复导入排序。

## 阶段 4：legacy HTTP API

- HTTP 调用经过统一 ToolExecutor，保留原响应字段与内置工具旧名称。
- 来源消息必须属于当前会话且为用户消息；新工具别名同样不能绕过写入检查。
- 删除被迁移替代的 agent/tools.py 与旧 mcp/registry.py；旧执行管线测试由统一执行器覆盖替代。
- 回归：109 passed、4 skipped；Ruff 通过。

## 阶段 5：官方 MCP stdio

- 官方 MCP Python SDK 1.30.0；Server 配置、显式权限、命名、Client、Adapter 与应用级 Manager。
- MCP 快照合并到每次 Agent/legacy HTTP 调用的 Registry；工具级权限覆盖是需求声明，默认权限是会话授予集合。
- 生命周期顺序：Checkpoint → MCP → 应用 → MCP 关闭 → Checkpoint 关闭。
- 真实 SDK 子进程覆盖握手、调用原名、结构化结果、环境变量限制、拒绝、业务失败、超时、崩溃、关闭回收。
- 回归：126 passed、4 skipped。四项原 PostgreSQL 集成测试因测试数据库未配置而跳过；Docker 引擎与本地 PostgreSQL 均未运行。

## 阶段 6：CLI 与官方互操作

- 新增 `memory-agent tools list`、`mcp list/status/test` 与 `python -m app.cli`。
- CLI 测试确认 provider 模块未加载；mcp test 只握手与列工具，禁止 tools/call，并验证子进程回收。
- 官方 `@modelcontextprotocol/server-filesystem@2026.8.31` 在临时独立目录实测：读取成功、显式授权写入成功、权限拒绝零远程调用、目录越界为业务错误、四次调用各一组 started/终态审计。
- 演示脚本：`scripts/mcp-filesystem-demo.py`。固定 Server 版本并检查已审查的工具集合，完成后回收临时文件与子进程。
- 前端展示统一 tool.execution.* 事件，并保留历史事件兼容。
- 阶段门禁：独立 PostgreSQL pgvector 测试库全量 135 passed、无跳过；前端 4 passed，生产构建通过；Ruff 通过。

## 整体审计修复

- 空闲 Server 退出：检测 SDK 读流 EOF，状态转 failed 并撤下后续快照。
- 跨环境配置：兼容仓库根目录与容器 `/app/config.toml`；支持 MEMORY_AGENT_CONFIG，显式配置缺失时报错；子进程工作目录基于配置文件目录。
- Windows 兼容：API 使用 Selector loop 满足 psycopg，SDK Session 在独立 Proactor loop 运行，修复未响应子进程的超时回收延迟；loop 与线程在关闭时回收。
- 禁用与白名单：模型 Schema 过滤启用工具；直接调用隐藏工具仍会在执行器拒绝；legacy 禁用错误继续返回 mcp_disabled。
- Schema：校验支持的方言、Function Calling 名称、局部引用和输入对象；禁止外部引用；校验失败不回显秘密输入。
- SDK 异常区分 JSON-RPC protocol、connection、business、timeout 和 internal；兼容 Python 3.10 的两种 TimeoutError 类型。
- MCP 只有 structuredContent 而没有文本 content 时，仍将结构化结果传给模型，避免工具成功但模型丢失数据。
- 真实端到端测试：一个 Agent Run 使用内置时间线与真实 MCP、参数失败后再一轮成功，PostgreSQL 审计与 Checkpoint 同时验证。

## 需求到证据

| 方案要求 | 实现/证据 |
| --- | --- |
| 协议类型与单一异步 Tool | app/protocol/tools.py、app/tools/base.py、test_tool_protocol.py |
| Registry 职责、三层名称、Schema 与冲突 | app/tools/registry.py、app/mcp/naming.py；test_tool_registry.py、test_mcp_naming.py、manager 映射冲突测试 |
| 参数校验与错误归一化 | app/tools/executor.py；test_tool_executor.py、test_mcp_tool_adapter.py |
| 显式能力权限与异常拒绝 | app/permission/policy.py；test_tool_permissions.py、executor fail-closed 与真实 Server 拒绝测试 |
| 超时/取消、唯一开始和终态 | executor 子任务 finally 回收；外部取消、运行中取消与 handshake timeout 实测 |
| 脱敏审计、复用 run_events | app/audit/sink.py；executor 只记录参数数量；真实 PostgreSQL RunEvent 检查 |
| 三个内置工具迁移、用户/Session 隔离 | app/tools/builtin/memory.py；test_builtin_memory_tools.py、原 MemoryService/治理回归 |
| Function Calling 统一循环、失败标记和 call ID | app/agent/tool_node.py、graph.py；test_agent_tool_node.py、combined Agent/真实 MCP/Checkpoint 集成测试 |
| 轮次上限、Checkpoint 与 SSE | test_config_and_graph.py、test_sse.py、test_postgres_integration.py |
| 请求级 Registry、显式 Manager 注入 | app/tools/request.py、agent/service.py、api/chat.py 与 api/mcp.py |
| stdio 配置与显式权限、允许命令、拒绝重复 ID | app/mcp/config.py；test_mcp_config.py |
| 官方 SDK 连接、初始化、发现、原名调用 | app/mcp/client.py；真实 SDK fixture 与官方 filesystem 演示 |
| isError、结构化 content、可区分错误 | app/mcp/tool.py；test_mcp_tool_adapter.py、真实 stdio 失败/超时/崩溃 |
| Manager 状态、快照、局部失败与关闭回收 | app/mcp/manager.py；test_mcp_connection_manager.py、test_mcp_stdio_integration.py |
| FastAPI 生命周期与退出顺序 | main.py；生命周期顺序测试，实际容器健康与 MCP status 验证 |
| ToolConfig/MCPConfig 分离、旧配置映射提示 | config/business.py、config.toml.example；旧配置/优先级测试，运行时 warning |
| 环境变量最小继承 | 官方 SDK 必要环境 + env_allowlist；真实子进程 secret_env 测试 |
| legacy 响应、用户会话与来源消息 | mcp/transport.py、api/mcp.py；test_mcp.py、test_mcp_api.py 与容器调用/审计回放 |
| 最小 CLI、脚本入口、无模型依赖 | app/cli、pyproject scripts；test_cli_tools.py、test_cli_mcp.py；宿主与容器 CLI 实测 |
| README 与演示脚本 | README.md、scripts/mcp-filesystem-demo.py；固定官方 Server 读取/写入/拒绝/审计实测 |
| 旧文件迁移与不提前创建后期能力 | 删除 agent/tools.py 与旧 MCP 注册器；protocol.py 仅兼容新核心类型和 legacy metadata；无 services/、MCP Server、多 Agent等空壳 |

后续 `/v1/tools`、标准 MCP Server、HTTP transport、OAuth、热更新、自动重连等明确不在本轮范围。当前保留 legacy 路径。

## 最终回归与部署实证

审计日期：2026-10-09。最终源码与锁文件一致，测试使用独立的 PostgreSQL + pgvector 数据库，不使用开发库。

| 检查 | 最终结果 |
| --- | --- |
| Windows / Python 3.10 全量后端回归 | 150 passed，无跳过；包含真实 PostgreSQL、Checkpoint 与 SDK stdio 子进程 |
| Linux / Python 3.12 容器全量后端回归 | 150 passed，无跳过；使用最新审计镜像及同一隔离测试库 |
| 前端事件回归 | 4 passed |
| TypeScript 与 Vite 生产构建 | 通过 |
| `uv run ruff check app tests alembic` | 通过 |
| `uv lock --check` | 通过，依赖锁文件一致 |
| `git diff --check` | 通过；仅有仓库既有 LF/CRLF 转换提示 |
| 官方 filesystem Server 互操作 | 读取、显式授权写入、拒绝零远程调用、目录越界错误、审计与关闭回收通过 |
| Windows 实际 API 启动 | 数据库 available、Checkpoint ready、MCP ready；Selector API 与 Proactor SDK 桥共存 |
| Linux 实际 API 与 CLI | 挂载配置生效、数据库/Checkpoint/MCP ready；CLI status/test 通过；legacy 远程调用及 RunEvent 回放通过 |

Linux 最终测试镜像为 `memory-agent-mcp-audit-api:latest`，构建标识 `sha256:80d0632c193bafe878768900d222b4862e34d93e2033c1843c3fe41bf35b9c00`。审计时另一次旧镜像挂载新测试得到 149 passed、1 failed：旧镜像尚无 structuredContent 消息修复；确认版本后最新镜像完整 150 项通过，不把旧镜像结果计为最终通过结果。Dockerfile 为 API/Worker 共用运行时镜像，启动命令由 Compose 指定；直接 `docker run` 也需要显式指定 API 或 Worker 命令。

隔离测试库仅发布 `127.0.0.1:15432`。Windows 回归的 DATABASE_URL 与 TEST_DATABASE_URL 使用 `127.0.0.1`；使用 `localhost` 的一次复验因 psycopg 先尝试 IPv6 `::1` 而超时，改用与 Docker 绑定一致的 IPv4 地址后复验。容器测试使用 `host.docker.internal:15432`。

离线评测使用既有模型预测快照，36 条样本指标与改造前基线一致：提取 F1 `0.8696`、冲突拦截率 `1.0`、跨用户泄漏率 `0.0`、删除残留率 `0.0`。本轮未调用付费模型或刷新预测快照，不能据此保证真实模型的所有回答质量。

## 落地结论与限制

结论：六阶段改造及门禁完成，本地开发、受控演示与单实例部署可落地；本轮验证范围内未发现未修复的 Tool/MCP/CLI 回归缺陷。测试通过不等于绝对无 bug，也不等于已达到开放公网、多用户生产服务的全部要求。

部署前需要满足：

- 使用 PostgreSQL + pgvector 并执行全部 Alembic 迁移；API 与 Worker 独立运行，模型密钥由环境变量提供。
- 配置受信任的 stdio Server、精确命令白名单、最小环境变量和显式能力；文件工具仅挂载独立目录。MCP Server 以当前系统用户权限执行，能力校验和目录配置不是操作系统沙箱。
- Windows API 使用 README 中的 `--loop app.runtime:event_loop`；Linux/容器可使用默认 loop。容器接入 Node/npx Server 时需自行提供 Node 运行时与目录挂载，默认镜像只有 Python。
- 对外演示开启共享密码并使用 TLS/反向代理、独立数据库与模型 Key。当前 `user_key` 不是身份认证，共享演示密码不能隔离互不信任的真实用户；正式多用户上线应先补账号认证与用户身份绑定，多副本需共享限流存储。
- 保持外部工具副作用的权限审查。当前不做自动重连和 tools/call 自动重试，避免重复副作用；连接失败通过 status 与事件显式呈现。

仍保留改造前的质量限制，不在本次 Tool/MCP 范围内擅自修改：

- `extract-fact-05`：职业目标被已有模型快照误标为敏感信息。
- `extract-irrelevant-03`：一次性午餐被误判为长期记忆。
- `recall-06`、`recall-09`：关键词/语义召回仍有缺口。
- Token 预算采用本地保守估算；历史无版本向量兼容读取，更换 Embedding 模型需要全量重建。

上述 4 条已知质量失败不应被描述为测试全绿或从评测集中删除。真实模型冒烟、目标环境身份与网络策略、备份恢复演练仍属于上线验收工作。

## 测试资源与交付状态

- 本次创建的 Windows 审计 API、MCP fixture 子进程均已退出，PID 检查确认没有遗留。
- 本次 Docker 审计 API、测试容器与临时 PostgreSQL 容器均已回收；临时测试库及其匿名卷已删除，测试数据未备份、不可恢复。
- 未对用户原有 `memory-agent-demo` 的 API、Worker、PostgreSQL 容器执行停止、重建或数据清理；Docker 引擎保持运行。
- 保留最新 `memory-agent-mcp-audit-api:latest` 镜像，便于复现；本次 `.runtime` 审计配置、日志和 PID 记录保留在忽略目录中，它们不是生产配置，用户既有运行文件未清理。
- 改动留在工作区，未创建 Git 提交；保留用户原有未提交内容，没有执行重置或整文件覆盖。
