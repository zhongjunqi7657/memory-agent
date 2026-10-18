# 桌面化前阶段 0：可复现基线验收

验收日期：2026-10-11。状态：通过，可进入阶段 1。

## 范围与环境

功能源码基线为 `codex/tool-mcp-cli` 的 `2b22694597823c25e50853e1fb9e3cdfeaa9e468`。本阶段只新增回归工作流与文档，没有修改 Agent、记忆或 MCP 业务代码。

| 项目 | 本次环境 |
| --- | --- |
| 操作系统 | Windows 11，10.0.26200 |
| Python | 3.10.8；使用当前 worktree 的 `backend/.venv` |
| uv | 0.12.5 |
| Node / npm | 22.11.0 / 10.9.0 |
| Docker / Compose | 29.7.2 / 5.4.0 |
| 测试 PostgreSQL / pgvector | 16.15 / 0.8.6 |
| Alembic | `0007_message_run_link`，从空库执行全部 7 个迁移 |

Python 环境通过 `uv sync --locked --offline --python 3.10` 在当前 worktree 独立建立，前端通过 `npm ci --no-audit --prefer-offline` 安装。锁文件、模型预测快照及已提交的评测报告均未修改。

测试数据库使用 `docker-compose.baseline.yml`，固定镜像摘要 `sha256:ccc6e83d6e35e931dc7c5def2022729d5a6c370318d099181995567ff1fb4d6b`。每次回归使用随机 Compose 项目名、临时密码、loopback 随机端口和 tmpfs 数据目录，不连接项目原来的 PostgreSQL。

Docker 只用于提供本次真实数据库测试依赖，不代表 Agent 已实现命令沙箱，也不改变“产品 Windows 原生优先、Docker 非强制运行依赖”的决定。

## 最终门禁

| 检查 | 结果 |
| --- | --- |
| 当前 worktree 导入路径与 Python 环境 | 确认指向本 worktree，未继续借用原开发目录环境 |
| `uv lock --check --offline` | 通过 |
| Ruff：`app tests alembic` | 通过 |
| 完整后端回归 | **153 passed，0 skipped，0 failed**；最终运行耗时 22.34 秒 |
| 真实 PostgreSQL 集成 | 5 项均执行，涵盖迁移/vector、Worker、冲突治理、SSE 与 Agent/MCP/Checkpoint |
| 前端 Vitest | 4 passed |
| TypeScript 与 Vite 生产构建 | 通过 |
| 离线评测 | 36 条样本，完整报告与已提交基线一致；未调用真实模型 |
| PowerShell 脚本语法 | AST 解析通过，并完成真实成功路径执行 |
| 失败路径 | 模拟 `npm.cmd` 返回 42，门禁失败且执行清理；环境变量与工作目录恢复通过 |
| 测试集合不被继承变量缩小 | 继承无匹配项的 `PYTEST_ADDOPTS` 时，脚本仍执行全部 153 项 |
| 数据与资源隔离 | 临时数据库、容器与网络均回收；当前 worktree 无遗留 Python 测试子进程 |
| Git 检查 | 空白检查通过；锁文件、预测快照与已提交评测报告无差异 |

最终成功运行的报告在 `.runtime/baseline/bd30fa2823a8480dbcb0aab46c156822/`，包含 `backend.xml` 和 `evaluation.json`。失败路径验证的报告在 `.runtime/baseline/7dd1a1d7b9e64ba9833b7796a460894c/`。这些文件是本机忽略产物，不是源码交付；新机器应执行脚本重新生成证据。

一次并行复验出现 pytest 共享临时目录清理警告（Windows `WinError 145`）。脚本已改为每次运行使用独立 `--basetemp`；最终完整复验没有该警告。Python 标准输出也显式使用 UTF-8，避免中文评测输出乱码，并在脚本退出时恢复调用前设置。

## 保留的质量基线

| 指标 | 本次结果 |
| --- | --- |
| 提取 F1 | 0.8696 |
| 冲突拦截率 | 1.0 |
| 删除残留率 | 0.0 |
| 跨用户泄漏率 | 0.0 |
| 已知质量失败 | 4 |

仍保留 `extract-fact-05`、`extract-irrelevant-03`、`recall-06` 和 `recall-09`：职业目标敏感性误标、一次性午餐误记，以及两项召回缺口。本阶段没有刷新模型快照或修复这些历史质量问题，也没有删除困难样本。因此“回归门禁通过”不是“所有回答质量无缺陷”或“绝对无 bug”。

## 复现

在仓库根目录运行，事先启动 Docker 引擎：

```powershell
uv sync --project backend --locked --python 3.10
npm --prefix frontend ci --no-audit
./scripts/verify-baseline.ps1
```

脚本仅使用专用 Compose 文件；不执行默认 `docker-compose.yml` 的 `up` 或 `down`。它创建并迁移测试库，完成后端、评测与前端门禁，然后在 `finally` 中回收测试数据库和网络、恢复进程环境与工作目录。出现命令失败、后端跳过或评测变化时，不能得到成功标记。

每次运行报告和测试文件都保存在新的 `.runtime/baseline/<随机 ID>/` 内。临时数据库数据位于 tmpfs，回收后不可恢复；报告保留。脚本不刷新预测，不覆盖评测基线，不修改系统代理、系统环境变量或安装数据库。

## 交付状态与下一阶段

- Docker 引擎保持运行。启动引擎后，既有 PostgreSQL 容器由其重启策略自动运行；本阶段没有对它执行迁移、SQL、数据清理或停止操作。
- 原英文提交 `2b22694` 的推送再次因无法连接 `github.com:443` 失败，仍仅保存在本地；未误报为远端已交付。
- 本轮工作流与文档改动保留在工作区，尚未创建新提交或 PR。
- 下一阶段按已确认方案实现项目/任务身份与运行控制，先覆盖事务边界、持久事件、取消及目录绑定测试；每阶段仍使用同样的完整回归门禁。
