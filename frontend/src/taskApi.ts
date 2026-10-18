import { parseResponse, requestWithDemoAuth } from "./api";

const apiBase = import.meta.env.VITE_API_BASE_URL ?? "";
const userKey = "demo-user";

export type Project = {
  id: string;
  name: string;
  directory: string;
  created_at: string;
};

export type Worktree = {
  id: string;
  project_id: string;
  directory: string;
  base_commit: string;
  start_mode: "head" | "snapshot";
  status: string;
  omitted: string[];
};

export type Approval = {
  id: string;
  run_id: string;
  call_id: string;
  tool_id: string;
  fingerprint: string;
  binding: Record<string, unknown>;
  status: string;
  session_allowed: boolean;
  expires_at: string;
  warning: string;
};

export type TaskChange = {
  path: string;
  status: string;
  diff: string;
  undoable: boolean;
  attribution: string;
};

export type TaskChanges = {
  fingerprint: string;
  changes: TaskChange[];
  external_effects: boolean;
  conflicts: string[];
};

export type WorktreePreview = {
  fingerprint: string;
  conflicts: string[];
  [key: string]: unknown;
};

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await requestWithDemoAuth(`${apiBase}${path}`, init);
  return parseResponse<T>(response);
}

export function fetchProjects() {
  return json<Project[]>(`/v1/projects?user_key=${userKey}&limit=100`);
}

export function createProject(name: string, directory: string) {
  return json<Project>("/v1/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_key: userKey, name, directory }),
  });
}

export function fetchWorktrees(projectId: string) {
  return json<Worktree[]>(`/v1/projects/${encodeURIComponent(projectId)}/worktrees?user_key=${userKey}&limit=100`);
}

export function createWorktree(projectId: string, startMode: "head" | "snapshot" = "head") {
  return json<Worktree>(`/v1/projects/${encodeURIComponent(projectId)}/worktrees?user_key=${userKey}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ start_mode: startMode, untracked_files: [] }),
  });
}

export function fetchApprovals(runId?: string | null) {
  const query = new URLSearchParams({ user_key: userKey });
  if (runId) query.set("run_id", runId);
  return json<Approval[]>(`/v1/approvals?${query}`);
}

export function decideApproval(id: string, fingerprint: string, decision: "approve_once" | "approve_session" | "reject") {
  return json<{ approval_id: string; decision: string }>(`/v1/approvals/${encodeURIComponent(id)}/decision?user_key=${userKey}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ fingerprint, decision }),
  });
}

export function fetchRun(runId: string) {
  return json<{ run_id: string; status: string; project_id: string | null; execution_mode: string; execution_directory: string | null; worktree_id: string | null }>(
    `/v1/runs/${encodeURIComponent(runId)}?user_key=${userKey}`,
  );
}

export function fetchTaskChanges(runId: string) {
  return json<TaskChanges>(`/v1/runs/${encodeURIComponent(runId)}/changes?user_key=${userKey}`);
}

export function undoTask(runId: string, fingerprint: string) {
  return json<TaskChanges>(`/v1/runs/${encodeURIComponent(runId)}/undo?user_key=${userKey}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ fingerprint, confirm: true }),
  });
}

export function previewWorktree(worktreeId: string, operation: "delivery" | "cleanup") {
  return json<WorktreePreview>(`/v1/worktrees/${encodeURIComponent(worktreeId)}/${operation}?user_key=${userKey}`);
}

export function applyWorktree(worktreeId: string, operation: "delivery" | "cleanup", fingerprint: string) {
  return json<Worktree>(`/v1/worktrees/${encodeURIComponent(worktreeId)}/${operation}?user_key=${userKey}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ fingerprint, confirm: true }),
  });
}
