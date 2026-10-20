import { useCallback, useEffect, useRef, useState } from "react";
import type { SetStateAction } from "react";
import { fetchMemories } from "./api";
import type { Memory } from "./api";

export function useScopedMemories(projectId: string | null, includePersonal: boolean) {
  const key = `${projectId ?? "personal"}:${includePersonal}`;
  const scope = useRef({ key, projectId, includePersonal });
  scope.current = { key, projectId, includePersonal };
  const request = useRef(0);
  const [stored, setStored] = useState<{ key: string; items: Memory[] }>({ key, items: [] });
  const [status, setStatus] = useState({ key, loading: false, error: "" });
  const loadMemories = useCallback(async () => {
    const current = scope.current;
    const sequence = ++request.current;
    setStatus({ key: current.key, loading: true, error: "" });
    try {
      const items = await fetchMemories(current.projectId, current.includePersonal);
      if (current.key === scope.current.key && sequence === request.current) {
        setStored({ key: current.key, items });
        setStatus({ key: current.key, loading: false, error: "" });
      }
      return items;
    } catch (error) {
      if (current.key === scope.current.key && sequence === request.current) {
        setStatus({ key: current.key, loading: false, error: error instanceof Error ? error.message : "记忆加载失败" });
      }
      throw error;
    }
  }, []);
  const setMemories = useCallback((update: SetStateAction<Memory[]>) => {
    setStored((current) => ({ key: current.key, items: typeof update === "function" ? update(current.items) : update }));
  }, []);
  useEffect(() => {
    void loadMemories().catch(() => { /* Displayed through the scoped error state. */ });
  }, [key, loadMemories]);
  return { memories: stored.key === key ? stored.items : [], setMemories, loadMemories,
    loading: status.key !== key || status.loading, error: status.key === key ? status.error : "" };
}
