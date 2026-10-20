# 阶段 5：分层长期记忆

日期：2026-10-20。开发基线：`main`，`835d042`，工作目录 `F:/memory-agent-demo`。

## 范围与产品规则

本阶段复用现有 MemoryService、混合召回、项目绑定及 Worker，实现个人记忆、项目记忆和临时任务上下文分层。

| 层级 | 身份与读取范围 | 写入规则 |
| --- | --- | --- |
| 个人长期记忆 | `user_id`，`project_id = NULL`；普通聊天和所有所属项目均可召回 | 保留普通聊天的提取、显式指令与确认治理 |
| 项目长期记忆 | `user_id + project_id`；仅当前项目及其受管 worktree | 在项目对话中明确发送“记住…”；继续经过 MemoryService 的秘密拒绝、冲突与确认流程 |
| 临时任务上下文 | 项目固定的会话消息、摘要、Checkpoint 和任务记录 | 项目普通回复不排队长期记忆提取；文件内容、工具日志与临时修复尝试不自动升为记忆 |

原目录与受管 worktree 使用同一个项目 ID，切换执行目录不会另建记忆身份。不同项目可以有同名 canonical_key、相同内容，去重及冲突替换均不跨层。项目约定不被加上“用户…”的个人事实前缀。

为避免模型把临时事实误判成稳定约定，本阶段采用确定规则：项目约定必须由用户明确要求记住，不对项目普通消息做自动长期提取。需要新增个人偏好时，在不绑定项目的普通聊天中表达或发送记忆指令。项目内“查看我的记忆”“忘记…”和更正指令只操作当前项目；个人记忆仍自动参与项目回答，也可从记忆管理的个人范围维护。

## 实现边界

- `memories.project_id` 与 `extraction_jobs.project_id` 新增可空外键；前者增加用户/项目/状态索引。作用域属于持久运行数据，不新增环境变量或业务配置。
- `app/memory/scope.py` 统一 SQL 作用域谓词及项目所有权验证。MemoryRepository 的默认读取范围为个人；主回答与记忆检索/时间线工具显式读取“个人＋当前项目”，管理页、回顾及写入只读指定层。
- MemoryService 的来源去重、证据合并、内容去重、canonical_key 冲突、指定 ID 冲突、确认替换、撤销恢复、忘记及更正均限制作用域。即使 conflicts_with 含其他项目或个人 ID，确认也不能替换它们。项目来源会话须归属同用户和项目。
- 请求级工具注册及 legacy `/v1/mcp/call` 均传递会话项目 ID；模型参数不能另选项目。项目内的工具提议只能来自未脱敏的用户显式保存指令，内容须与指令目标一致，否则返回未创建。
- 提取任务从会话取得项目快照；Worker 校验用户、会话、消息和快照。旧项目提取任务完成为空结果，不创建提取模型，不把历史临时任务升为个人记忆。作用域不一致时沿用已有失败/重试机制。
- Embedding 任务从记忆取得作用域快照，模型调用前以及锁定刷新后检查用户、项目和有效状态；错误项目/用户、已删除记录不生成向量，调用期间发生作用域变化则拒绝写回。既有内容与模型版本校验继续生效。
- 会话的项目绑定沿用阶段 1 的不可切换规则，摘要与消息保留在该会话；新项目开启新会话。每次 Graph 调用替换 Checkpoint 消息、摘要、当前召回，并清空旧 retrieved_memories，避免空召回时回退到旧记忆。自动注入文本标明个人/项目，并计入预算。
- 记忆管理与时间线增加范围选择，确认、删除、编辑、撤销及周期回顾携带作用域。聊天侧栏读取个人和当前项目，显示层级。范围切换立即隐藏旧列表，并丢弃迟到的旧请求结果；失败可见且可刷新。

## API 与迁移

迁移版本：`0011_memory_scopes`，前序为 `0010_workspace_changes`。

- 旧记忆全部保持个人身份，不根据目录、内容或来源会话猜测和重分类；内容、状态、来源及向量保持原值。历史个人条目若含项目细节，需要用户逐项核对处理，本次不会自动迁移其语义。
- 旧提取任务回填来源会话的项目 ID；旧 Embedding 任务仍对应历史个人记忆。
- 不提供移动记忆作用域的接口，项目 ID 不接受编辑；跨作用域需要独立治理。
- `alembic downgrade` 若检测到任意项目记忆会明确拒绝，不把项目私有事实转换为个人记忆。需先备份并显式处理项目记忆；空数据库及仅有历史个人记忆时支持往返。

读取接口 `GET /v1/memories`、`GET /v1/timeline` 默认只读个人层；传 `project_id` 只读该项目。记忆列表可通过 `include_personal=true` 读取个人与该项目。`PATCH /v1/memories/{id}` 与 `POST /v1/memories/{id}/undo` 必须携带条目对应的项目 ID，省略时只能操作个人条目；错误作用域返回 404。`POST /v1/reviews` 的 JSON 可携带 `project_id`。记忆及时间线响应增加可空 `project_id`。

使用现有开发数据库前，先停止 API 与 Worker，备份后在 backend 执行 `uv run alembic upgrade head`，再重启两个进程。自动化门禁仅迁移隔离测试数据库；随后根据用户明确授权执行开发库备份、迁移与服务恢复，结果如下。

## 开发库升级与启动记录（2026-10-20）

