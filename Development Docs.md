# 长期记忆 Agent Demo 开发文档

> 文档版本：v0.1
>
> 目标工期：8 周以内，每周至少 18 小时，总投入约 144 小时
>
> 定位：面向 AI 应用 / Agent 开发工程师求职的学习型项目
>
> 参考项目：[CowAgent](https://github.com/zhayujie/CowAgent)

## 1. 项目概述

### 1.1 项目目标

构建一个中文优先、单用户、本地优先的个人成长与学习伙伴 Agent。它不追求复刻 CowAgent 的全部能力，而是提炼其中最适合学习和面试展示的一条主线：

```text
跨会话交流
  -> 识别用户信息
  -> 记忆治理
  -> 混合召回
  -> 个性化回答
  -> 用户可见、可控、可评测
```

项目的核心问题不是“模型能不能聊天”，而是：

1. Agent 能否记住用户明确表达的稳定信息和重要经历。
2. Agent 能否在后续会话中只召回相关记忆，而不是把全部历史塞进 Prompt。
3. 新旧信息冲突、用户纠正和删除后，系统是否保持一致。
4. 用户能否看到 Agent 记住了什么，以及这些记忆来自哪里。
5. 召回和提取质量能否通过离线样本和自动化测试持续验证。

### 1.2 首版用户场景

产品名称暂定为“个人成长与学习伙伴”。用户可以连续数周记录学习目标、技术偏好、近期经历和困惑；Agent 在新会话中利用这些信息提供更个性化的回答，并支持主动生成周期回顾。

示例：

```text
第 1 天：我准备学习 LangGraph，希望先理解原理再看代码。
第 5 天：这周主要在补 FastAPI 异步基础。
第 20 天：我决定暂时不准备考研，优先参加秋招。
第 30 天：根据我之前的学习情况，帮我安排下周计划。
```

最后一轮应该能够同时利用稳定偏好、近期阶段和目标变化，但不会把已经删除或被替代的旧目标继续注入。

### 1.3 非目标

首版明确不做以下内容：

- 文件上传、PDF/Word 解析和外部知识库 RAG。
- 浏览器、邮箱、日历、微信、QQ、语音等外部系统。
- 多供应商切换界面。
- 真正的多 Agent 协作、自动推送和复杂调度。
- Electron 桌面端、移动端和复杂前端动效。
- 训练或本地部署模型。

这些能力可以作为后续迭代，但不能影响首版的记忆闭环验收。

## 2. 技术选型与理由

### 2.1 技术栈

| 层次 | 选择 | 用途 |
| --- | --- | --- |
| 后端 | Python + FastAPI | HTTP API、SSE、应用生命周期和后台服务 |
| Agent 组件 | LangChain | Chat Model、Prompt、Tool、Retriever、结构化输出 |
| Agent 编排 | LangGraph 自定义 `StateGraph` | 状态流、条件路由、工具循环、Checkpoint |
| 数据库 | PostgreSQL + pgvector | 业务数据、消息、任务、向量检索 |
| ORM/迁移 | SQLAlchemy 2.0 Async + asyncpg + Alembic | 异步数据库访问和版本化迁移 |
| 前端 | React + Vite + TypeScript | 薄客户端、聊天、记忆管理和事件展示 |
| 实时协议 | SSE | 单向流式回复和结构化运行事件 |
| 部署 | Docker Compose | `api`、`worker`、`postgres` 三个服务 |
| 模型服务 | 阿里云百炼 Qwen OpenAI 兼容接口 | 对话、结构化抽取、Embedding |

### 2.2 模型配置

首版只接入千问，不实现模型切换页面，但代码保留窄的适配器接口：

- `ChatModelProvider`：主对话、工具决策和记忆候选提取。
- `EmbeddingProvider`：记忆文本和查询向量化。

默认模型配置：

- 对话模型：`qwen-plus` 或账号中可用的同级 Qwen Chat 模型。
- Embedding：`text-embedding-v3`。
- 向量维度：`1024`。

百炼的 OpenAI 兼容接口需要配置正确的地域 Endpoint；部分地域的 URL 需要业务空间 ID，API Key 必须与 Endpoint 所属地域匹配。实际模型可用性以账号控制台为准，模型名只放配置文件，不写死在业务逻辑中。

建议配置分离：

```text
环境变量：DASHSCOPE_API_KEY、DASHSCOPE_BASE_URL、DATABASE_URL、运行环境
配置文件：chat_model、embedding_model、embedding_dimension、召回权重、重试次数、上下文预算
代码常量：记忆类型、状态枚举、协议字段名、风险类别
```

API Key 永不进入前端、数据库、日志或提交记录。

## 3. 总体架构

### 3.1 模块边界

```text
app/
  api/              HTTP、SSE、记忆管理和回顾接口
  agent/            LangGraph 状态、节点、路由和工具
  memory/           MemoryService、提取、风险、召回和版本
  models/           Qwen Chat / Embedding 适配器
  jobs/             extraction_jobs Worker
  persistence/      SQLAlchemy、迁移和 Repository
  security/         SecretRedactor、输入限制和数据删除
  evaluation/       离线评测集、指标和回归报告
web/
  chat/             聊天页和 SSE 消费
  memories/         记忆列表、编辑、删除和待处理项
  runs/             默认折叠的执行事件
  settings/         模型连接和应用设置
```

模块职责约束：

- Agent 图只负责编排，不直接写 SQL。
- `MemoryService` 是新增、更新、删除、版本和风险治理的唯一入口。
- Repository 只负责持久化，不决定业务风险。
- Worker 只处理可恢复任务，不参与主回复生成。
- 前端不实现风险规则、召回排序和模型 Prompt。

### 3.2 请求时序

原始用户输入先经过本地脱敏，再进入 Agent 图；这样秘密不会发送给千问，也不会写入消息表。

```text
HTTP 请求
  -> SecretRedactor
  -> 创建 run / 保存已脱敏用户消息
  -> LangGraph
  -> SSE 推送运行事件
  -> 保存助手消息和运行结果
  -> 创建 extraction_job
  -> 关闭本次 SSE

Worker
  -> 领取 extraction_job
  -> 千问结构化提取
  -> Pydantic 校验
  -> 代码风险策略
  -> active 或 pending 记忆
  -> Embedding 成功则写向量，失败则保留空向量并重试
```

### 3.3 LangGraph 状态图

首版使用自定义 `StateGraph`，工具循环复用 LangGraph `ToolNode`；不直接把全部逻辑包在旧式 `AgentExecutor` 中。

```text
load_session
  -> classify_explicit_intent
       ├─ memory_command -> persist_run
       └─ normal_chat
             -> retrieve_memory
             -> call_model
                  ├─ tool_calls -> ToolNode -> call_model
                  └─ final_answer -> persist_run -> enqueue_extraction
```

节点职责：

- `load_session`：加载会话摘要、最近消息和用户身份。
- `classify_explicit_intent`：先用代码规则识别“请记住/忘记/更正”等命令，无法确定时再用千问结构化分类。
- `retrieve_memory`：自动召回 3～5 条高相关有效记忆，作为受控上下文注入。
- `call_model`：生成回答或工具调用。
- `ToolNode`：执行受控工具并记录结构化事件。
- `persist_run`：更新 `runs`、`messages`、`run_events`。
- `enqueue_extraction`：在主回复完成后创建可恢复的异步提取任务。

主 Agent 持有用户关系和长期记忆；未来子 Agent 只能通过受控委派获得必要上下文，不能绕过 `MemoryService` 直接写画像。

### 3.4 Checkpoint 与业务数据分层

- 业务表：`users`、`conversations`、`messages`、`runs`、`run_events`、`memories`、`extraction_jobs`，用于产品页面、报表、评测和审计。
- LangGraph Checkpoint：单独持久化图状态快照，以 `conversation_id` 作为 `thread_id`，支持会话恢复和后续需要时的人工中断。

两层数据不能互相替代。首版记忆审批走图外 API，不使用 `interrupt()` 挂起每一条记忆审批；未来涉及文件删除、发送消息等高风险工具时，再引入 `interrupt()`。

## 4. 长期记忆设计

### 4.1 三级记忆

| 层级 | 生命周期 | 示例 | 首版实现 |
| --- | --- | --- | --- |
| 工作记忆 | 当前会话 | 最近若干轮消息、当前任务状态 | `messages` + 会话上下文预算 |
| 情景记忆 | 中长期、带时间 | “这周在准备 LangGraph 面试” | `memory_type=episodic` |
| 语义记忆 | 稳定画像 | “喜欢先理解原理再看代码” | `memory_type=semantic` |

单次、带明显时间范围的内容默认是情景记忆；稳定自我描述或重复出现的信息才有资格成为语义记忆。情景记忆不能因为一次对话自动晋升为稳定画像，升级需要重复证据或用户手动调整。

### 4.2 MemoryRecord

建议的结构化记录字段：

```text
id                  UUID
user_id             UUID
memory_type        semantic | episodic
content             给模型看的事实文本
status              pending | active | superseded | deleted | rejected
explicitness        explicit | inferred
sensitivity         normal | sensitive | secret
confidence          0..1
importance          0..1
source_message_ids  JSON 数组，保存来源消息 ID
conversation_id     产生记忆的会话
valid_from          生效时间
valid_to            失效时间，可为空
supersedes_id       被替代的旧记忆，可为空
embedding           vector(1024)，可为空
created_at          创建时间
updated_at          更新时间
```

旧记忆不被静默覆盖。发生冲突时创建新候选，旧记录保留并标记为 `superseded`；用户删除时标记 `deleted`，查询和向量召回都必须过滤无效状态。拒绝的候选标记为 `rejected`，便于评测和问题追踪。

### 4.3 事实来源

- 用户消息是唯一事实来源。
- 助手消息只能作为指代解析和上下文，不能单独变成用户记忆。
- “请记住”“忘记”“更正”等显式指令进入专门的记忆意图路径。
- 用户确认助手刚才复述的内容后，只有确认部分才可作为事实。

### 4.4 记忆提取时机

主回复完成后，API 创建 `extraction_job`，由 Worker 异步调用千问提取候选。主回复不等待提取，避免第二次模型请求拖慢聊天；提取失败也不影响主回答。

提取输入包括：

- 当前用户消息（主要事实来源）。
- 本轮必要的最近几轮对话，用于解析代词和上下文。
- 不把助手内容当作事实来源。

提取输出使用 Pydantic Schema，例如：

```json
{
  "content": "用户正在准备秋招后端开发岗位",
  "memory_type": "episodic",
  "explicitness": "explicit",
  "confidence": 0.92,
  "sensitivity": "normal",
  "contradicts_memory_ids": []
}
```

千问只负责提取和标注，不直接决定写入。后端先做 Schema 校验，再由确定性代码策略决定状态。

### 4.5 风险策略

普通、明确、低风险信息自动写入 `active`，并在聊天界面显示本轮新增或更新的内容。以下内容不允许直接自动生效：

1. 敏感信息：健康、财务、精确住址、政治/宗教/性相关信息等，进入 `pending`，不进入稳定画像。
2. 模型推断：用户未明确表达的性格、能力或诊断，如“你有拖延症”。进入 `pending`。
3. 低确定性或短期状态：包含“可能、应该、暂时、最近”等表达，默认作为低优先级情景记忆或进入 `pending`。
4. 与已有记忆冲突：例如“之前准备考研，现在决定参加秋招”，创建新版本并等待确认。

密码、API Key、Token、银行卡号、身份证号等秘密：

- 在本地发送给千问和写数据库之前经过 `SecretRedactor`。
- 原文不写日志、不写 `messages`、不进入 `memories`。
- 使用正则、已知 Key 前缀和高熵字符串组合做检测。
- 识别尽力而为，不能宣称对未知秘密做到百分之百识别；README 明确提醒用户不要发送秘密。

健康、财务、住址等一般敏感内容按既定规则保留在已脱敏的本地会话历史，但不进入长期画像；页面提供删除本轮和清空全部数据。

### 4.6 用户控制

同时支持自然语言和页面操作，最终都进入 `MemoryService`：

- “请记住我喜欢先看原理” -> 新增或更新记忆。
- “我已经不准备考研了” -> 生成冲突更新。
- “忘记我之前说的考研计划” -> 软删除相关记忆。
- “你记住了我什么？” -> 查询记忆工具或记忆页。

普通记忆自动生效并展示；只有推断、敏感、低置信度和冲突内容进入待确认列表。用户可以确认、拒绝、编辑后确认、删除和短期撤销。

## 5. 召回与上下文管理

### 5.1 混合召回

不只依赖向量相似度。候选筛选至少组合：

```text
语义相似度 + 关键词匹配 + 时间衰减 + 重要度 + 记忆类型匹配 + 有效状态
```

建议将各项得分保留在内部事件中，并使用配置化权重：

```text
final_score =
  w_vector * vector_score
  + w_keyword * keyword_score
  + w_recency * recency_score
  + w_importance * importance_score
  + w_type * type_match_score
```

初始权重只是待评测的起点，例如向量 0.55、关键词 0.20、时间 0.10、重要度 0.10、类型 0.05；不能在没有评测的情况下宣称这些权重最优。

### 5.2 自动注入与工具检索

- 每轮自动注入 3～5 条高相关、`active` 状态记忆。
- 同时提供 `search_memory` 工具，模型需要更深历史时主动检索。
- `get_user_timeline` 工具返回按时间排序的目标、经历和变化。
- `propose_memory_update` 只能生成待治理的新增、修改或删除建议，不得绕过 `MemoryService` 直接激活。

### 5.3 长对话上下文

```text
最近消息 + 当前会话摘要 + 按需长期记忆
```

- 工作记忆只保留最近若干轮，并受 Token 预算约束。
- 接近预算时，用千问生成会话摘要，早期消息仍保存在数据库。
- 原始消息用于历史查看、来源追踪和重新生成摘要，默认不全部发送给模型。
- 长期记忆通过自动召回或工具检索获取，不与聊天历史混为一谈。

## 6. 数据库模型

首版使用 7 张核心业务表：

| 表 | 主要职责 |
| --- | --- |
| `users` | 单用户身份；保留 `user_id` 以便未来多租户 |
| `conversations` | 会话标题、会话摘要和时间 |
| `messages` | 已脱敏的用户/助手原始消息 |
| `runs` | 每次 Agent 执行的生命周期、耗时、状态和 Token |
| `run_events` | 记忆召回、工具调用、错误、记忆变化等结构化事件 |
| `memories` | 记忆内容、类型、状态、版本、来源和向量 |
| `extraction_jobs` | 异步提取任务、重试、锁和错误信息 |

`memories.embedding` 使用 `vector(1024)`。更换 Embedding 模型或维度必须执行索引重建迁移，不允许在运行中混用不同维度。

`extraction_jobs` 至少应有：`status`、`attempts`、`available_at`、`locked_at`、`locked_by`、`last_error`、`idempotency_key`、`created_at`、`updated_at`。Worker 使用 PostgreSQL 事务和 `FOR UPDATE SKIP LOCKED` 领取任务，保证多 Worker 时不重复消费。

## 7. 异步 Worker 与兜底方式

### 7.1 任务生命周期

```text
pending -> running -> succeeded
                   -> pending（可重试）
                   -> failed（达到上限）
```

任务必须幂等：同一个 `run_id` 或消息集合只允许产生一次有效提取结果。Worker 重启后，超时的 `running` 任务可以重新变为 `pending`。

### 7.2 失败与降级矩阵

| 故障 | 处理 | 用户可见结果 |
| --- | --- | --- |
| 千问聊天请求超时/临时错误 | 有限次数重试，指数退避；仍失败则结束本次运行并返回明确错误 | 主回复失败原因可见，不写入虚假记忆 |
| 结构化输出无法通过 Pydantic | 最多一次修复重试；仍失败则任务 `failed`，不写记忆 | 记忆提取失败事件 |
| Embedding 请求失败 | 主对话继续；文本和元数据先落库，向量为空，检索降级为关键词 + 时间/重要度 | `embedding_fallback` 事件，Worker 后续补向量 |
| Worker 进程重启 | 未完成任务重新领取 | 任务状态可恢复，不丢失提取机会 |
| SSE 连接断开 | 运行事件已写入 `run_events`，按 `run_id` 查询或重放 | 前端可恢复显示，不重复执行 |
| 数据库暂时不可用 | API 健康检查失败并返回明确错误；不静默切换到另一数据源 | 用户知道本次请求未完成 |
| SecretRedactor 发现明显秘密 | 发送前替换为 `[REDACTED]`，原文不落库 | 对话中显示已脱敏结果 |
| 疑似但无法确定为秘密 | 不自动进入长期记忆，必要时标记待确认 | 不把不确定内容直接写入画像 |

不使用 Celery、Redis 或第二套数据库做首版兜底。数据库和模型服务不可用时，系统应明确失败；只有 Embedding 失败允许在同一条主链路内降级。

## 8. SSE 与可观测性

主 Agent 运行使用单次 SSE 连接，后台记忆 Worker 不与其共享长连接。建议事件类型：

```text
run_started
memory_retrieved
token_delta
tool_call
tool_result
memory_change
run_completed
extraction_queued
embedding_fallback
error
```

事件写入 `run_events` 后再推送或同时推送；前端断线后按 `run_id` 重放。页面默认只展示最终回答，执行事件和“本轮上下文”面板默认折叠，用户主动打开后查看：

- 召回了哪些记忆、来源和相关性分数。
- 使用了哪些工具、参数摘要、状态和耗时。
- 新增、更新、待确认或拒绝的记忆。

不展示原始 Chain-of-Thought，只展示结构化运行事件。

## 9. 前端范围

React 是薄客户端，业务规则全部在后端。首版页面：

1. 聊天页：流式回答、会话切换、本轮记忆变化提示。
2. 记忆页：按类型和状态查看、编辑、删除、确认、拒绝和撤销。
3. 时间线/回顾页：按时间查看重要经历和主动生成周期回顾。
4. 设置页：配置连接状态和非敏感运行参数；API Key 只在后端环境变量中维护。
5. 运行详情：默认折叠的召回、工具、错误和提取事件。

不引入 Redux 等复杂状态框架；先使用 React 状态和少量 API 封装。

周期回顾由用户主动触发，不做自动推送。回顾内容包括本周讨论主题、目标变化、记忆新增/更新/淘汰和下一阶段建议。

## 10. 测试与评测

### 10.1 自动化测试层级

- 单元测试：秘密脱敏、风险分类、生命周期转换、冲突版本、混合召回评分、删除过滤。
- 数据库集成测试：Docker PostgreSQL + pgvector，验证事务、索引、并发领取任务和迁移。
- Graph 测试：Fake Chat Model 验证节点跳转、工具调用、循环终止和错误恢复。
- API 测试：SSE 事件顺序、断线重放、输入限制、单用户隔离和错误响应。
- Worker 测试：幂等、重试、锁超时、Embedding 补偿和失败任务。
- 真实 API 冒烟：本地手动执行少量用例，不进入默认 CI，避免密钥和费用问题。

### 10.2 离线评测集

手写 30～50 个中文场景，覆盖：

- 明确事实是否被提取。
- 无关内容是否被忽略。
- 相关记忆是否被召回。
- 新旧信息冲突是否拦截。
- 删除后是否不再召回。
- 推断内容是否错误地自动生效。
- 不同会话之间是否串数据。

每次修改 Prompt、召回公式或阈值都运行评测，记录：

```text
提取准确率
召回 Precision / Recall
冲突拦截率
删除后残留率
平均响应延迟
Token 消耗
Embedding 降级比例
```

首版不追求学术 benchmark，重点是基线、回归和失败样本。

## 11. 安全、隐私与数据删除

- 单用户免登录，但所有业务表保留 `user_id`。
- API Key、Token、密码等秘密在本地脱敏后才离开本机。
- 密钥不写前端、日志、消息表、记忆表和 Git。
- 一般敏感信息不进入长期记忆，秘密不保存为任何业务记录。
- 记忆删除使用软删除和过滤；“清空全部数据”需要删除消息、记忆、运行事件、任务和 Checkpoint。
- README 明确提示：敏感信息识别是尽力而为，用户不应把秘密发送给系统。
- 线上演示使用独立 Key 和独立数据库，增加共享密码/基础登录、输入长度限制、频率限制和每日 Token 预算。

## 12. 部署与交付

### 12.1 开发环境

前 6 周保证 Docker Compose 可重复运行：

```text
docker compose up postgres
python -m app.api
python -m app.jobs.worker
cd web && npm run dev
```

也可以用 Compose 同时启动 `api`、`worker` 和 `postgres`。本地开发可配置 Fake Provider，不依赖真实 Key。

### 12.2 线上演示

第 7～8 周再部署受保护的演示环境：

- 线上使用独立千问 Key 和独立数据库。
- API Key 只注入后端运行环境。
- 增加共享密码或基础登录、速率限制、输入长度限制和每日预算。
- 预置一组演示数据，并准备录屏，防止现场网络或额度异常。
- 不把没有限流的聊天接口直接公开。

## 13. 版本验收标准

首版必须完整走通以下场景：

1. 用户在不同会话表达学习目标、偏好和近期经历。
2. Worker 提取出语义记忆和情景记忆，并保留来源。
3. 新会话自动召回相关记忆，Agent 给出个性化回答。
4. 用户更正旧信息，系统识别冲突并生成新版本。
5. 用户删除记忆后，后续检索和回答不再使用它。
6. 页面展示本轮召回、工具和记忆变化，默认保持折叠。
7. 离线评测和自动化测试能够量化提取、召回、冲突和删除行为。

只有上述闭环通过，才开始真正的子 Agent 扩展。

## 14. 后续多 Agent 演进

目标形态：一个掌握用户长期记忆的主 Agent，按任务临时委派专长子 Agent，例如学习规划、复盘总结或资料分析。

演进原则：

- 主 Agent 负责用户关系、记忆召回和最终回答。
- 子 Agent 只接收完成任务所需的最小上下文。
- 子 Agent 不直接写 `memories`，只能返回结构化建议给主 Agent/MemoryService 治理。
- 子 Agent 的运行事件和成本独立记录。
- 只有当首版验收和离线评测通过后才实现 `SubagentRunner`。

## 15. 面试主叙事

面试时建议按以下顺序讲解：

1. 为什么不把长期记忆等同于聊天记录或单纯向量库。
2. 为什么把工作、情景和语义记忆分层。
3. 为什么 LLM 只负责候选提取，代码负责风险和生命周期治理。
4. 为什么普通信息自动生效，但推断、敏感和冲突信息需要待确认。
5. 为什么自动召回与 `search_memory` 工具并存。
6. 为什么记忆提取异步化，并用任务表保证可恢复和幂等。
7. Embedding 不可用时为什么关键词降级但主对话不中断。
8. 如何用 30～50 个样本评测提取、召回、冲突和删除质量。
9. 为什么首版只做单主 Agent，以及如何在不重写 MemoryService 的情况下演进到子 Agent。

## 16. 需要在第 0 阶段确认的外部条件

- 千问账号对应的地域 Endpoint 和业务空间 ID。
- 对话模型实际可用名称。
- `text-embedding-v3` 是否已开通，以及 `1024` 维度调用是否成功。
- 本机 Docker Desktop、Python、Node.js 和 PostgreSQL 客户端是否可用。
- 是否准备独立的线上演示 Key 和数据库。

