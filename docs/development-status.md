# 开发状态与后续迭代

更新日期：2026-10-18

## 当前开发进度与文档入口

桌面化前阶段 0 至阶段 3 已完成并通过阶段门禁。阶段 4 的任务工作台、审批、Diff、撤销和 worktree 操作界面已实现，确定性编码修错闭环已加入回归；真实界面演示与独立演示仓库验收仍需在阶段 4 手工复验。

| 阶段 | 状态 | 交付或下一步 |
| --- | --- | --- |
| 0. 可复现基线 | 已验收 | 独立依赖、隔离数据库、完整门禁脚本；见[阶段 0](pre-desktop-stage0-baseline.md) |
| 1. 项目身份与运行控制 | 已验收 | 项目目录绑定、任务状态、取消/中断、目录互斥；见[阶段 1](pre-desktop-stage1-run-control.md) |
| 2. 审批与受控执行 | 已验收 | 文件工具、命令审批、Windows 子进程回收、MCP 调用授权；见[阶段 2](pre-desktop-stage2-controlled-execution.md) |
| 3. Diff、撤销与可选 worktree | 已验收 | 任务基线、归属保护、显式创建、带回及清理；见[阶段 3](pre-desktop-stage3-workspace-changes.md) |
| 4. Agent 编码闭环与任务界面 | 开发完成，手工验收待复验 | 读代码→修改→测试失败→修正→通过；任务工作台、审批、Diff、撤销和 worktree 操作已接入，待真实界面和命令演示 |
| 5. 分层长期记忆 | 待开始 | 个人/项目/临时上下文隔离，worktree 共享项目身份 |
| 6. MCP 客户端扩展 | 待开始 | Streamable HTTP、resources、prompts、鉴权与连接管理 |
| 7. 本地交付与整体审计 | 待开始 | 启动与生命周期、本机访问安全、完整演示与最终审计 |

完整范围与各阶段进入条件见[桌面化前实施与验收方案](pre-desktop-implementation-plan.md)；此前 Tool/MCP/CLI 文件级改造见[历史改造验收记录](tool-mcp-implementation.md)。两套文档的阶段编号属于不同实施批次。

最新门禁（2026-10-18）：后端 **227 passed、0 skipped、0 failed**，前端 **8 passed**；Ruff、TypeScript/生产构建、数据库迁移回退/重升、离线评测基线一致性均通过。阶段 3二次验收的独立基线为后端 226 项、前端 6 项；阶段 4新增审批恢复回归使当前总数增加。阶段 3证据目录为 `.runtime/baseline/db7ba532d3ae40ebadfc9920cb428c06/`，完整复现命令为 `./scripts/verify-baseline.ps1`。

仍有 4 条既有记忆质量失败；当前不具备操作系统沙箱，也尚未完成阶段 7 的本机访问安全与整体发布审计。阶段 3提交 `6a48328` 与阶段 4当前改动需在本地提交后推送远端；阶段 0/1/2 已通过 `4db3e1e` 交付远端。

阶段 4 的实现与自动化门禁见[阶段 4 验收记录](pre-desktop-stage4-agent-task-ui.md)。阶段 4当前仍保留手工浏览器演示、真实界面重连恢复和独立演示仓库命令验收项，不能据自动化测试通过宣称桌面化前全部发布验收完成。

## 历史完成记录

