import { useEffect, useState } from "react";
import { fetchProjects } from "../taskApi";
import type { Project } from "../taskApi";

export function MemoryScopeSelector({ value, onChange }: {
  value: string | null;
  onChange: (value: string | null) => void;
}) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [error, setError] = useState(false);
  useEffect(() => {
    let active = true;
    void fetchProjects().then((items) => { if (active) setProjects(items); })
      .catch(() => { if (active) setError(true); });
    return () => { active = false; };
  }, []);
  return <div className="filter-bar">
    <label className="select-field">记忆范围
      <select value={value ?? ""} onChange={(event) => onChange(event.target.value || null)}>
        <option value="">个人记忆</option>
        {projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
      </select>
    </label>
    <span>{error ? "项目列表加载失败，请重新打开页面。" : "个人记忆适用于所有项目；项目记忆仅在该项目内使用。"}</span>
  </div>;
}
