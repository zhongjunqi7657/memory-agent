# 阶段 7：本地运行交付与整体审计

初次开发日期：2026-10-25。提交前复验日期：2026-11-05。状态：开发及提交前完整自动化门禁已完成；阶段 4 人工演示、阶段 6 用户验收及整体发布结论仍待完成。未迁移开发数据库，也未重启既有开发服务。

## 范围和模块

- `app/local_runtime/checks.py`：Python、Node、Git、配置/连接覆盖、模型凭据存在性、数据库、pgvector、迁移版本及前端产物检查；诊断不调用真实模型、不自动迁移。
- `app/cli/local.py`：`local doctor / migrate / start / backup / restore`，沿用既有 CLI，错误返回非零状态且不打印原始数据库/供应商异常。
- `app/local_runtime/service.py`：单 API、独立 Worker，复用既有事件循环、目录互斥、RunController、MCP Manager、Checkpoint 和数据库租约。
- `app/security/local_access.py`：覆盖所有控制接口的独立本机认证、loopback、Host、Origin 和跨站请求检查。前端请求及 CLI 状态查询随之接入。
- `app/local_runtime/backup.py`：原生 PostgreSQL custom 归档、确认恢复、失败清理及单事务恢复。旧 Compose SQL 脚本补退出码检查和固定 Compose 路径。

没有增加桌面壳、安装包、系统服务、数据库安装器、多 Agent 或新的执行管线。

## 启动与数据目录

安装 Python 3.10—3.13、Node 20+、Git，准备外置 PostgreSQL + pgvector。复制配置示例，填入实际数据库/模型配置，并为本机设置至少 32 位随机 ASCII `LOCAL_API_TOKEN`。令牌属于秘密，只放环境变量或 `.env`，不放业务 TOML。现有 `.env` 不自动覆盖。

```powershell
uv sync --project backend --locked
npm --prefix frontend ci
npm --prefix frontend run build
# 停止旧 API/Worker、备份后显式迁移
uv run --project backend memory-agent local migrate
uv run --project backend memory-agent local doctor
uv run --project backend memory-agent local start
```

访问 `http://127.0.0.1:8000/`，首次请求输入本机令牌。生产构建由 API 根地址提供，保持 MCP OAuth 根地址回调，不通过重定向丢弃授权代码；开发时仍可使用 Vite 5173 端口。改变端口要同步更新 OAuth 回调、受信任 Origin 和前端 API 配置。

| 内容 | 默认位置 | 覆盖和边界 |
| --- | --- | --- |
| 运行秘密 | 仓库 `.env` / 进程环境 | 不因调用时当前目录改变 |
| 业务配置 | 源码定位的 `config.toml` | `MEMORY_AGENT_CONFIG` 必须为绝对路径；CLI `--config` 明确选择 |
| 应用数据 | 仓库 `.runtime` | `APP_DATA_DIR` 必须为绝对路径 |
| API / Worker 日志 | `APP_DATA_DIR/logs/runtime.log`、`worker.log` | 分进程轮转；完整格式化记录及异常文本脱敏 |
| MCP 连接覆盖 | `APP_DATA_DIR/mcp-connections.json` | 不保存 OAuth 令牌 |
| 受管 worktree | `APP_DATA_DIR/worktrees/` | 工作文件需独立备份 |
| 任务基线、变更归属、事件、审批、Checkpoint | PostgreSQL | 不向被操作项目散落应用快照 |

## 生命周期与失败行为

启动诊断通过后先绑定监听端口，再取得数据库单实例租约、初始化 Checkpoint/MCP，最后启动 Worker。API 等待 Worker 加载业务模块并发出就绪信号才报告启动完成；`[runtime].worker_startup_timeout_seconds` 默认 60 秒，超时或初始化退出时关闭本实例资源并报告失败。端口被占用、租约失败或 Checkpoint 未就绪时，不启动记忆处理；不恢复旧工具副作用、不重放批准。

Ctrl+C 或启动器 stdin 的一行 `stop` 触发关闭。Worker 忽略终端广播的 Ctrl+C，由父进程协同停止；运行中记忆事务取消回滚，已领取任务由既有租约补偿机制处理。随后复用 RunController 回收任务/命令、关闭 MCP、Checkpoint，释放 API 租约及数据库连接池。只操作本实例的 Worker 句柄，不按进程名称批量关闭，不停止 PostgreSQL。关闭等待阈值在 TOML `[runtime].shutdown_timeout_seconds`，默认 15 秒；Worker 超时只终止本实例拥有的进程。