- 步骤 1“异步记忆事件展示”已经验收，并通过 GitHub PR #1 合并到 `main`。
- 步骤 2“完善普通聊天冲突治理”已通过真实 PostgreSQL 验收。
- 普通聊天提取会从当前用户的 `active` 记忆中选择语义冲突候选，模型确认冲突后仍由 `MemoryService` 统一执行版本治理。
- 步骤 3“完善 Embedding 与上下文可靠性”已于 2026-09-11 验收通过；本轮没有混入 MCP 或多 Agent 改动。
- 步骤 4“完成首版验收”已于 2026-09-11 通过完整 7 项复验；步骤 5 MCP 工具调用首轮传输闭环已于 2026-09-12 完成。
- 2026-10-09 文件级改造已完成六阶段门禁：统一 Tool 执行核心、官方 MCP stdio Client、legacy HTTP 兼容迁移与最小 CLI；最终 Windows/Linux 后端各 150 项通过，整体审计及部署限制见 [改造验收记录](tool-mcp-implementation.md)。
- 2026-10-11 用户已确认[桌面化前实施方案](pre-desktop-implementation-plan.md)。阶段 0 已通过当前 worktree 的完整门禁：后端 153 passed、无跳过，前端 4 passed、构建通过，离线评测保持既有 4 条质量失败；复现流程与证据见[阶段 0 验收记录](pre-desktop-stage0-baseline.md)。下一步是项目/任务身份与运行控制；下表中的多 Agent 路线保留为历史后续规划，不纳入本轮。
- 2026-10-17 桌面化前阶段 1 已完成：项目与会话绑定、Run 目录快照、运行中事件、取消/中断、目录互斥及单实例恢复保护。后端 164 passed、无跳过，前端 6 passed、构建通过；迁移升级/回退/重升通过，离线评测不变。交付说明见[阶段 1 验收记录](pre-desktop-stage1-run-control.md)，下一步为阶段 2 审批与受控文件/命令执行。
- 2026-10-17 桌面化前阶段 2 已完成：调用快照审批、精确会话批准与撤销、本地文本文件工具、Windows 原生命令/进程树回收及 MCP 独立调用授权。修复了内部 UUID 被误判为银行卡号而偶发拒绝合法审批的问题。后端 198 passed、无跳过，前端 6 passed、构建、迁移往返及离线基线一致性通过；本地执行默认关闭，当前通过 API 验收，正式界面在阶段 4。交付说明见[阶段 2 验收记录](pre-desktop-stage2-controlled-execution.md)，下一步为阶段 3 任务 Diff、撤销与可选 worktree。

## 历史迭代路线（非本轮执行顺序）

以下保留首版迭代与多 Agent 路线的历史记录；当前开发顺序以上方桌面化前阶段表为准，多 Agent 不纳入本轮范围。历史记录中的“下一步”指当时计划。

| 步骤 | 状态 | 当前交付或进入条件 |
| --- | --- | --- |
| 1. 补齐异步记忆事件展示 | 已验收 | PR #1；真实 PostgreSQL 全量后端测试 70 项通过；前端事件测试 4 项通过；生产构建与依赖审计通过 |
| 2. 完善普通聊天冲突治理 | 已验收 | `conflict-03` 已进入生产链路；真实 PostgreSQL 冲突生命周期通过 |
| 3. 完善 Embedding 与上下文可靠性 | 已验收 | 降级原因可观测；补偿任务绑定内容与模型版本；上下文严格受预算约束 |
| 4. 完成首版验收 | 已验收 | 7 项首版闭环场景、真实 PostgreSQL 综合回归和前端生产构建全部通过 |
| 5. 接入 MCP 工具调用 | 文件级改造已验收 | 统一执行器、官方 stdio Client、CLI 与真实 filesystem 互操作通过；本地/单实例演示可落地，保留文档列明的生产与模型质量限制 |
| 6. 实现最小多 Agent 协作 | 待开始 | MCP 最小工具闭环稳定后进入 |
| 7. 扩展协作调度与可观测性 | 待开始 | 单子 Agent 委派闭环稳定后进入 |
| 8. 建立 MCP 与多 Agent 综合评测 | 待开始 | MCP 与多 Agent 核心能力完成后进入 |

## 步骤 1 验收证据

- 合并提交：`356da8c Merge pull request #1 from zhongjunqi7657/codex/async-memory-events`。
- 助手消息通过 `messages.run_id` 与运行持久关联，迁移版本为 `0007_message_run_link`。
- Worker 追加 `memory.extraction_completed` 或 `memory.extraction_failed`，失败信息在持久化前脱敏。
- 前端通过独立事件跟随模块按 `run_id + sequence` 合并；切换会话会取消旧任务。
- 真实 PostgreSQL 下覆盖成功、最终失败、历史恢复和跨用户隔离。

## 步骤 2 交付

- 新增独立的 `app.memory.conflicts` 模块，负责生成消息向量、取得用户域内的 `active` 候选并复用生产混合排序；候选选择不直接修改记忆状态。
- `MemoryExtractor` 将语义候选单独标记为“优先冲突候选”，并从普通 active 清单中去重；Prompt 明确语义相似不等于冲突，补充事实和无关事实不得替换旧版本。
- 模型仍通过 `contradicts_memory_ids` 或 `canonical_key` 提供冲突证据，`MemoryService` 仍是创建 `pending`、确认替换、拒绝和撤销的唯一入口。
- 离线冲突评测改用同一冲突候选排序，`conflict-03` 增加固定语义向量，不使用地点词表或测试专用判断。
- 回归覆盖目标、偏好、居住地变更，以及跨用户、`pending`、`deleted` 和语义相近但无冲突的记忆。

