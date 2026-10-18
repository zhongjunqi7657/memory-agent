import { useCallback, useEffect, useMemo, useState } from "react";
import { Check, GitBranch, LoaderCircle, Play, RefreshCw, RotateCcw, Square, Terminal, X } from "lucide-react";
import type { TaskExecutionConfig } from "../api";
import {
  applyWorktree,
  createProject,
  createWorktree,
  decideApproval,
  fetchApprovals,
  fetchProjects,
  fetchRun,
  fetchTaskChanges,
  fetchWorktrees,
  previewWorktree,
  undoTask,
} from "../taskApi";
import type { Approval, Project, TaskChanges, Worktree, WorktreePreview } from "../taskApi";

type TaskPageProps = {
  config: TaskExecutionConfig;
  onConfigChange: (config: TaskExecutionConfig) => void;
  runId: string | null;
  sending: boolean;
  onStop: () => void;
  onError: (message: string) => void;
};

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "任务操作失败，请稍后重试";
}

export function TaskPage({ config, onConfigChange, runId, sending, onStop, onError }: TaskPageProps) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [worktrees, setWorktrees] = useState<Worktree[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [runStatus, setRunStatus] = useState<string | null>(null);
  const [changes, setChanges] = useState<TaskChanges | null>(null);
  const [preview, setPreview] = useState<{ operation: "delivery" | "cleanup"; data: WorktreePreview } | null>(null);
  const [loading, setLoading] = useState(false);
  const [projectName, setProjectName] = useState("");
  const [projectDirectory, setProjectDirectory] = useState("");

  const selectedProject = useMemo(
    () => projects.find((project) => project.id === config.projectId) ?? null,
    [config.projectId, projects],
  );
  const selectedWorktree = useMemo(
    () => worktrees.find((worktree) => worktree.id === config.worktreeId) ?? null,
    [config.worktreeId, worktrees],
  );

  const loadProjects = useCallback(async () => {
    setLoading(true);
    try {
      const loaded = await fetchProjects();
      setProjects(loaded);
      if (!config.projectId && loaded[0]) onConfigChange({ ...config, projectId: loaded[0].id });
    } catch (error) {
      onError(errorMessage(error));
    } finally {
      setLoading(false);
    }
  }, [config, onConfigChange, onError]);

  const loadWorktrees = useCallback(async () => {
    if (!config.projectId) {
      setWorktrees([]);
      return;
    }
    try {
      setWorktrees(await fetchWorktrees(config.projectId));
    } catch (error) {
      onError(errorMessage(error));
    }
  }, [config.projectId, onError]);

  const loadRunData = useCallback(async () => {
    if (!runId) {
      setRunStatus(null);
      setChanges(null);
      setApprovals([]);
      return;
    }
    try {
      const run = await fetchRun(runId);
      setRunStatus(run.status);
      const [pending, taskChanges] = await Promise.all([
        fetchApprovals(runId),
        ["completed", "failed", "cancelled", "interrupted"].includes(run.status)
          ? fetchTaskChanges(runId).catch(() => null)
          : Promise.resolve(null),
      ]);
      setApprovals(pending);
      if (taskChanges) setChanges(taskChanges);
    } catch (error) {
      onError(errorMessage(error));
    }
  }, [onError, runId]);

  useEffect(() => { void loadProjects(); }, [loadProjects]);
  useEffect(() => { void loadWorktrees(); }, [loadWorktrees]);
  useEffect(() => {
    void loadRunData();
    if (!runId || !sending) return undefined;
    const timer = window.setInterval(() => void loadRunData(), 1200);
    return () => window.clearInterval(timer);
  }, [loadRunData, runId, sending]);

  async function handleCreateProject() {
    if (!projectName.trim() || !projectDirectory.trim()) return;
    try {
      const project = await createProject(projectName.trim(), projectDirectory.trim());
      setProjects((current) => [project, ...current]);
      setProjectName("");
      setProjectDirectory("");
      onConfigChange({ ...config, projectId: project.id, worktreeId: null });
    } catch (error) {
      onError(errorMessage(error));
    }
  }

  async function handleCreateWorktree() {
    if (!config.projectId) return;
    try {
      const worktree = await createWorktree(config.projectId, "snapshot");
      setWorktrees((current) => [worktree, ...current]);
      onConfigChange({ ...config, executionMode: "worktree", worktreeId: worktree.id });
    } catch (error) {
      onError(errorMessage(error));
    }
  }

  async function handleApproval(approval: Approval, decision: "approve_once" | "approve_session" | "reject") {
    try {
      await decideApproval(approval.id, approval.fingerprint, decision);
      await loadRunData();
    } catch (error) {
      onError(errorMessage(error));
    }
  }

  async function handlePreview(operation: "delivery" | "cleanup") {
    if (!selectedWorktree) return;
    try {
      setPreview({ operation, data: await previewWorktree(selectedWorktree.id, operation) });
    } catch (error) {
      onError(errorMessage(error));
    }
  }

  async function handleApplyPreview() {
    if (!selectedWorktree || !preview) return;
    try {
      await applyWorktree(selectedWorktree.id, preview.operation, preview.data.fingerprint);
      setPreview(null);
      await loadWorktrees();
    } catch (error) {
      onError(errorMessage(error));
    }
  }

  async function handleUndo() {
    if (!runId || !changes?.fingerprint || !window.confirm("确认撤销本次任务的文件修改？")) return;
    try {
      setChanges(await undoTask(runId, changes.fingerprint));
    } catch (error) {
      onError(errorMessage(error));
    }
  }

  return (
    <section className="workspace-page task-page" aria-label="编码任务">
      <div className="page-heading">
        <div>
          <div className="section-kicker">本地编码闭环</div>
          <h1>任务工作台</h1>
          <p>绑定项目后，任务可以读取代码、修改文件、执行验证命令，并保留可审阅的结果。</p>
        </div>
        {sending && <button className="danger-button" onClick={onStop}><Square size={14} />停止任务</button>}
      </div>

      <section className="task-section">
        <div className="task-section-heading"><div><h2>执行目标</h2><p>选择目录和任务隔离方式。</p></div><button className="icon-button" aria-label="刷新项目" onClick={() => void loadProjects()}><RefreshCw size={16} /></button></div>
        <div className="task-grid">
          <label className="task-field"><span>项目</span><select value={config.projectId ?? ""} onChange={(event) => onConfigChange({ ...config, projectId: event.target.value || null, worktreeId: null })}><option value="">选择项目</option>{projects.map((project) => <option key={project.id} value={project.id}>{project.name} · {project.directory}</option>)}</select></label>
          <div className="segmented-control task-mode"><button className={config.executionMode === "original" ? "selected" : ""} onClick={() => onConfigChange({ ...config, executionMode: "original", worktreeId: null })}>原目录</button><button className={config.executionMode === "worktree" ? "selected" : ""} onClick={() => onConfigChange({ ...config, executionMode: "worktree" })}>Worktree</button></div>
        </div>
        {config.executionMode === "worktree" && <div className="task-worktree-row"><label className="task-field"><span>Worktree</span><select value={config.worktreeId ?? ""} onChange={(event) => onConfigChange({ ...config, worktreeId: event.target.value || null })}><option value="">选择或新建 worktree</option>{worktrees.map((worktree) => <option key={worktree.id} value={worktree.id}>{worktree.status} · {worktree.directory}</option>)}</select></label><button className="secondary-button" disabled={!config.projectId} onClick={() => void handleCreateWorktree()}><GitBranch size={14} />新建 worktree</button></div>}
        {!selectedProject && <p className="task-muted">尚未绑定项目。可在下方绑定一个本地目录。</p>}
      </section>

      <section className="task-section project-bind-section">
        <div className="task-section-heading"><div><h2>绑定项目目录</h2><p>目录由服务端校验并按用户隔离。</p></div></div>
        <div className="task-grid"><label className="task-field"><span>名称</span><input value={projectName} onChange={(event) => setProjectName(event.target.value)} placeholder="例如：memory-agent" /></label><label className="task-field"><span>目录</span><input value={projectDirectory} onChange={(event) => setProjectDirectory(event.target.value)} placeholder="C:\\projects\\memory-agent" /></label><button className="primary-text-button task-bind-button" onClick={() => void handleCreateProject()} disabled={!projectName.trim() || !projectDirectory.trim()}><Play size={14} />绑定目录</button></div>
      </section>

      <section className="task-section">
        <div className="task-section-heading"><div><h2>任务状态</h2><p>{runId ? `运行 ${runId}` : "发送任务后，审批和结果会显示在这里。"}</p></div><span className={`task-status status-${runStatus ?? "idle"}`}>{runStatus ?? "未运行"}</span></div>
        {approvals.length > 0 && <div className="approval-list">{approvals.map((approval) => <div className="approval-row" key={approval.id}><div><strong>{approval.tool_id}</strong><p>{approval.warning}</p></div><div className="row-actions"><button className="confirm-action" onClick={() => void handleApproval(approval, "approve_once")}><Check size={13} />本次批准</button>{approval.session_allowed && <button onClick={() => void handleApproval(approval, "approve_session")}>会话批准</button>}<button className="danger-action" onClick={() => void handleApproval(approval, "reject")}><X size={13} />拒绝</button></div></div>)}</div>}
        {runId && changes && <div className="task-result"><div className="task-result-heading"><span><Terminal size={14} />文件 Diff</span><button className="secondary-button" disabled={!changes.changes.some((change) => change.undoable)} onClick={() => void handleUndo()}><RotateCcw size={14} />撤销修改</button></div>{changes.changes.length === 0 ? <p className="task-muted">本次任务没有可见文件修改。</p> : changes.changes.map((change) => <details className="diff-row" key={change.path}><summary><span>{change.path}</span><small>{change.status} · {change.attribution}</small></summary><pre>{change.diff}</pre></details>)}{changes.external_effects && <p className="task-warning">任务执行过外部命令，撤销只会处理可安全归属的文件修改。</p>}</div>}
      </section>

      {selectedWorktree && <section className="task-section"><div className="task-section-heading"><div><h2>Worktree 交付</h2><p>{selectedWorktree.directory}</p></div></div><div className="row-actions"><button className="secondary-button" onClick={() => void handlePreview("delivery")}><GitBranch size={14} />预览交付</button><button className="secondary-button" onClick={() => void handlePreview("cleanup")}><X size={14} />预览清理</button></div>{preview && <div className="operation-preview"><p>{preview.operation === "delivery" ? "交付" : "清理"}预览</p>{preview.data.conflicts.length > 0 ? <ul>{preview.data.conflicts.map((conflict) => <li key={conflict}>{conflict}</li>)}</ul> : <><span>检查通过，可以确认。</span><button className="primary-text-button" onClick={() => void handleApplyPreview()}>确认执行</button></>}</div>}</section>}
      {loading && <div className="task-loading"><LoaderCircle size={15} />正在加载项目…</div>}
    </section>
  );
}
