import { useEffect, useRef, useState } from "react";
import { ChevronDown, FolderOpen, Plus, Search } from "lucide-react";
import { fetchPublicConfig } from "../api";
import type { TaskExecutionConfig } from "../api";
import { chooseProjectDirectory, createProject, fetchProjects } from "../taskApi";
import type { Project } from "../taskApi";
import { WorktreeOptions } from "./WorktreeOptions";

type Props = {
  config: TaskExecutionConfig;
  locked: boolean;
  disabled: boolean;
  onChange: (config: TaskExecutionConfig) => void;
  onError: (message: string) => void;
  onBusyChange: (busy: boolean) => void;
};

export function ProjectMenu({ config, locked, disabled, onChange, onError, onBusyChange }: Props) {
  const [open, setOpen] = useState(false);
  const [projects, setProjects] = useState<Project[]>([]);
  const [busy, setBusy] = useState(false);
  const [path, setPath] = useState("");
  const [query, setQuery] = useState("");
  const [localAvailable, setLocalAvailable] = useState<boolean | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const selected = projects.find((project) => project.id === config.projectId);

  useEffect(() => {
    let active = true;
    void fetchProjects().then((loaded) => { if (active) setProjects(loaded); })
      .catch((error: Error) => { if (active) onError(error.message); });
    void fetchPublicConfig().then((loaded) => {
      if (active) setLocalAvailable(loaded.local.enabled && loaded.tools.enabled);
    }).catch(() => { if (active) onError("无法检查项目文件访问状态，请重试"); });
    return () => { active = false; };
  }, [open, config.projectId, onError]);

  useEffect(() => {
    if (!open) return;
    function dismiss(event: PointerEvent) {
      if (!menuRef.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, [open]);

  function select(projectId: string | null) {
    if (projectId !== config.projectId) onChange({ projectId, executionMode: "original", worktreeId: null });
    setOpen(false);
  }

  async function bind(directory: string) {
    const name = directory.split(/[\\/]/).filter(Boolean).pop() || "本地项目";
    const project = await createProject(name, directory);
    setProjects((current) => [project, ...current.filter((item) => item.id !== project.id)]);
    select(project.id);
    setPath("");
  }

  async function addProject(directory?: string) {
    setBusy(true);
    onBusyChange(true);
    try {
      const chosen = directory ?? (await chooseProjectDirectory()).directory;
      if (chosen) await bind(chosen);
    } catch (error) {
      onError(error instanceof Error ? error.message : "添加项目失败");
    } finally {
      setBusy(false);
      onBusyChange(false);
    }
  }

  return (
    <div className="project-picker" ref={menuRef}>
      <button className="project-trigger" disabled={disabled || busy} aria-expanded={open} aria-label="选择项目" onClick={() => setOpen(!open)}>
        <FolderOpen size={15} /><span>{busy ? "正在添加项目…" : selected?.name ?? (config.projectId ? "当前项目" : "选择项目")}</span>
        {config.executionMode === "worktree" && <small>Worktree</small>}<ChevronDown size={13} />
      </button>
      {config.projectId && localAvailable === false && <span className="project-access-warning" role="status">本地文件访问未启用，暂时无法读取项目。请在本机配置中启用本地工具后重启服务。</span>}
      {open && <div className="project-popover" aria-label="项目菜单" onKeyDown={(event) => { if (event.key === "Escape") setOpen(false); }}>
        <label className="project-search"><Search size={14} /><input aria-label="搜索项目" placeholder="搜索项目" value={query} onChange={(event) => setQuery(event.target.value)} /></label>
        {locked && <p>切换项目将开启新对话</p>}
        <button disabled={busy} onClick={() => select(null)}>普通聊天 · 不使用项目</button>
        <div className="project-options">{projects.filter((project) => `${project.name} ${project.directory}`.toLocaleLowerCase().includes(query.toLocaleLowerCase())).map((project) => <button key={project.id} disabled={busy} aria-pressed={project.id === config.projectId} title={project.directory} onClick={() => select(project.id)}><FolderOpen size={14} /><span>{project.name}</span></button>)}</div>
        <button className="add-project" disabled={busy} onClick={() => void addProject()}><Plus size={14} />添加本地项目…</button>
        <details><summary>手动填写路径</summary><form onSubmit={(event) => { event.preventDefault(); if (path.trim()) void addProject(path.trim()); }}><input aria-label="项目目录路径" placeholder="项目文件夹的完整路径" value={path} onChange={(event) => setPath(event.target.value)} /><button disabled={busy || !path.trim()}>添加</button></form></details>
        {config.projectId && <WorktreeOptions config={config} locked={locked} onChange={onChange} onError={onError} />}
      </div>}
    </div>
  );
}