## 步骤 2 验证结果

- Ruff：`app` 与 `tests` 全部通过。
- 后端：步骤 2 开发时的 75 项回归通过。
- PostgreSQL：3 项隔离测试库用例通过，普通聊天冲突生命周期覆盖 pgvector 候选、用户与状态隔离、确认、撤销和拒绝。
- 前端：4 项测试通过，TypeScript 与 Vite 生产构建通过。
- 离线评测：冲突拦截率由 `0.8` 提升至 `1.0`；已知失败由 5 项降至 4 项；删除残留率和跨用户泄漏率保持 `0.0`。

## 步骤 2 完成标准

- `conflict-03` 通过生产冲突链路，不依赖测试专用分支或硬编码地点词表。
- 目标变更、偏好变更和居住地变更均生成可审计的 `pending` 新版本。
- 确认前旧版本继续参与召回；确认、拒绝和撤销行为有自动化测试。
- 无关事实不会因为语义相近被错误标记为冲突。
- 评测报告明确记录指标变化和剩余质量限制。

## 步骤 3 交付

- 新增独立的 `app.memory.embeddings` 模块，统一处理 Embedding 调用结果、降级原因、内容版本、模型配置版本和记忆元数据。
- 自动召回与 `search_memory` 工具在服务未配置、请求失败或维度不匹配时写入结构化 `memory.embedding_fallback` 事件；前端展示具体原因。
- 补偿任务幂等键和 payload 同时绑定记忆内容哈希、Embedding 模型与维度；Worker 在模型调用前后两次校验版本，内容已变化时跳过旧结果并排队最新版本。
- 新生成的向量记录内容、模型和维度元数据；已知 `pending`、内容漂移或模型配置漂移的向量不参与语义评分。无版本元数据的历史向量暂按兼容模式读取，新补偿写入使用严格模式。
- 长会话摘要按 Token 预算分批推进游标；主回答按“系统预留、长期记忆、最近消息、摘要”统一核算，超预算时优先保留最近消息并明确记录裁剪状态。
- `memory.extraction_completed` 增加向量 ready、pending 和 unavailable 数量，`session.loaded` 增加预算、预留量和裁剪状态。

## 步骤 3 验证结果

- 验收日期：2026-09-11。
- Ruff：`app`、`tests` 与 `alembic` 全部通过。
- 后端：86 项通过，其中 4 项使用真实 PostgreSQL；新增实证确认旧内容补偿任务不会把过期向量写回已编辑记忆。
- 前端：4 项测试通过，TypeScript 与 Vite 生产构建通过。
- 离线评测：36 条固定样本结果保持不变；冲突拦截率 `1.0`，删除残留率和跨用户泄漏率均为 `0.0`，已知失败 4 项。

## 步骤 3 完成标准

- Embedding 不可用的原因可从运行事件区分，而不是统一表现为无信息降级。
- 补偿结果只允许写入匹配的内容、模型和维度版本；过期任务必须跳过并产生最新补偿任务。
- 已知失效向量不得参与语义评分，关键词、时间、重要度和类型信号继续工作。
- 摘要请求和主回答上下文均不超过配置预算，摘要游标不得越过未实际处理的历史消息。
- 用户可在运行详情中看到上下文预算占用、裁剪状态和记忆向量降级数量。

当前剩余验收限制：Token 估算仍是本地保守启发式，并非供应商精确 tokenizer；历史无版本向量采用兼容读取，后续如更换 Embedding 模型仍需按设计执行全量重建迁移。

## 步骤 4 首版验收

验收日期：2026-09-11。

