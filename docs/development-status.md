# 开发状态与后续迭代

更新日期：2026-10-09

## 当前结论

- 步骤 1“异步记忆事件展示”已经验收，并通过 GitHub PR #1 合并到 `main`。
- 步骤 2“完善普通聊天冲突治理”已通过真实 PostgreSQL 验收。
- 普通聊天提取会从当前用户的 `active` 记忆中选择语义冲突候选，模型确认冲突后仍由 `MemoryService` 统一执行版本治理。
- 步骤 3“完善 Embedding 与上下文可靠性”已于 2026-09-11 验收通过；本轮没有混入 MCP 或多 Agent 改动。
- 步骤 4“完成首版验收”已于 2026-09-11 通过完整 7 项复验；步骤 5 MCP 工具调用首轮传输闭环已于 2026-09-12 完成。
- 2026-10-09 文件级改造已完成六阶段门禁：统一 Tool 执行核心、官方 MCP stdio Client、legacy HTTP 兼容迁移与最小 CLI；最终 Windows/Linux 后端各 150 项通过，整体审计及部署限制见 [改造验收记录](tool-mcp-implementation.md)。

## 迭代状态

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
