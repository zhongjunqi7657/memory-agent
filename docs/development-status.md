# 开发状态与后续迭代

更新日期：2026-10-25

## 阶段 6：MCP 客户端扩展（2026-10-20）

已完成标准 Streamable HTTP、tools/静态 resources/prompts、环境变量 Bearer、预注册公共客户端 OAuth 授权码 + PKCE，以及设置页连接创建/编辑、启停、重连与鉴权管理。资源与提示词复用现有 ToolExecutor、审批和审计，审批中展示真实目标；连接配置不等于调用授权。令牌仅存内存，失效不匿名降级，不重放先前调用。明确不支持的可选能力、配置覆盖与 OAuth 范围见[阶段 6 验收记录](pre-desktop-stage6-mcp-client.md)。

最终完整门禁：后端 **316 passed、0 skipped**，前端 **56 passed**；Ruff、锁文件检查、迁移升级/回退/重升、TypeScript/生产构建、差异检查与离线基线一致性均通过，记忆评测的 4 条既有质量失败不变。最终日志 `.runtime/stage6-baseline-release.log`，证据目录 `.runtime/baseline/cfc81fe91aa84a569d73f7bdbe60c132/`。新增后端 27 项、前端 6 项；真实 stdio/HTTP fixture、官方 filesystem Server 互操作和 Edge 隔离连接/OAuth 界面闭环通过。修复了 HTTP 断线/401 被延迟误报为超时的问题，保护关闭期间的旧快照，并确认重连只启动指定服务、畸形请求不回显秘密。

2026-10-20 按用户要求再次全量复测通过：后端 316 项（含 67 项 MCP 相关测试）、前端 56 项及全部门禁通过；官方 filesystem Server 互操作通过，离线评测的 4 条既有质量失败不变，未调用真实模型。此次日志 `.runtime/full-test-20261020.log`、`.runtime/mcp-recheck-20261020.log`，证据目录 `.runtime/baseline/96eefb21f1a04873bc7d98a200826142/`；临时测试数据库已回收。