- 执行前确认 API/Worker 未运行，开发库无活动 Run 或排队/运行中的 Worker 任务；未终止其他应用进程。
- 先校验 Docker 备份实例与应用连接的数据库系统身份一致，取得应用运行互斥锁，再使用 `pg_dump --format=custom` 备份完整数据库，`pg_restore --list` 验证归档包含记忆和会话数据。
- 备份：`.runtime/start-2026-10-20-stage5/memory-agent-before-0011.dump`，3616614 字节；SHA-256：`cfa737ce11767447e0f0616fbf0861d1954ef56701d31724622110430dea7d6c`。备份含用户数据，仅保存在被 Git 忽略的本机运行目录。
- 开发库从 `0010_workspace_changes` 升级至 `0011_memory_scopes`。升级前后用户 1、项目 2、会话 16、消息 54、记忆 2、提取/补偿任务 27、Run 27，数量一致；两条记忆去除新增作用域字段后的完整行指纹一致，均保留个人身份。提取任务作用域回填无不一致。
- 使用当前 `F:/memory-agent-demo` 源码恢复 API、Worker 及 Vite 前端；沿用此前 `C:/Users/Forever/.codex/worktrees/59ac/memory-agent-demo/config.toml`，保持本地工具启用，数据库连接使用已有 loopback 运行时覆盖。
- API：`http://127.0.0.1:8000`；前端：`http://127.0.0.1:5173`。健康与配置接口 200，数据库 available、Checkpoint ready；三个进程存活，Worker 和前端无启动错误。
- 只读冒烟检查两个现有项目：项目层均为空，包含个人层时各返回两条旧记忆；前端健康代理正常。未为部署验证创建测试会话或写入新记忆。
- 运行及验证记录位于 `.runtime/start-2026-10-20-stage5/`：`migration.json`、`health.json`、`scope-smoke.json`、`processes.json` 和各服务日志；`launch.ps1` 记录本次启动参数，不写入密钥。

## 验证与证据

按用户后续要求补充的边界、失败恢复及界面请求乱序用例，见[阶段 5 测试用例与执行报告](pre-desktop-stage5-test-report.md)，其中记录新增用例、测试发现及修复后的复验结果。下方保留初次开发门禁的历史证据。

首次先增加边界测试，在独立 PostgreSQL 中复现 `2 failed, 252 passed`：原实现不支持项目作用域。初始证据为 `.runtime/baseline/dce72664824c42978f01b56c1bcc1af5/`。实现后完整门禁一轮为后端 263 项、前端 35 项通过；后续补充旧数据迁移及错误 Worker 快照回归，最终完整门禁为 **265 passed、0 skipped**，前端 **35 passed**。Ruff、迁移升级/回退/重升、TypeScript/生产构建、离线基线一致性及差异检查均通过。最终证据目录 `.runtime/baseline/875e5ced4f1b48afa8a01458a30f56fd/`，包含后端 JUnit 与离线评测报告；执行日志 `.runtime/stage5-final-gate.log`。

| 验收点 | 自动化证据 |
| --- | --- |
| 个人跨项目、项目隔离、同键去重与冲突、确认/撤销、删除 | `test_memory_scopes.py`，真实 PostgreSQL 与 pgvector 查询 |
| 秘密拒绝、外部项目所有权、管理接口、时间线、周期回顾、legacy 工具边界 | `test_memory_scopes.py`、`test_memory_scope_flows.py` |
| 原目录/worktree 共用项目记忆、清理 worktree 后记忆保留 | `test_memory_scope_flows.py`，真实临时 Git worktree |
| 临时内容不排队、不被工具提议持久化，旧任务和错误快照不污染 | `test_memory_scope_flows.py` |
| Worker 错误用户/项目、删除状态、有效补偿及调用期间范围漂移 | `test_memory_scope_flows.py`，真实独立数据库连接 |
| 摘要限定会话、项目切换拒绝重绑、旧 Checkpoint 不复活召回 | `test_memory_scope_flows.py`，真实数据库与确定性模型、内存 Checkpoint；既有 PostgreSQL Checkpoint 集成继续回归 |
| 旧记忆升级不变、任务快照回填、项目数据拒绝降级、个人数据往返 | `test_memory_scope_migration.py`，事务内隔离 PostgreSQL schema |
| 请求携带作用域、切换范围丢弃迟到响应 | `api.test.ts`、`useScopedMemories.test.tsx` |

复现完整门禁：在仓库根目录执行 `./scripts/verify-baseline.ps1`。脚本使用随机命名的独立 PostgreSQL + pgvector 测试实例，要求完整后端零跳过，检查迁移往返、Ruff、离线基线、前端测试、TypeScript/生产构建及差异格式；结束后清理自身测试数据库。

本次按用户要求尝试 `codegraph init -i`，本机无可用命令且会话无 CodeGraph 工具，改用定点源码读取；未安装依赖或初始化索引。

## 保留限制

- 离线 36 条样本仍保留原有 4 条质量失败，本阶段不改写评测基线，也不把作用域隔离测试当作模型提取质量提升。
- 第五阶段验证使用确定性模型、真实文件/Git/数据库；未新增真实 Qwen 或手工浏览器演示结论。
- 阶段 4 的完整真实命令修复及重连人工验收仍待完成，阶段 6/7 未实施；受控执行仍不具备操作系统沙箱或阶段 7 的本机访问安全。
