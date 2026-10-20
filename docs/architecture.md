# 系统架构

## 请求与任务边界

```mermaid
flowchart LR
    UI[React 前端] -->|HTTP / SSE| API[FastAPI]
    API --> REDACT[本地秘密脱敏]
    REDACT --> GRAPH[LangGraph 主 Agent]
    GRAPH --> QWEN[Qwen Chat]
    GRAPH --> MEMORY[MemoryService]
    MEMORY --> DB[(PostgreSQL + pgvector)]
    API --> JOB[extraction_jobs]
    WORKER[Worker] --> JOB
    WORKER --> EXTRACT[结构化提取]
    EXTRACT --> MEMORY
    GRAPH --> CHECKPOINT[(LangGraph Checkpoint 表)]
```

业务表与 LangGraph Checkpoint 分离。业务表服务于产品查询、审计和评测；Checkpoint 只保存图状态，以 `conversation_id` 作为 `thread_id`。Checkpoint 不替代消息、运行和记忆记录。

普通聊天先写入脱敏后的用户消息和运行记录，再进入 Graph。主回答完成后创建持久化提取任务，SSE 不等待第二次模型调用。Worker 通过 `FOR UPDATE SKIP LOCKED`、租约、幂等键和指数退避恢复任务；Embedding 失败会创建同类持久补偿任务，文本记忆仍可通过关键词、时间和重要度召回。补偿任务绑定内容哈希、模型名和维度，模型调用完成后再次锁定记忆校验版本，避免把旧内容的向量写回已编辑记录。

`run.completed` 只表示主回答完成，不表示异步记忆提取已经结束。助手消息通过 `run_id` 关联本轮运行；Worker 成功后追加 `memory.extraction_completed`，达到最终重试上限后追加 `memory.extraction_failed`。前端以 `run_id + sequence` 增量获取并去重，切换会话时取消旧跟随；刷新页面时会从会话历史恢复完整运行事件，并继续跟随尚未产生终态事件的提取任务。

对话模型节点通过 `astream()` 逐段消费响应，聚合完整消息供工具路由，同时将可见正文通过 `model.output.started/delta` 持久化并推送至前端实时渲染 Markdown。每次模型输出有独立 `output_id`，工具参数分片完整聚合后才执行，推理块不作为正文发送。部分输出可通过运行事件恢复，成功结束后只保存一次完整助手消息。详细协议与验证见[对话流式生成](model-streaming.md)。

## Graph 状态图

```mermaid
flowchart TD
    START --> LOAD[load_session]
    LOAD --> CLASSIFY[classify_explicit_intent]
    CLASSIFY -->|显式记忆命令| COMMAND[MemoryService 命令路径]
    CLASSIFY -->|普通聊天| RETRIEVE[retrieve_memory]
    RETRIEVE --> MODEL[call_model]
    MODEL -->|tool_calls| TOOLS[ToolNode]
    TOOLS --> MODEL
    MODEL -->|达到循环上限| LIMIT[终止提示]
    MODEL -->|final_answer| PERSIST[持久化回答并排队提取]
    COMMAND --> PERSIST
    PERSIST --> END
```

Graph 只负责编排。会话、召回和工具所需的数据库访问由服务层准备或通过受控工具注入。三个工具分别是 `search_memory`、`get_user_timeline` 和 `propose_memory_update`；最后一个工具只能创建 `pending` 建议，不能绕过治理直接激活记忆。

工具边界由 `app.tools.ToolRegistry` 和 `ToolExecutor` 管理：请求级内置记忆工具与应用级 MCP 远程快照合并，通过自定义 LangGraph 工具节点执行同一 JSON Schema 校验、能力权限、超时、取消和错误归一化流程。`tool.execution.*` 事件写入现有 run_events；参数仅记录摘要。MCPConnectionManager 在 Checkpoint 初始化后连接官方 SDK stdio Session，应用结束时先关闭 Session/子进程，再关闭 Checkpoint。

Legacy Tool HTTP API 由 `GET /v1/mcp/tools` 和 `POST /v1/mcp/call` 提供，并复用 HTTP Basic 共享密码；它不是标准 MCP Server。请求携带用户和会话，记忆写入必须携带当前会话用户消息的 source_message_id。API 每次调用创建 Run，调用统一 ToolExecutor，把审计写入同一运行流。`GET /v1/mcp/status` 查询应用级连接状态；CLI 默认不启动模型，仅列 Schema/配置/状态或临时握手。

## 数据模型

```mermaid
erDiagram
    USERS ||--o{ CONVERSATIONS : owns
    USERS ||--o{ MEMORIES : owns
    CONVERSATIONS ||--o{ MESSAGES : contains
    CONVERSATIONS ||--o{ RUNS : executes
    CONVERSATIONS ||--o{ EXTRACTION_JOBS : queues
    CONVERSATIONS o|--o{ MEMORIES : originates
    RUNS ||--o{ RUN_EVENTS : records
    MESSAGES o|--o{ MEMORIES : sources
    MESSAGES ||--o{ EXTRACTION_JOBS : triggers
    MEMORIES o|--o{ MEMORIES : superseded_by

    MEMORIES {
        uuid id PK
        uuid user_id FK
        uuid conversation_id FK
        enum kind
        enum status
        text content
        decimal confidence
        decimal importance
        jsonb source_message_ids
        vector embedding
        uuid superseded_by_id FK
    }
```

记忆冲突不会静默覆盖。新候选先进入 `pending` 并记录 `conflicts_with`；用户确认时旧记录才变为 `superseded`。撤销确认会成对恢复旧版本。重复证据合并到 `source_message_ids`，避免为同一事实创建重复记忆。

## 召回评分

```text
final_score =
  0.55 * vector
  + 0.20 * keyword
  + 0.10 * recency
  + 0.10 * importance
  + 0.05 * type_match
```

权重来自 `config.toml`，不是代码常量。PostgreSQL 先通过 `embedding <=> query_vector` 的近邻排序取得 HNSW 候选，再与近期候选合并，由纯函数计算五项得分。没有 Embedding 时向量权重退出并重新归一化，其余信号继续工作；各分项随 `memory.retrieved` 事件展示。新向量在元数据中记录内容版本、模型和维度，已知版本失效的向量不参与语义评分；历史无版本向量暂按兼容模式读取。

长会话上下文统一受 `context_token_budget` 约束。系统提示预留、自动注入记忆、最近消息和摘要共同计入预算；摘要刷新只处理当前预算能容纳的连续历史批次，并把游标推进到实际处理的最后一条消息。`session.loaded` 记录估算用量、预算、记忆预留量和裁剪状态。

## 部署与安全

Compose 运行 `api`、`worker` 和 `postgres`。线上演示必须设置独立的 `DASHSCOPE_API_KEY`、数据库和 `DEMO_SHARED_PASSWORD`，并保留输入长度、分钟限流和每日 Token 预算。单进程内存限流只适用于单副本演示，多副本需要外部共享限流存储。

`GET /health` 会真实执行数据库查询；`GET /config` 只返回模型名、阈值和连接布尔状态。备份、恢复、预置演示数据和清空脚本位于 `scripts/`；清空数据同时处理业务表和 Checkpoint 表。