2026-10-25 提交前审计修复了同源 GET 误拒、未编辑 TOML 连接被固化、OAuth 清除授权与在途令牌结果的竞态、畸形令牌响应以及提示词参数与审批字段冲突。新增 8 项回归测试，最终后端 **324 项**（含 75 项 MCP 测试）、前端 **56 项**及全部门禁通过，官方 filesystem 互操作及隔离浏览器连接列表验证通过；4 条既有质量失败不变。日志 `.runtime/audit-20261025-baseline.log`，证据目录 `.runtime/baseline/7e47249e285041ec8cf3295c4450acb6/`。按用户要求以英文提交说明同步远端，详见[阶段 6 审计记录](pre-desktop-stage6-mcp-client.md#提交前审计与全量回归2026-10-25)。

阶段 6 当前为开发、审计及门禁完成，待用户验收；未重启开发 API/Worker 或变更开发数据库。阶段 4 的完整真实编码命令/重连人工演示仍待完成，阶段 7 尚未开始；此次不宣称已经完成整体发布或本机访问安全审计。

## 阶段 5：分层长期记忆（2026-10-20）

个人记忆与项目记忆已按稳定项目 ID 分层，受管 worktree 共享原项目身份；回答检索读取个人与当前项目，去重、冲突确认/撤销、删除、更正及 Worker 补偿严格限定作用域。项目工具日志和临时尝试不自动提取，稳定约定由用户明确发送“记住…”后进入 MemoryService。记忆管理、侧栏、时间线及周期回顾均传递作用域，切换范围会丢弃迟到请求。

最新补充测试新增 39 个用例（后端 24、前端 15），复现并修复时间线往返切换的迟到响应覆盖及旧范围回顾错误污染。最终完整门禁：后端 **289 passed、0 skipped**，前端 **50 passed**；Ruff、迁移升级/回退/重升、TypeScript/生产构建、差异检查及离线基线一致性均通过，既有 4 条记忆质量失败保持不变。证据目录 `.runtime/baseline/e75fd71fd19243e88964d719332d6101/`；用例矩阵、修复说明与复现步骤见[阶段 5 测试报告](pre-desktop-stage5-test-report.md)。

迁移 `0011_memory_scopes` 保持历史记忆为个人身份，并保护降级时的项目数据。实现、使用方式、接口和自动化证据见[阶段 5 验收记录](pre-desktop-stage5-layered-memory.md)。本次按用户要求推进阶段 5，阶段 4 的未完成人工验收项仍保留，阶段 6/7 尚未开始。随后按用户授权完成开发库备份与迁移，当前版本 `0011_memory_scopes`；API、Worker 与前端已恢复，数据库/Checkpoint 健康，本地工具保持启用。部署记录见阶段 5 文档。

### Delivery notes (2026-10-20, stage 5)

- User acceptance confirmed on 2026-10-20.
- Separate personal and project memories across retrieval, governance, background jobs and the memory interface; share project identity with managed worktrees.
- Add scoped migration and regression coverage, including stale timeline and review responses after scope changes.
- Validation: 289 backend tests and 50 frontend tests passed; Ruff, migration rollback/reapply, TypeScript and the production build passed. Offline evaluation matched the existing baseline with 4 known quality failures.

## 对话流式生成（2026-10-19）

主对话模型调用已由 `invoke/ainvoke` 改为 `stream/astream`：普通回答、工具循环和预算总结均支持逐段正文，经持久化 SSE 事件实时渲染 Markdown。保留工具参数完整聚合、审批/取消、最终消息一次保存、断线和刷新恢复。内部摘要与后台结构化提取保持原有调用方式。

完整门禁通过：后端 **252 passed、0 skipped**，前端 **33 passed**，Ruff、迁移往返、离线基线一致性及生产构建通过。真实 Qwen 网页验证已看到生成过程中的标题/代码块，刷新后正常恢复；后端已重启使改动生效。协议、实现边界与证据见[对话流式生成](model-streaming.md)。

## Main integration (2026-10-19)

- Integrate `codex/ordinary-chat-conflicts` at `b7fe2ff` and `codex/tool-mcp-cli` at `5114813` into `main`, preserving both branch histories.
- Resolve 21 overlapping files against the newer project-task implementation. Production code, the configuration example and dependency locks match `5114813`; the shared Markdown implementation is retained without duplicating it. Replace a timing-sensitive process-cleanup test with an explicit parent/child handshake.
- Validate the combined tree in a separate worktree: 246 backend tests and 27 frontend tests passed, with zero skips; Ruff, migration rollback/reapply, production build and npm audit passed. Offline evaluation matched the recorded baseline, including its 4 existing quality failures.
- This integration does not complete stage 4 manual acceptance or stages 5–7. See the [merge review](main-merge-2026-10-19.md) for scope, evidence and remaining limits.

## Delivery notes (2026-10-18, project-task branch)

- Branch: `codex/tool-mcp-cli`.
- Move project selection, approvals, command output, change review and optional worktree controls into the conversation workflow; restore pending runs from history.
- Include selected project identity in model context and apply an independent project tool budget with a final summary when the budget is exhausted.
- Render new and stored assistant replies and generated reviews as Markdown; keep user input literal and contain wide tables within their own scroll area.
- Validation: the baseline gate passed with 246 backend tests, 27 frontend tests, Ruff, database migration rollback/reapply and the production build. The offline evaluation matched its recorded baseline, including the 4 existing quality failures.

### Delivery notes (2026-10-18, ordinary-chat branch)

- Branch: `codex/ordinary-chat-conflicts`, delivery commit `b7fe2ff`.
- Unify tool execution, permissions and audit events; add the MCP stdio client, legacy HTTP compatibility and CLI commands.
- Render assistant replies and generated reviews with shared Markdown support, including GFM tables and preserved code formatting.
- Historical validation: 148 backend tests passed, followed by the 5 database tests against a separate temporary PostgreSQL instance; 9 frontend tests, Ruff and the production build passed. The combined main-branch validation above supersedes these counts.

## 当前开发进度与文档入口

桌面化前阶段 0 至阶段 3 已完成并通过阶段门禁。阶段 4 仍在开发验收中：按用户确认的 Codex 式交互，在对话框左下角选择/添加项目，选文件夹后自动添加并选中；审批、命令输出与折叠 Diff 均放在对应回复中，Worktree 收在高级选项。独立“编码任务”页已移除，完整真实模型与独立演示仓库验收仍待完成。

| 阶段 | 状态 | 交付或下一步 |
| --- | --- | --- |
| 0. 可复现基线 | 已验收 | 独立依赖、隔离数据库、完整门禁脚本；见[阶段 0](pre-desktop-stage0-baseline.md) |
| 1. 项目身份与运行控制 | 已验收 | 项目目录绑定、任务状态、取消/中断、目录互斥；见[阶段 1](pre-desktop-stage1-run-control.md) |
| 2. 审批与受控执行 | 已验收 | 文件工具、命令审批、Windows 子进程回收、MCP 调用授权；见[阶段 2](pre-desktop-stage2-controlled-execution.md) |
| 3. Diff、撤销与可选 worktree | 已验收 | 任务基线、归属保护、显式创建、带回及清理；见[阶段 3](pre-desktop-stage3-workspace-changes.md) |
| 4. Agent 编码闭环与对话界面 | 开发验收中 | 对话框项目菜单、行内审批、折叠 Diff、历史恢复已接入；真实模型项目识别/读取已验证，完整命令与重连演示待验收 |
| 5. 分层长期记忆 | 已验收 | 用户已确认；个人/项目/临时上下文隔离、Worker 防污染、worktree 共享身份；见[阶段 5](pre-desktop-stage5-layered-memory.md) |
| 6. MCP 客户端扩展 | 开发完成，待用户验收 | stdio/Streamable HTTP、静态 resources/prompts、Bearer/OAuth PKCE、本机连接管理及门禁；见[阶段 6](pre-desktop-stage6-mcp-client.md) |
| 7. 本地交付与整体审计 | 待开始 | 启动与生命周期、本机访问安全、完整演示与最终审计 |

完整范围与各阶段进入条件见[桌面化前实施与验收方案](pre-desktop-implementation-plan.md)；此前 Tool/MCP/CLI 文件级改造见[历史改造验收记录](tool-mcp-implementation.md)。两套文档的阶段编号属于不同实施批次。

最新对话界面、项目识别与工具预算修复回归（2026-10-18）：后端 **246 passed、0 skipped、0 failed**，前端 **20 passed**；Ruff、TypeScript/生产构建及差异检查通过。覆盖自动添加项目、原生窗口取消不添加、选择期间阻止误发送、项目搜索/切换、历史项目及等待审批恢复、停止与提前停止、折叠 Diff 与撤销确认，以及项目上下文传入模型、工具关闭提示。Edge + Qwen 已在独立演示目录实际读取 README，事件确认无命令/写入/删除；API 已重启且数据库/Checkpoint 正常。原生窗口人工选择与完整命令失败修复/重连演示尚待验证。此前阶段 4 回归为后端 227 项、前端 8 项，迁移回退/重升和离线评测基线一致性通过；本次未修改迁移和记忆评测链路。阶段 3 二次验收的独立基线为后端 226 项、前端 6 项，证据目录为 `.runtime/baseline/db7ba532d3ae40ebadfc9920cb428c06/`，完整复现命令为 `./scripts/verify-baseline.ps1`。

“选择项目后模型仍问是哪一个项目”已修复：原先绑定仅存在执行上下文，没有进入模型提示，开发配置又默认关闭本地工具。现请求级注入项目身份/执行目录与有效工具状态，前端在本地或全局工具关闭时明确提示。当前 worktree 使用自身不纳入 Git 的 `config.toml` 启用本地工具，默认示例与原目录配置不变。旧回复和缺失快照保留历史状态，重新发送即可使用修复后的能力；详情及真实读取证据见[阶段 4 记录](pre-desktop-stage4-agent-task-ui.md)。

项目审计只读 4 轮就终止的问题已修复：普通聊天默认 4 轮，项目任务独立默认 24 轮；触顶后停止新工具并根据已有证据生成阶段性报告，明确未检查范围。新增超过 4 轮读取、低预算/零预算与待调用不执行回归。重启后 Edge + Qwen 在用户已绑定项目实际执行 7 轮只读调用并生成阶段性报告，Run 完成、Diff 为空，无命令或文件改动；本次未触及 24 轮上限，触顶总结已有确定性回归。证据见[阶段 4 记录](pre-desktop-stage4-agent-task-ui.md)。

仍有 4 条既有记忆质量失败；当前不具备操作系统沙箱，也尚未完成阶段 7 的本机访问安全与整体发布审计。阶段 3 提交 `6a48328` 与阶段 4 提交 `6583720` 已推送到远端 `codex/tool-mcp-cli`；阶段 0/1/2 已通过 `4db3e1e` 交付远端。本次对话界面与 Markdown 改动的交付范围及验证结果见上方英文交付备注。

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
