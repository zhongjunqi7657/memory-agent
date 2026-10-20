import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { Menu, PanelRight, Send, Sparkles, Square } from "lucide-react";
import {
  fetchConversationMessages,
  fetchConversations,
  fetchPublicConfig,
  cancelRun,
  streamChat,
  undoMemory,
  updateMemory,
} from "./api";
import type {
  ConversationSummary,
  MemoryUpdate,
  PublicConfig,
  RunEvent,
  TaskExecutionConfig,
} from "./api";
import { ConversationSidebar } from "./components/ConversationSidebar";
import { MarkdownContent } from "./components/MarkdownContent";
import { MemoryPanel } from "./components/MemoryPanel";
import { RunDetails } from "./components/RunDetails";
import { ChatRun } from "./components/ChatRun";
import { ProjectMenu } from "./components/ProjectMenu";
import { MemoriesPage } from "./pages/MemoriesPage";
import { SettingsPage } from "./pages/SettingsPage";
import { TimelinePage } from "./pages/TimelinePage";
import { fetchConversationRuns } from "./taskApi";
import { followRunEvents, mergeRunEvents, needsExtractionFollow } from "./runEvents";
import { MemoryScopeSelector } from "./components/MemoryScopeSelector";
import { useScopedMemories } from "./useScopedMemories";
import { useScopedTimeline } from "./useScopedTimeline";
import { getRunOutput } from "./modelOutput";

type AppView = "chat" | "memories" | "timeline" | "settings";

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  redacted?: boolean;
  runId?: string;
  runEvents?: RunEvent[];
};

const selectedConversationKey = "memory-agent:selected-conversation";
const defaultTaskConfig: TaskExecutionConfig = { projectId: null, executionMode: "original", worktreeId: null };
const welcomeMessages: ChatMessage[] = [
  {
    id: "welcome",
    role: "assistant",
    content: "你好，我是你的长期学习伙伴。你可以直接告诉我正在学什么、遇到什么困难，或者接着上次的计划聊。",
  },
];