Worker 异常退出会使 API 退出并返回失败，不静默保持“可用”；`/health` 在认证后展示受管状态及 Worker 存活情况。直接使用 Uvicorn 与单独 Worker 属于调试模式，各终端需自行关闭。强杀、断电、操作系统崩溃不等于优雅关闭；重启保留既有 Run 中断和任务租约恢复语义，不保证外部副作用自动撤销。

## 本机访问保护

默认 `APP_ENV=local`，非 demo 环境启动必须具有 loopback `APP_HOST` 和有效令牌；即使关闭本地文件工具，控制接口仍需认证。`LOCAL_API_TOKEN` 通过 `X-Local-Token` 传递；`user_key`、工具审批和演示共享密码均不能替代它。

- 请求实际对端、Host 必须是 loopback；非 loopback Host 在令牌正确时也拒绝，防止 DNS rebinding。
- 控制请求有 Origin 时须匹配配置中的本机页面或本机 API 端口；`null`、远程 Origin、`Sec-Fetch-Site: cross-site` 拒绝。无 Origin 的原生调用仍必须持有令牌。公开首页和静态资源仅允许 GET/HEAD 免认证，允许 OAuth 从服务商跨站导航回来；实际控制请求不享有该例外。
- 只对公开静态入口和 assets 放行读取，对 `/health`、`/config`、OpenAPI 文档及全部 `/v1` 路由统一保护。受信任 CORS 预检不等于调用授权。
- 认证拒绝发生在业务路由和数据库依赖之前，错误响应不回显凭据。控制响应 `no-store`，不接受查询字符串中的令牌。
- 前端将本机令牌保存在标签页会话存储，独立于演示密码；拒绝将它发送到远程 API，禁止跟随重定向。只有认证拒绝会触发重新输入，连接失败不重放写请求。
- MCP CLI 状态查询加载相同设置，本机令牌不能发送到远程地址，关闭隐式代理和重定向。
- CORS 包裹本机认证响应并公开 `WWW-Authenticate`，使显式配置的跨端口本机前端能够读取 401 挑战，再提交令牌；CORS 不授予控制权限。

显式 `APP_ENV=demo` 是兼容的共享密码部署，必须设置 `DEMO_SHARED_PASSWORD` 且关闭 `[local].enabled`，不视为本机执行的安全模式。Compose 固定 demo，API 和 PostgreSQL 端口只发布到 loopback，应用数据使用单独卷。

这些措施不提供操作系统沙箱，也不能隔离同一操作系统账户、恶意扩展或已掌握本机令牌的进程。Shell/MCP 仍以当前用户权限运行，批准具体调用前必须检查实际目标。

## 备份与恢复

准备与服务器版本匹配的 `pg_dump` / `pg_restore`，通过 PATH 查找；不自动安装。停止 API 和所有独立 Worker 后：

```powershell
uv run --project backend memory-agent local backup --output F:/memory-agent-backups/before-upgrade.dump
uv run --project backend memory-agent local restore --input F:/memory-agent-backups/before-upgrade.dump --confirm-restore
```

两项操作与显式迁移均持有同一 API 数据库租约，运行中的实例会阻止维护。备份独占创建新归档，完成后原子替换临时文件；失败移除自己的占位和临时文件，不覆盖旧归档。数据库凭据只经子进程环境传递，不放参数、控制台或日志。恢复使用 `--clean --if-exists --exit-on-error --single-transaction`；恢复对象或依赖冲突会整体失败并回滚。

取消等待不会提前释放维护租约：原生迁移/归档命令结束后才允许清理文件和释放锁，避免线程取消后原生命令继续写入时，另一个实例开始维护。

归档包含整个数据库，包括业务记录、事件、审批、任务快照与 Checkpoint。另行备份配置、MCP 连接覆盖、项目文件和受管 worktree；`.env`、OAuth 内存凭据不会额外打包进归档，秘密须自行安全保存。跨机器恢复前核对项目路径；仅恢复数据库无法重建项目文件或 worktree。旧 Compose SQL 归档只能使用旧脚本恢复，不能混用格式。

## 验证记录

