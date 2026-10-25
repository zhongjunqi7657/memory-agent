# 桌面化前阶段 6：MCP 客户端扩展

更新日期：2026-10-25。状态：开发及审计完成，已通过自动化门禁和隔离环境浏览器验证，待用户验收。基于 `main` 的 `76f3749` 开发；实现和审计修复已通过英文提交 [4d3d903](https://github.com/zhongjunqi7657/memory-agent/commit/4d3d90381a9600b7d90ad4bf716fe3f6a88f8d86) 推送到 `origin/main`，远端提交已核对一致。本次未部署开发库或重启开发 API/Worker。

最新验证为 2026-10-25：后端 **324 项**（含 75 项 MCP 相关测试，零失败/错误/跳过）、前端 **56 项 / 9 个文件**及完整门禁通过。下文 2026-10-20 的 316 项结果为历史证据，最终依据见[提交前审计与全量回归](#提交前审计与全量回归2026-10-25)。4 条既有记忆质量失败、真实模型未复测的限制继续保留。

## 交付范围

- 保留官方 SDK stdio 和 Windows Selector/Proactor 桥，新增标准 Streamable HTTP。legacy `/v1/mcp/tools`、`/v1/mcp/call` 保持兼容，不作为标准 MCP 服务端。
- 初始化后按协商能力发现 tools、静态 resources 和 prompts；未声明的能力不发送发现或调用请求。分页游标重复时停止，避免无限请求。
- 资源和提示词适配为同一 ToolRegistry 中的受控操作，复用 ToolExecutor、ApprovalBroker、RunLifecycle 和审计；资源 URI、提示词名称及参数进入具体调用的审批快照。返回的角色和指令只作为外部数据，不插入系统消息或扩大本地权限。
- 支持匿名、环境变量引用的 Bearer，以及预注册公共客户端的 OAuth 授权码 + PKCE S256。OAuth 支持单次状态、有效期、精确回调、令牌刷新、过期拒绝和本机清除授权。
- 设置页增加连接创建、编辑、启用/禁用、连接/重连、断开、OAuth 授权及清除授权。保存配置不自动启动连接；状态包含工具、资源、提示词数量和脱敏错误类别。

## 模块边界

| 模块 | 职责 |
| --- | --- |
| `backend/app/mcp/config.py` | 传输、权限、白名单、凭据引用与 OAuth 非敏感参数校验 |
| `backend/app/mcp/http_security.py` | HTTP 地址限制、禁止重定向/环境代理、传输失败通知 |
| `backend/app/mcp/auth.py` | 环境变量 Bearer、内存 OAuth 状态/令牌、PKCE 与刷新 |
| `backend/app/mcp/client.py` | 官方 SDK 生命周期、能力协商、发现与调用、取消和错误归一化 |
| `backend/app/mcp/catalog.py` | 资源/提示词到统一工具协议的适配和审批目标展示 |
| `backend/app/mcp/manager.py`、`store.py` | 连接快照、启停、配置覆盖记录及原子保存 |
| `backend/app/api/mcp_connections.py` | 受本机来源限制的连接管理 API |
| `frontend/src/mcpApi.ts` | 连接请求与类型 |
| `frontend/src/components/McpConnections.tsx`、`McpOAuthCallback.tsx` | 连接表单、状态、授权入口与回调处理 |

没有新增数据库表或迁移。HTTPX 从已有开发依赖提升为直接运行依赖，锁定版本不变。

## 配置与凭据

业务配置仍放在 `config.toml`。设置页维护的本机连接覆盖保存在 `APP_DATA_DIR/mcp-connections.json`，默认是仓库忽略目录 `.runtime/mcp-connections.json`；它是用户连接记录，不保存历史运行或令牌。同 ID 覆盖 TOML 条目，其余 TOML 条目保留；命令白名单和全局开关仍由 TOML 管理。相对 stdio 参数路径沿用配置文件所在目录。

在设置页编辑 TOML 来源连接后，会产生同 ID 的本机覆盖；后续修改 TOML 时应同时检查覆盖记录。应用重启会加载启用的连接；OAuth 因令牌不落盘，需要重新授权。配置疑似包含凭据时拒绝保存，历史含疑似凭据的配置不会返回给界面。

Streamable HTTP 示例已加入 `config.toml.example`，默认禁用：

```toml
[[mcp.servers]]
id = "service"
enabled = true
transport = "streamable_http"
url = "https://example.com/mcp"

[mcp.servers.auth]
type = "bearer"
token_env = "MCP_SERVICE_TOKEN"

[mcp.servers.default_permissions]
capabilities = ["network.access"]
```

`MCP_SERVICE_TOKEN` 必须存在于 API 进程环境中，例如启动 API 前在当前 PowerShell 设置 `$env:MCP_SERVICE_TOKEN`；任意自定义变量仅写入 `.env` 不会自动导出为进程环境。不要把令牌填入地址、命令参数、配置或聊天。未找到凭据直接拒绝，不降级匿名请求。

`allowed_tools` 使用远程工具名，`allowed_resources` 使用精确 URI，`allowed_prompts` 使用提示词名；省略表示加载全部，空列表表示不加载。白名单引用未发现条目时该连接加载失败。资源和提示词分别使用 `tool_permissions."resource:<URI>"`、`tool_permissions."prompt:<名称>"` 覆盖所需权限，未覆盖时沿用 `default_permissions`；声明权限不等于批准调用。

## OAuth 支持范围

当前支持预注册、无客户端秘密的公共客户端；显式配置 `client_id`、`authorization_url`、`token_url`、`redirect_uri` 和 `scope`。回调必须是当前受信任本机前端的根地址，例如 `http://127.0.0.1:5173/`，并与服务商登记一致。

1. 设置页点击“授权”，后端生成随机 state 和 PKCE verifier，返回授权链接。
2. 点击“继续授权”后，浏览器在当前会话中保存 state，并跳转到配置的授权端点。
3. 返回前端后立即从当前 URL 移除 code/state；以 POST 请求提交回调，后端核对单次 state、有效期、回调和连接配置快照后交换令牌。
4. 令牌只保存在 API 进程内存中。过期时有 refresh token 才尝试刷新，刷新失败清除授权；401/403 使连接失效，不重试该 MCP 请求。
5. “清除授权”断开连接并丢弃本机临时/访问/刷新凭据；远端授权的撤销仍须在服务商页面完成。

授权等待期通过 `authorization_timeout_seconds` 配置，默认 300 秒；服务商未返回 `expires_in` 时，使用 `default_token_ttl_seconds`，默认 3600 秒。凭据不写配置、长期记忆、运行事件或仓库。回调错误和校验错误不回显原始 code 或令牌。前端仅移除当前历史条目的查询参数，不承诺清除浏览器、代理或服务商自己的历史记录。

未实现动态客户端注册、授权服务器自动发现、机密客户端、设备授权、OS 凭据库和远端 revocation endpoint；不能据此宣称兼容全部服务商的 OAuth 扩展。实际第三方服务商还需按其注册信息复验。

## 网络、连接与权限边界

- HTTP 服务和 OAuth 端点只能是显式配置的 HTTPS 地址；明文 HTTP 只允许 loopback。拒绝 URL 内凭据、查询参数、片段和控制字符，拒绝全部 HTTP 重定向，不继承环境代理。出站 TLS 继续校验。
- 本阶段只允许本机客户端和受信任 loopback Origin 管理连接，并复用已有共享密码验证；写请求必须提供 Origin。同源浏览器 GET 通常不带 Origin，仅在 Sec-Fetch-Site 为 same-origin 且 Referer 来源受信任时允许读取。其余缺失来源、跨站和远端请求拒绝。此局部限制不替代阶段 7 对所有控制 API 的本机认证与 Origin 审计。
- HTTP 失败通知直接中断等待中的 SDK 请求。真实测试曾发现断线/401 被延迟表现为超时，现已修复并加入回归，错误分别保留 connection/permission 类别。
- 断开/编辑/禁用立即撤下后续工具快照；连接关闭后旧请求快照不能继续调用。显式重连重新发现能力，不重放之前批准的操作；正在执行的外部副作用不能承诺回滚。
- 不自动重连或重试工具写入。HTTP 无持续连接的服务空闲失联可能到下一次请求才被发现，设置页可手动刷新状态。
- stdio 仍以当前用户权限运行，HTTP Server 也可能修改远端数据。连接管理、能力声明和逐次审批均不是操作系统沙箱。
- 不声明 sampling、elicitation、roots 或 tasks 等可选双向能力；SDK 默认拒绝未协商的 sampling/elicitation 请求。本轮只暴露静态资源，不展开 resource templates，也不实现订阅、资源链接自动抓取或 listChanged 自动热更新；需要时手动重连重新发现。

## 管理接口

所有下列接口均要求本机来源限制；它们只管理连接，不提供绕过审批的资源读取或提示词执行入口。

| 方法与路径 | 行为 |
| --- | --- |
| `GET /v1/mcp/connections` | 非敏感配置、状态及允许命令 |
| `POST /v1/mcp/connections` | 创建/更新配置，保存后保持断开 |
| `POST /v1/mcp/connections/{id}/connect` | 显式连接/重连与能力发现 |
| `POST /v1/mcp/connections/{id}/disconnect` | 断开并撤下快照 |
| `POST /v1/mcp/connections/{id}/oauth/start` | 创建单次授权尝试 |
| `POST /v1/mcp/connections/oauth/complete` | 校验回调、交换令牌并连接 |
| `POST /v1/mcp/connections/{id}/oauth/revoke` | 断开并清除本机授权 |

资源/提示词以受控工具出现在 Agent 或 legacy 工具发现中；内容适配 ID 使用确定性摘要，审批快照会补充实际资源 URI/提示词名供用户检查。

## 验证证据

2026-10-20 开发完成时的历史门禁：后端 **316 passed、0 skipped**，前端 **56 passed**，Ruff、锁文件检查、迁移升级/回退/重升、TypeScript/Vite 构建及 `git diff --check` 通过。JUnit 记录后端用时 121.730 秒。36 条离线样本与已提交基线一致，4 条既有质量失败未增加；没有调用真实 Qwen 或刷新预测快照。最终日志 `.runtime/stage6-baseline-release.log`，JUnit/评测目录 `.runtime/baseline/cfc81fe91aa84a569d73f7bdbe60c132/`；较早的门禁另存于 `.runtime/stage6-baseline.log` 和 `.runtime/stage6-baseline-final.log`，分别对应 `76bf7ee3694a45c8905363a06f9a03eb`、`31232deb1d7a4deeb8cfd167cff3ba5f`，不作为最新代码的最终证据。

新增 27 个后端用例和 6 个前端用例；既有 stdio 集成用例同时扩展 resources/prompts 验证：

| 覆盖范围 | 主要证据 |
| --- | --- |
| HTTP/stdio 配置、URL、秘密引用及回调边界 | `test_mcp_http_config.py` |
| 真实 HTTP 握手、tools/resources/prompts、匿名/Bearer、拒绝零执行、取消、超时、断线、旧快照、401 不重试 | `test_mcp_http_integration.py`、`fixtures/mcp_http_server.py` |
| 真实 OAuth 授权码 + PKCE、刷新、单次/错误 state、本机撤销 | 同一真实 HTTP fixture |
| 未协商能力不请求、重复分页停止、未支持双向请求拒绝、过期状态和配置变化拒绝、HTTP 重定向拒绝 | `test_mcp_capabilities.py` |
| 本机来源、持久化恢复、配置不自动执行、命令/凭据校验、不回显秘密、回调源限制 | `test_mcp_connections_api.py` |
| 多连接中只重连目标，其他保存或断开的服务不被连带启动 | `test_mcp_connection_manager.py` |
| 真实 stdio 资源/提示词读取及原进程/超时/权限回归 | `test_mcp_stdio_integration.py` |
| 连接显示、保存不启动、禁用保留权限、授权链接、回调清理与 state 拒绝 | `McpConnections.test.tsx` |

官方 `@modelcontextprotocol/server-filesystem@2026.8.31` 再次互操作通过：读取、显式授权写入、权限拒绝时零远程调用、目录越界业务错误、唯一开始/终态审计以及进程回收；日志 `.runtime/stage6-filesystem.log`。

Edge 实测使用独立前端 `127.0.0.1:15173`、真实连接管理 API `127.0.0.1:18006`、本机 HTTP fixture `18007/18008`。页面其余数据为验收桩，不连接开发库、不调用模型。已完成：添加/编辑、保存后不连接、手动连接显示 2 tools/1 resource/1 prompt、断开、OAuth 浏览器跳转/回调清理/连接成功、清除授权后再次连接提示凭据缺失、禁用后连接/授权按钮不可用。测试配置和启动代码保存在忽略目录 `.runtime/stage6-ui/`；这不等同于已经部署开发 API，也不替代阶段 4 的真实编码修错演示。

复现完整门禁：仓库根目录运行 `./scripts/verify-baseline.ps1`；官方互操作运行 `uv run --project backend python scripts/mcp-filesystem-demo.py`。专项后端测试为上述四个新增测试文件和 stdio 集成测试；前端专项为 `npm test -- src/components/McpConnections.test.tsx`。

### 全量复测（2026-10-20）

按用户要求重新运行 `./scripts/verify-baseline.ps1`：后端 316 项通过，0 失败、0 错误、0 跳过（JUnit 115.186 秒），其中 67 项 MCP 相关后端测试通过；前端 9 个测试文件、56 项通过。Ruff、锁文件检查、迁移升级/回退/重升、TypeScript、生产构建和差异检查全部通过。36 条离线评测与已提交基线完全一致，仍有 4 条既有质量失败，未调用真实模型。

本次完整日志 `.runtime/full-test-20261020.log`，JUnit 与评测证据目录 `.runtime/baseline/96eefb21f1a04873bc7d98a200826142/`。官方 filesystem Server 互操作再次通过，读取、授权写入、拒绝零远程调用、越界拦截、审计与关闭均正常，日志 `.runtime/mcp-recheck-20261020.log`。临时 PostgreSQL 容器、卷和网络已回收，未变更开发数据库。会话 MCP 工具调用有正常响应，但未提供 CodeGraph 工具，资源与资源模板列表为空；这与项目 MCP 客户端的互操作结果分别记录。

## 交付与剩余事项

### 提交前审计与全量回归（2026-10-25）

审计覆盖本阶段全部未提交代码、测试、配置示例和文档，重点检查连接生命周期、鉴权、审批绑定、持久化与前端请求。新增 `test_mcp_audit_regressions.py` 的 8 项回归测试，修复前复现 7 项失败、1 项通过，修复后全部通过：

| 问题 | 修复 |
| --- | --- |
| 浏览器同源 GET 没有 Origin，设置页连接列表被误拒 | 只对同源 GET 接受受信任 Referer；写请求仍要求 Origin，跨站与无来源请求拒绝 |
| 保存任意连接时把未编辑的 TOML 连接一起固化，后续 TOML 更新失效 | 仅持久化显式编辑的连接，保留其他 TOML 配置的更新；写盘成功后再更新运行状态 |
| 清除授权期间迟到的令牌交换或刷新结果可恢复凭据 | 授权版本及当前令牌身份校验，拒绝已撤销的在途结果 |
| 非对象 OAuth 响应或错误类型的刷新令牌未正确拒绝 | 校验响应结构并返回统一鉴权错误 |
| 提示词参数与审批元数据字段重名时被覆盖或丢弃 | 原参数独立嵌套保存，实际 URI/名称仍单独绑定审批 |

最终完整门禁通过：后端 **324 passed、0 failures/errors/skipped**（其中 75 项 MCP 相关测试，JUnit 268.678 秒）、前端 **56 passed / 9 files**；Ruff、锁文件检查、迁移升级/回退/重升、TypeScript、生产构建、差异检查及离线基线一致性通过。36 条离线样本仍有 4 条既有质量失败，未调用真实模型。完整日志 `.runtime/audit-20261025-baseline.log`，JUnit/评测证据目录 `.runtime/baseline/7e47249e285041ec8cf3295c4450acb6/`。

官方 filesystem Server 互操作复测通过，日志 `.runtime/audit-20261025-filesystem.log`。隔离浏览器在 `127.0.0.1:18017` 使用真实管理路由和生产构建，确认同源 GET 能显示 `audit-browser` 连接；其余页面数据使用桩，不连接开发库或模型。截图 `.runtime/audit-20261025-ui/connections.png`，测试代码与配置保存在同一忽略目录。隔离服务和本轮门禁临时数据库均已回收。

阶段 6 代码、回归测试和审计文档已于 2026-10-25 通过 `4d3d903` 推送到 `main`，提交标题为 `Add stage 6 MCP connections and fix audit regressions`。未重启开发 API/Worker、未迁移或修改开发数据库；使用本轮修复前需要将开发 API 重启到当前代码。CodeGraph 命令不可用，用户已明确允许本轮不使用。审计与测试没有发现尚未修复的发布阻塞问题；上述结果不构成绝对无缺陷或完整安全审计的保证。

阶段 4 的独立演示仓库真实模型命令失败修复、原生窗口人工选择和完整重连演示仍待验收；阶段 7 的启动/生命周期、本机访问安全、备份恢复及整体审计尚未开始。记忆质量仍保留 4 条既有失败，不以本阶段门禁替代真实模型效果复测。