const liveEventLabels: Record<string, string> = {
  "run.started": "已创建本次运行",
  "memory.embedding_ready": "正在理解相关长期记忆",
  "memory.embedding_fallback": "使用降级检索长期记忆",
  "memory.retrieved": "已完成长期记忆检索",
  "session.loaded": "已载入会话上下文",
  "session.summary_updated": "已更新会话摘要",
  "tool.started": "正在使用记忆工具",
  "tool.completed": "记忆工具已完成",
  "tool.execution.started": "正在使用工具",
  "tool.execution.completed": "工具已完成",
  "tool.execution.failed": "工具执行失败",
  "tool.execution.denied": "工具权限不足",
  "tool.execution.cancelled": "工具调用已取消",
  "model.completed": "正在整理回答",
  "model.output.started": "正在生成回答",
  "model.output.delta": "正在生成回答",
  "memory.extraction_queued": "已排队更新长期记忆",
  "memory.command_applied": "已处理记忆指令",
};

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function App() {
  const [view, setView] = useState<AppView>("chat");
  const [taskConfig, setTaskConfig] = useState<TaskExecutionConfig>(defaultTaskConfig);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [liveRunEvents, setLiveRunEvents] = useState<RunEvent[]>([]);
  const [recoveredRunId, setRecoveredRunId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>(welcomeMessages);
  const [input, setInput] = useState("");
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [memoryProjectId, setMemoryProjectId] = useState<string | null>(null);
  const { memories, setMemories, loadMemories, loading: memoryLoading, error: memoryError } = useScopedMemories(view === "chat" ? taskConfig.projectId : memoryProjectId, view === "chat");
  const { timeline, timelineLoading, review, reviewLoading, error: timelineError, loadTimeline, loadReview } = useScopedTimeline(memoryProjectId);
  const [publicConfig, setPublicConfig] = useState<PublicConfig | null>(null);
  const [reviewPeriod, setReviewPeriod] = useState(7);
  const [isSending, setIsSending] = useState(false);
  const [isSelectingProject, setIsSelectingProject] = useState(false);
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);
  const [pageLoading, setPageLoading] = useState(false);
  const [liveEvent, setLiveEvent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [memoryPanelOpen, setMemoryPanelOpen] = useState(() => window.innerWidth > 680);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const activeConversationRef = useRef<string | null>(null);
  const conversationRequestRef = useRef(0);
  const activeChatRef = useRef<AbortController | null>(null);
  const activeRunIdRef = useRef<string | null>(null);
  const pendingStopRef = useRef(false);
  const runFollowersRef = useRef(new Map<string, AbortController>());

  const loadConversations = useCallback(async () => {
    const loaded = await fetchConversations();
    setConversations(loaded);
    return loaded;
  }, []);

  const stopRunFollowers = useCallback(() => {
    for (const controller of runFollowersRef.current.values()) controller.abort();
    runFollowersRef.current.clear();
  }, []);

  const stopActiveChat = useCallback(() => {
    const controller = activeChatRef.current;
    const runId = activeRunIdRef.current;
    if (runId && (controller || recoveredRunId)) {
      void cancelRun(runId).catch((cancelError) => {
        if (!isAbortError(cancelError)) setError(cancelError instanceof Error ? cancelError.message : "任务停止失败");
      });
    } else {
      if (controller) pendingStopRef.current = true;
    }
  }, [recoveredRunId]);

  const startRunFollower = useCallback((
    runId: string,
    initialEvents: RunEvent[],
    ownerConversationId: string,
  ) => {
    if (!needsExtractionFollow(initialEvents) || runFollowersRef.current.has(runId)) return;
    const controller = new AbortController();
    runFollowersRef.current.set(runId, controller);
    void followRunEvents({
      runId,
      initialEvents,
      signal: controller.signal,
      onUpdate: (events) => {
        if (activeConversationRef.current !== ownerConversationId) {
          controller.abort();
          return;
        }
        setMessages((current) => current.map((message) => (
          message.runId === runId ? { ...message, runEvents: events } : message
        )));
      },
    }).then((events) => {
      if (
        activeConversationRef.current === ownerConversationId
        && events.some((event) => event.event_type === "memory.extraction_completed")
      ) {
        void loadMemories().catch(() => setError("记忆列表刷新失败，请稍后重试"));
      }
    }).catch((followError) => {
      if (!isAbortError(followError) && activeConversationRef.current === ownerConversationId) {
        setError("异步记忆事件恢复失败，请稍后重新打开会话");
      }
    }).finally(() => {
      if (runFollowersRef.current.get(runId) === controller) {
        runFollowersRef.current.delete(runId);
      }
    });
  }, [loadMemories]);

  const loadConfig = useCallback(async () => {
    setPageLoading(true);
    try {
      const loaded = await fetchPublicConfig();
      setPublicConfig(loaded);
      setError(null);
    } finally {
      setPageLoading(false);
    }
  }, []);

  const selectConversation = useCallback(async (id: string) => {
    const requestId = conversationRequestRef.current + 1;
    conversationRequestRef.current = requestId;
    activeChatRef.current?.abort();
    activeChatRef.current = null;
    activeRunIdRef.current = null;
    setActiveRunId(null);
    setRecoveredRunId(null);
    setIsSending(false);
    stopRunFollowers();
    activeConversationRef.current = id;
    setError(null);
    setIsLoadingHistory(true);
    setConversationId(id);
    setView("chat");
    setMobileNavOpen(false);
    localStorage.setItem(selectedConversationKey, id);
    try {
      const [stored, runs, summaries] = await Promise.all([fetchConversationMessages(id), fetchConversationRuns(id), fetchConversations()]);
      if (conversationRequestRef.current !== requestId) return;
      setConversations(summaries);
      const latest = runs[0];
      setTaskConfig({ projectId: summaries.find((item) => item.id === id)?.project_id ?? latest?.project_id ?? null, executionMode: latest?.execution_mode ?? "original", worktreeId: latest?.worktree_id ?? null });
      const loadedMessages = stored
        .filter((message) => message.role === "user" || message.role === "assistant")
        .map((message) => ({
          id: message.id,
          role: message.role as "user" | "assistant",
          content: message.content,
          redacted: message.is_redacted,
          runId: message.run_id ?? undefined,
          runEvents: message.run_id ? mergeRunEvents(message.run_id, message.run_events) : undefined,
        }));
      const orphanRuns: ChatMessage[] = runs.filter((run) => !loadedMessages.some((message) => message.runId === run.run_id)).reverse().map((run) => ({ id: run.run_id, role: "assistant", content: "", runId: run.run_id }));
      setMessages([...loadedMessages, ...orphanRuns]);
      const pending = runs.find((run) => ["queued", "running", "waiting_approval"].includes(run.status));
      if (pending) {
        activeRunIdRef.current = pending.run_id;
        setActiveRunId(pending.run_id);
        setRecoveredRunId(pending.run_id);
        setIsSending(true);
      }
      for (const message of loadedMessages) {
        if (message.runId && message.runEvents) {
          startRunFollower(message.runId, message.runEvents, id);
        }
      }
    } catch (loadError) {
      if (conversationRequestRef.current === requestId && !isAbortError(loadError)) {
        setError(loadError instanceof Error ? loadError.message : "对话加载失败");
      }
    } finally {
      if (conversationRequestRef.current === requestId) setIsLoadingHistory(false);
    }
  }, [startRunFollower, stopRunFollowers]);

  const restoreCompletedRun = useCallback(() => {
    if (!activeChatRef.current && activeConversationRef.current) void selectConversation(activeConversationRef.current);
  }, [selectConversation]);

  useEffect(() => {
    let cancelled = false;
    async function loadInitialData() {
      try {
        const [loadedConversations] = await Promise.all([loadConversations(), loadMemories()]);
        if (cancelled || loadedConversations.length === 0) return;
        const selectedId = localStorage.getItem(selectedConversationKey);
        const initial = loadedConversations.find((item) => item.id === selectedId) ?? loadedConversations[0];
        await selectConversation(initial.id);
      } catch {
        if (!cancelled) setError("暂时无法连接服务，请检查 API 是否已启动");
      }
    }
    void loadInitialData();
    return () => {
      cancelled = true;
      activeChatRef.current?.abort();
      for (const controller of runFollowersRef.current.values()) controller.abort();
    };
  }, [loadConversations, loadMemories, selectConversation]);

  useEffect(() => {
    setError(null);
    if (view === "timeline") {
      void loadTimeline();
    }
    if (view === "settings") {
      void loadConfig().catch((loadError) => setError(loadError instanceof Error ? loadError.message : "配置加载失败"));
    }
  }, [loadConfig, loadTimeline, view, memoryProjectId]);

  const currentTitle = useMemo(() => {
    if (view === "memories") return "长期记忆";
    if (view === "timeline") return "时间线与回顾";
    if (view === "settings") return "设置";
    return conversations.find((item) => item.id === conversationId)?.title || "新的对话";
  }, [conversationId, conversations, view]);

  async function handleSend() {
    const content = input.trim();
    if (!content || isSending || isLoadingHistory || isSelectingProject) return;
    setError(null);
    setInput("");
    setMessages((current) => [...current, { id: crypto.randomUUID(), role: "user", content }]);
    setIsSending(true);
    pendingStopRef.current = false;
    activeRunIdRef.current = null;
    setActiveRunId(null);
    setLiveRunEvents([]);
    let targetConversationId = activeConversationRef.current;
    const controller = new AbortController();
    activeChatRef.current = controller;
    let runEvents: RunEvent[] = [];
    try {
      const response = await streamChat(content, targetConversationId, (event) => {
        if (
          activeChatRef.current !== controller
          || activeConversationRef.current !== targetConversationId
        ) return;
        if (event.run_id) {
          activeRunIdRef.current = event.run_id;
          setActiveRunId(event.run_id);
          if (pendingStopRef.current) {
            pendingStopRef.current = false;
            void cancelRun(event.run_id).catch((cancelError) => setError(cancelError instanceof Error ? cancelError.message : "任务停止失败"));
          }
          runEvents = mergeRunEvents(event.run_id, runEvents, [event]);
          setLiveRunEvents(runEvents);
        }
        if (event.event_type === "run.started" && typeof event.payload.conversation_id === "string") {
          targetConversationId = event.payload.conversation_id;
          activeConversationRef.current = targetConversationId;
          setConversationId(targetConversationId);
          localStorage.setItem(selectedConversationKey, targetConversationId);
        }
        setLiveEvent(liveEventLabels[event.event_type] ?? null);
      }, controller.signal, taskConfig);
      if (
        activeChatRef.current !== controller
        || activeConversationRef.current !== targetConversationId
      ) return;
      runEvents = mergeRunEvents(response.run_id, runEvents);
      activeRunIdRef.current = response.run_id;
      setActiveRunId(response.run_id);
      activeConversationRef.current = response.conversation_id;
      setConversationId(response.conversation_id);
      localStorage.setItem(selectedConversationKey, response.conversation_id);
      setMessages((current) => [...current, {
        id: response.run_id,
        role: "assistant",
        content: response.message,
        redacted: response.redacted,
        runId: response.run_id,
        runEvents: [...runEvents],
      }]);
      startRunFollower(response.run_id, runEvents, response.conversation_id);
      await Promise.all([loadMemories(), loadConversations()]);
    } catch (sendError) {
      if (activeChatRef.current === controller && activeRunIdRef.current) {
        const failedId = activeRunIdRef.current;
        setMessages((current) => [...current, { id: failedId, role: "assistant", content: "", runId: failedId, runEvents }]);
      }
      if (!isAbortError(sendError)) {
        setError(sendError instanceof Error ? sendError.message : "请求失败，请重试");
      }
    } finally {
      if (activeChatRef.current === controller) {
        activeChatRef.current = null;
        setIsSending(false);
        setLiveEvent(null);
        textareaRef.current?.focus();
      }
    }
  }

  async function handleMemoryUpdate(id: string, update: MemoryUpdate) {
    try {
      const updated = await updateMemory(id, update, memories.find((item) => item.id === id)?.project_id ?? null);
      setMemories((current) => current.map((memory) => (memory.id === id ? updated : memory)));
      await loadMemories();
      if (view === "timeline") await loadTimeline();
    } catch (updateError) {
      setError(updateError instanceof Error ? updateError.message : "记忆更新失败");
    }
  }

  async function handleMemoryUndo(id: string) {
    try {
      const updated = await undoMemory(id, memories.find((item) => item.id === id)?.project_id ?? null);
      setMemories((current) => current.map((memory) => (memory.id === id ? updated : memory)));
      await loadMemories();
    } catch (undoError) {
      setError(undoError instanceof Error ? undoError.message : "撤销失败");
    }
  }

  async function handleGenerateReview() {
    setError(null);
    await loadReview(reviewPeriod);
  }

  function resetConversation() {
    conversationRequestRef.current += 1;
    stopActiveChat();
    stopRunFollowers();
    activeConversationRef.current = null;
    activeRunIdRef.current = null;
    setActiveRunId(null);
    setRecoveredRunId(null);
    setLiveRunEvents([]);
    setTaskConfig(defaultTaskConfig);
    setInput("");
    setView("chat");
    setMessages(welcomeMessages);
    setConversationId(null);
    setError(null);
    setMobileNavOpen(false);
    localStorage.removeItem(selectedConversationKey);
    textareaRef.current?.focus();
  }

  function navigate(nextView: AppView) {
    setView(nextView);
    setMobileNavOpen(false);
  }

  function changeProject(config: TaskExecutionConfig) {
    if (isSending) return;
    if (conversationId) resetConversation();
    setTaskConfig(config);
  }

  return (
    <div className={`app-shell ${view === "chat" && memoryPanelOpen ? "memory-open" : ""}`}>
      <ConversationSidebar conversations={conversations} currentId={conversationId} open={mobileNavOpen} view={view} busy={isSending || isSelectingProject} onClose={() => setMobileNavOpen(false)} onNew={resetConversation} onSelect={(id) => void selectConversation(id)} onNavigate={navigate} />
      {mobileNavOpen && <button className="scrim" aria-label="关闭导航" onClick={() => setMobileNavOpen(false)} />}

      <main className="main-column">
        <header className="topbar">
          <button className="icon-button mobile-menu" aria-label="打开导航" onClick={() => setMobileNavOpen(true)}><Menu size={20} /></button>
          <div className="topbar-title"><span>{currentTitle}</span>{view === "chat" && <span className="topbar-meta">在线 · Qwen Plus</span>}</div>
          {view === "chat" && <button className="icon-button panel-toggle" aria-label="切换记忆面板" onClick={() => setMemoryPanelOpen((open) => !open)}><PanelRight size={19} /></button>}
        </header>
        {(error || (view === "timeline" && timelineError)) && view !== "chat" && <div className="workspace-error">{error || timelineError}</div>}

        {view === "chat" && (
          <section className="chat-area" aria-label="对话内容">
            <div className="message-list">
              {isLoadingHistory ? <div className="history-loading">正在加载对话…</div> : messages.map((message) => (
                <article key={message.id} className={`message-row ${message.role}`}>
                  {message.role === "assistant" && <div className="assistant-avatar"><Sparkles size={15} /></div>}
                  <div className="message-content-wrap">
                    <div className="message-author">{message.role === "assistant" ? "Memory Agent" : "你"}</div>
                    {message.role === "assistant" ? <MarkdownContent content={message.content} /> : <div className="message-text">{message.content}</div>}
                    {message.role === "assistant" && message.runEvents && <RunDetails events={message.runEvents} />}
                    {message.role === "assistant" && message.runId && <ChatRun runId={message.runId} events={message.runEvents} showOutput={!message.content} onRecovered={message.runId === recoveredRunId ? restoreCompletedRun : undefined} />}
                    {message.redacted && <div className="redaction-note">已在本机脱敏敏感信息</div>}
                  </div>
                </article>
              ))}
              {isSending && !recoveredRunId && <article className="message-row assistant"><div className="assistant-avatar"><Sparkles size={15} /></div><div className="message-content-wrap"><div className="message-author">Memory Agent</div>{!getRunOutput(liveRunEvents) && <p className="live-message">{liveEvent ?? "正在思考"}</p>}{activeRunId && <ChatRun runId={activeRunId} events={liveRunEvents} live showOutput onStop={stopActiveChat} />}</div></article>}
            </div>
            <div className="composer-wrap">
              {error && <div className="error-banner">{error}</div>}
              <div className="composer">
                <div className="composer-input-row">
                <textarea ref={textareaRef} value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={(event: KeyboardEvent<HTMLTextAreaElement>) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void handleSend(); } }} placeholder="告诉我你最近在思考什么..." rows={1} aria-label="输入消息" />
                {isSending ? <button className="send-button stop-button" aria-label="停止任务" onClick={stopActiveChat}><Square size={15} /></button> : <button className="send-button" aria-label="发送消息" disabled={!input.trim() || isLoadingHistory || isSelectingProject} onClick={() => void handleSend()}><Send size={17} /></button>}
                </div>
                <div className="composer-toolbar"><ProjectMenu config={taskConfig} locked={conversationId !== null} disabled={isSending || isLoadingHistory} onChange={changeProject} onError={setError} onBusyChange={setIsSelectingProject} /></div>
              </div>
              <div className="composer-hint">Enter 发送 · Shift + Enter 换行</div>
            </div>
          </section>
        )}

        {view === "memories" && <MemoriesPage scopeControl={<MemoryScopeSelector value={memoryProjectId} onChange={setMemoryProjectId} />} memories={memories} loading={memoryLoading} error={memoryError} onRefresh={() => { void loadMemories().catch(() => undefined); }} onUpdate={handleMemoryUpdate} onUndo={handleMemoryUndo} />}
        {view === "timeline" && <TimelinePage scopeControl={<MemoryScopeSelector value={memoryProjectId} onChange={setMemoryProjectId} />} items={timeline} loading={timelineLoading} review={review} reviewLoading={reviewLoading} periodDays={reviewPeriod} onPeriodChange={setReviewPeriod} onRefresh={() => void loadTimeline()} onGenerateReview={() => void handleGenerateReview()} />}
        {view === "settings" && <SettingsPage config={publicConfig} loading={pageLoading} onRefresh={() => void loadConfig()} />}
      </main>

      {view === "chat" && memoryPanelOpen && <MemoryPanel error={memoryError} memories={memories.filter((memory) => memory.status === "active" || memory.status === "pending")} onClose={() => setMemoryPanelOpen(false)} onRefresh={() => { void loadMemories().catch(() => undefined); }} onUpdate={(id, status) => void handleMemoryUpdate(id, { status })} />}
    </div>
  );
}

export default App;