2026-11-05 最终完整门禁：后端 **361 passed，零失败/错误/跳过**（211.44 秒），前端 **60 passed / 10 files**。Ruff、依赖锁文件检查、数据库迁移升级/回退/重升、TypeScript/生产构建、离线评测基线比对和差异检查通过。相较阶段 6 新增后端 37 项、前端 4 项。证据目录 `.runtime/baseline/06d7d2387580489b88033e466d49e140/`，完整日志 `.runtime/stage7-final-20261105.log`；本轮临时数据库、网络及 API/Worker 已回收。离线评测 36 条样本中的 4 条既有质量失败保持不变。

2026-11-05 提交前复验补充了以下可复现问题的修复与回归：

- 本机认证中间件拒绝请求时原先缺少 CORS 响应，跨端口前端无法读取令牌挑战；调整包装顺序并公开 `WWW-Authenticate`。
- OAuth 服务商跨站导航回根页面原先被拦截；仅对通过 loopback 对端和 Host 校验的 GET/HEAD 首页及静态资源允许公开读取，控制接口仍校验令牌和 Origin。
- 取消异步维护等待原先可能提前释放数据库租约，而原生命令仍在线程中执行；现在等待命令结束后才清理归档并释放锁。
- Windows 启动器在 spawn 前启动阻塞 stdin 读取线程会使子解释器初始化挂起；改为 Worker 就绪后再启动读取线程，增加就绪握手、初始化超时和失败回收。
- 原生进程测试从检查存活扩展到真实领取持久化提取任务、调用本地确定性模型 HTTP 服务、保存记忆及 1024 维向量、记录完成事件，并由认证 API 查询确认。测试子进程的 loopback 请求显式绕过系统代理；不更改系统代理配置，不调用真实供应商模型。

2026-10-25 历史门禁：后端 **354 passed、零失败/错误/跳过**（200.83 秒），前端 **60 passed / 10 files**。Ruff、锁文件检查、迁移升级/回退/重升、TypeScript/生产构建、离线基线比对与 `git diff --check` 通过。当时新增后端 30 项、前端 4 项；记忆评测仍为 36 条样本、4 条既有质量失败。测试入口：`./scripts/verify-baseline.ps1`，所有数据库验证使用随机命名的隔离 PostgreSQL + pgvector，不连接开发库。

历史证据目录 `.runtime/baseline/3a2299801b554694a4a4a0f55b7027d2/`，日志 `.runtime/stage7-baseline-release.log`；这些是本机忽略产物，新机器应重新运行。PowerShell 脚本 AST、模拟原生命令退出 42 的失败清理/拒绝成功提示、Compose 配置检查亦通过。早期复验曾因 TOML 末尾空行及 Docker 测试适配器端口筛选失败，均已修复。11 月复验曾暴露 Windows Worker 初始化阻塞及测试请求被系统代理转发，按上述修复后重新执行完整门禁，不跳过或豁免失败用例。

新增验证包含：令牌/Origin/Host/对端边界、完整应用路由提前拒绝、错误脱敏；本机令牌重试/拒绝清除/远端不发送/网络失败不重放；仓库外迁移与启动、真实 API 与 Worker、重复实例及维护互斥、正常关闭、Worker 崩溃后应用失败退出；真实 custom 归档往返、中文数据和 Checkpoint 表恢复、损坏归档拒绝及依赖冲突单事务回滚。

本机未安装原生 PostgreSQL 客户端。测试优先使用本机客户端；本轮使用专用 baseline 容器内的官方 `pg_dump` / `pg_restore`，通过测试适配器传输归档，生产代码仍要求原生工具。原生客户端参数和凭据隔离有单元测试，Windows 原生客户端发行包兼容性仍需在安装客户端后复验。官方 filesystem Server 互操作已再次通过，最新证据 `.runtime/stage7-filesystem-20261105.log`；HTTP/stdio fixture 纳入全量后端回归。

## 尚未完成的人工验收

1. 阶段 4：独立演示仓库中由真实模型完成读取、修改、测试失败、修正和测试通过；原生目录选择、审批/停止和完整浏览器重连恢复。
2. 阶段 6：用户确认连接管理、实际服务商 OAuth 和审批使用体验；自动化通过不能替代用户确认。
3. 阶段 7：按上述入口配置本机令牌，人工浏览器验证首次输入、刷新、原生 Ctrl+C 和受控编码演示；在准备原生 PostgreSQL 客户端后复验归档入口。
4. 真实模型记忆效果不在确定性门禁内，保留原有 36 条离线样本及 4 条已知质量失败。

本轮结论限于本地交付代码与自动化验证。人工条目完成前，不宣称整体发布验收通过或已经适合桌面壳正式开发；不交付公网、多用户生产服务或安装包。