| 场景 | 验收证据 | 结果 |
| --- | --- | --- |
| 1. 不同会话表达目标、偏好和近期经历 | `tests/test_postgres_integration.py::test_sse_chat_recalls_memory_across_conversations` | 通过；跨会话按用户隔离并完成召回 |
| 2. Worker 提取语义/情景记忆并保留来源 | `test_memory_extractor.py`、`test_job_reliability.py`、`test_postgres_integration.py::test_migrated_schema_vector_io_and_worker_locking` | 通过；来源消息、任务幂等和 Worker 锁均有覆盖 |
| 3. 新会话自动召回并生成个性化回答 | PostgreSQL SSE 综合测试中的 `memory.retrieved` 与完成事件断言 | 通过；运行事件包含召回结果和回答 |
| 4. 更正旧信息并生成冲突新版本 | `test_postgres_integration.py::test_postgres_ordinary_chat_conflict_lifecycle`、`test_memory_service.py` 冲突治理回归 | 通过；确认、拒绝和撤销保持版本可追溯 |
| 5. 删除后不再参与检索和回答 | `test_memory_retrieval.py`、`test_memory_service.py`、离线评测删除残留率 | 通过；删除残留率 `0.0` |
| 6. 页面展示召回、工具和记忆变化且默认折叠 | `frontend/src/runEvents.test.ts`（4 项）与 `npm run build` | 通过；事件跟随、去重、取消和生产构建均通过 |
| 7. 离线评测与自动化测试量化闭环质量 | 首版基线后端 `86 passed`、Ruff 全通过、离线评测 36 条样本 | 通过；冲突拦截率 `1.0`、跨用户泄漏率 `0.0`、删除残留率 `0.0` |

### 步骤 4 复验命令结果

- `uv run ruff check app tests alembic`：通过。
- 配置隔离 `memory_agent_test` 数据库后，`uv run pytest -q`：`86 passed`。
- `npm test -- --run`：`4 passed`。
- `npm run build`：Vite 生产构建通过。
- `uv run python -m app.evaluation.runner`：36 条固定样本完成，已知失败 4 项；指标与步骤 3 基线一致。

步骤 5 首轮交付后，完整后端套件（含 MCP 回归）为 `97 passed, 4 skipped`。

步骤 4 仍保留步骤 3 的已知限制：Token 估算是本地保守启发式而非供应商精确 tokenizer；历史无版本向量按兼容模式读取，更换 Embedding 模型时需要执行全量重建迁移。

## 步骤 5 MCP 工具层首轮交付

以下为首轮历史记录。当前已按文件级改造方案完成统一 Tool、官方 MCP stdio Client 与 CLI，原 MCPToolRegistry 已替换；最新分阶段回归与整体审计见 [改造验收记录](tool-mcp-implementation.md)。

- 新增 `app.mcp` 注册器和稳定协议类型；现有三个记忆工具通过注册器注入 LangGraph。
- 工具发现仅返回配置白名单内的名称、描述、JSON 输入/输出 Schema、权限标签和超时配置。
- 调用前执行启用状态与权限检查；参数校验、超时、取消和执行异常统一为稳定错误码，不向模型或客户端暴露原始异常。
- `mcp.tool.started`、`mcp.tool.completed`、`mcp.tool.failed` 和 `mcp.tool.denied` 写入现有运行事件流，前端运行详情可展示这些事件。
- 新增受保护 HTTP 传输：`GET /v1/mcp/tools` 发现白名单工具，`POST /v1/mcp/call` 调用工具并返回 `run_id`、结果和稳定错误结构。
- 每次调用必须绑定 `user_key` 和 `conversation_id`；`propose_memory_update` 还必须提供属于该会话的用户消息 `source_message_id`，不会绕过 `MemoryService` 治理。
- 传输层调用会创建运行记录并持久化 MCP 审计事件；共享密码由 `DEMO_SHARED_PASSWORD` 控制，工具白名单和超时继续由 `config.toml` 控制。
- `backend/tests/test_mcp.py` 与 `backend/tests/test_mcp_api.py` 覆盖发现、白名单、认证、用户域隔离、来源消息校验、参数校验、超时、取消、错误分类和 LangGraph 适配器。

### 步骤 5 验证结果

- `uv run ruff check app tests alembic`：通过。
- `uv run pytest -q`：`97 passed, 4 skipped`；其中 MCP 注册器与 HTTP 传输专项测试 `14 passed`。
- 传输测试覆盖未认证拒绝、发现白名单、跨用户会话拒绝、写入工具来源消息约束、成功调用返回 `run_id` 和审计事件，以及取消后运行标记失败。

本次交付范围以文件级改造方案为准，多 Agent 不属于本轮开发任务。
