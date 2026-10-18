# 桌面化前阶段 4：Agent 编码闭环与任务界面

验收日期：2026-10-18。状态：实现与自动化门禁通过；手工浏览器演示、独立演示仓库和真实界面重连恢复仍待复验。

## 实际交付行为

任务页面提供项目选择、目录绑定、原目录/worktree 模式、worktree 创建、运行状态、命令审批、Diff、撤销，以及 worktree 交付/清理的预览与确认。任务请求复用现有 `/v1/chat/stream`，携带 `project_id`、`execution_mode` 和 `worktree_id`；停止任务先调用后端取消接口，再中断前端 SSE，避免把断开连接误当成停止执行。

页面请求与类型定义放在 `frontend/src/taskApi.ts`，任务视图放在 `frontend/src/pages/TaskPage.tsx`，通用流式聊天和取消请求仍由 `frontend/src/api.ts` 管理。现有对话、记忆、时间线和设置页面保持原入口；侧栏新增“编码任务”。

Agent 仍使用项目/执行目录和现有 ToolExecutor、ApprovalBroker、RunLifecycle；本阶段没有新增第二套文件或命令执行路径。确定性模型回归覆盖读取文件、写入故障代码、审批执行失败命令、修复文件、再次审批并执行成功，最终 Run 状态为 `completed`，命令退出码记录为 `3, 0`。

## 自动化验收证据

- 后端：`pytest -q` 为 **227 passed**，零跳过；新增用例为 `tests/test_approvals_integration.py::test_agent_repairs_failed_command_and_reaches_completed_state`。
- 前端：`npm test -- --run` 为 **8 passed**；`npm run build` 的 TypeScript 与 Vite 生产构建通过。
- Ruff：`python -m ruff check app tests alembic` 通过。
- 数据库：`0010_workspace_changes` 已验证回退到 `0009_tool_approvals` 后再次升级到 head。
- 离线评测：36 条固定样本与已保存基线一致；已知失败仍为 4 条，冲突拦截率 `1.0`、删除残留率和跨用户泄漏率均为 `0.0`。
- 阶段 3二次验收的独立结果仍为后端 226 项、前端 6 项；阶段 4新增回归没有改变既有接口和迁移。

## 当前未完成的验收项

计划要求的拒绝审批、取消、模型失败、工具轮次上限、超长工具输出、重连恢复和历史事件兼容已有后端/API基础，但本阶段尚未新增一套浏览器自动化验收。真实界面与真实命令应在独立演示仓库中手工复验，并记录启动方式、审批交互、停止后的 Run 状态和重连后的历史事件恢复。

本阶段仍不提供操作系统级沙箱、本机访问认证或自动推送；命令以本地用户权限运行，GitHub 推送必须由用户明确执行并单独验收。
