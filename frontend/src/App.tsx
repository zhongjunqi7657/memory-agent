import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { Menu, PanelRight, Send, Sparkles } from "lucide-react";
import {
  fetchConversationMessages,
  fetchConversations,
  fetchMemories,
  fetchPublicConfig,
  fetchTimeline,
  generateReview,
  streamChat,
  undoMemory,
  updateMemory,
} from "./api";
import type {
  ConversationSummary,
  Memory,
  MemoryUpdate,
  PublicConfig,
  RunEvent,
  TimelineItem,
} from "./api";
import { ConversationSidebar } from "./components/ConversationSidebar";
import { MemoryPanel } from "./components/MemoryPanel";
import { RunDetails } from "./components/RunDetails";
import { MemoriesPage } from "./pages/MemoriesPage";
import { SettingsPage } from "./pages/SettingsPage";
import { TimelinePage } from "./pages/TimelinePage";

type AppView = "chat" | "memories" | "timeline" | "settings";

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  redacted?: boolean;
  runEvents?: RunEvent[];
};

const selectedConversationKey = "memory-agent:selected-conversation";
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
  "model.completed": "正在整理回答",
  "memory.extraction_queued": "已排队更新长期记忆",
  "memory.command_applied": "已处理记忆指令",
};

function App() {
  const [view, setView] = useState<AppView>("chat");
  const [messages, setMessages] = useState<ChatMessage[]>(welcomeMessages);
  const [input, setInput] = useState("");
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [memories, setMemories] = useState<Memory[]>([]);
  const [timeline, setTimeline] = useState<TimelineItem[]>([]);
  const [publicConfig, setPublicConfig] = useState<PublicConfig | null>(null);
  const [review, setReview] = useState<string | null>(null);
  const [reviewPeriod, setReviewPeriod] = useState(7);
  const [reviewLoading, setReviewLoading] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);
  const [pageLoading, setPageLoading] = useState(false);
  const [liveEvent, setLiveEvent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [memoryPanelOpen, setMemoryPanelOpen] = useState(() => window.innerWidth > 680);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const loadMemories = useCallback(async () => {
    const loaded = await fetchMemories();
    setMemories(loaded);
    return loaded;
  }, []);

  const loadConversations = useCallback(async () => {
    const loaded = await fetchConversations();
    setConversations(loaded);
    return loaded;
  }, []);

  const loadTimeline = useCallback(async () => {
    setPageLoading(true);
    try {
      setTimeline(await fetchTimeline());
    } finally {
      setPageLoading(false);
    }
  }, []);

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
    setError(null);
    setIsLoadingHistory(true);
    setConversationId(id);
    setView("chat");
    setMobileNavOpen(false);
    localStorage.setItem(selectedConversationKey, id);
    try {
      const stored = await fetchConversationMessages(id);
      setMessages(
        stored
          .filter((message) => message.role === "user" || message.role === "assistant")
          .map((message) => ({
            id: message.id,
            role: message.role as "user" | "assistant",
            content: message.content,
            redacted: message.is_redacted,
          })),
      );
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "对话加载失败");
    } finally {
      setIsLoadingHistory(false);
    }
  }, []);

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
    return () => { cancelled = true; };
  }, [loadConversations, loadMemories, selectConversation]);

  useEffect(() => {
    setError(null);
    if (view === "timeline") {
      void loadTimeline().catch((loadError) => setError(loadError instanceof Error ? loadError.message : "时间线加载失败"));
    }
    if (view === "settings") {
      void loadConfig().catch((loadError) => setError(loadError instanceof Error ? loadError.message : "配置加载失败"));
    }
  }, [loadConfig, loadTimeline, view]);

  const currentTitle = useMemo(() => {
    if (view === "memories") return "长期记忆";
    if (view === "timeline") return "时间线与回顾";
    if (view === "settings") return "设置";
    return conversations.find((item) => item.id === conversationId)?.title || "新的对话";
  }, [conversationId, conversations, view]);

  async function handleSend() {
    const content = input.trim();
    if (!content || isSending) return;
    setError(null);
    setInput("");
    setMessages((current) => [...current, { id: crypto.randomUUID(), role: "user", content }]);
    setIsSending(true);
    const runEvents: RunEvent[] = [];
    try {
      const response = await streamChat(content, conversationId, (event) => {
        runEvents.push(event);
        setLiveEvent(liveEventLabels[event.event_type] ?? null);
      });
      setConversationId(response.conversation_id);
      localStorage.setItem(selectedConversationKey, response.conversation_id);
      setMessages((current) => [...current, {
        id: response.run_id,
        role: "assistant",
        content: response.message,
        redacted: response.redacted,
        runEvents: [...runEvents],
      }]);
      await Promise.all([loadMemories(), loadConversations()]);
    } catch (sendError) {
      setError(sendError instanceof Error ? sendError.message : "请求失败，请重试");
    } finally {
      setIsSending(false);
      setLiveEvent(null);
      textareaRef.current?.focus();
    }
  }

  async function handleMemoryUpdate(id: string, update: MemoryUpdate) {
    try {
      const updated = await updateMemory(id, update);
      setMemories((current) => current.map((memory) => (memory.id === id ? updated : memory)));
      await loadMemories();
      if (view === "timeline") await loadTimeline();
    } catch (updateError) {
      setError(updateError instanceof Error ? updateError.message : "记忆更新失败");
    }
  }

  async function handleMemoryUndo(id: string) {
    try {
      const updated = await undoMemory(id);
      setMemories((current) => current.map((memory) => (memory.id === id ? updated : memory)));
      await loadMemories();
    } catch (undoError) {
      setError(undoError instanceof Error ? undoError.message : "撤销失败");
    }
  }

  async function handleGenerateReview() {
    setReviewLoading(true);
    setError(null);
    try {
      setReview((await generateReview(reviewPeriod)).content);
    } catch (reviewError) {
      setError(reviewError instanceof Error ? reviewError.message : "回顾生成失败");
    } finally {
      setReviewLoading(false);
    }
  }

  function resetConversation() {
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

  return (
    <div className={`app-shell ${view === "chat" && memoryPanelOpen ? "memory-open" : ""}`}>
      <ConversationSidebar conversations={conversations} currentId={conversationId} open={mobileNavOpen} view={view} onClose={() => setMobileNavOpen(false)} onNew={resetConversation} onSelect={(id) => void selectConversation(id)} onNavigate={navigate} />
      {mobileNavOpen && <button className="scrim" aria-label="关闭导航" onClick={() => setMobileNavOpen(false)} />}

      <main className="main-column">
        <header className="topbar">
          <button className="icon-button mobile-menu" aria-label="打开导航" onClick={() => setMobileNavOpen(true)}><Menu size={20} /></button>
          <div className="topbar-title"><span>{currentTitle}</span>{view === "chat" && <span className="topbar-meta">在线 · Qwen Plus</span>}</div>
          {view === "chat" && <button className="icon-button panel-toggle" aria-label="切换记忆面板" onClick={() => setMemoryPanelOpen((open) => !open)}><PanelRight size={19} /></button>}
        </header>
        {error && view !== "chat" && <div className="workspace-error">{error}</div>}

        {view === "chat" && (
          <section className="chat-area" aria-label="对话内容">
            <div className="message-list">
              {isLoadingHistory ? <div className="history-loading">正在加载对话…</div> : messages.map((message) => (
                <article key={message.id} className={`message-row ${message.role}`}>
                  {message.role === "assistant" && <div className="assistant-avatar"><Sparkles size={15} /></div>}
                  <div className="message-content-wrap">
                    <div className="message-author">{message.role === "assistant" ? "Memory Agent" : "你"}</div>
                    <div className="message-text">{message.content}</div>
                    {message.role === "assistant" && message.runEvents && <RunDetails events={message.runEvents} />}
                    {message.redacted && <div className="redaction-note">已在本机脱敏敏感信息</div>}
                  </div>
                </article>
              ))}
              {isSending && <article className="message-row assistant"><div className="assistant-avatar"><Sparkles size={15} /></div><div className="message-content-wrap"><div className="message-author">Memory Agent</div><div className="typing-indicator"><span /><span /><span /><em>{liveEvent ?? "正在思考"}</em></div></div></article>}
            </div>
            <div className="composer-wrap">
              {error && <div className="error-banner">{error}</div>}
              <div className="composer">
                <textarea ref={textareaRef} value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={(event: KeyboardEvent<HTMLTextAreaElement>) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void handleSend(); } }} placeholder="告诉我你最近在思考什么..." rows={1} aria-label="输入消息" />
                <button className="send-button" aria-label="发送消息" disabled={!input.trim() || isSending} onClick={() => void handleSend()}><Send size={17} /></button>
              </div>
              <div className="composer-hint">Enter 发送 · Shift + Enter 换行</div>
            </div>
          </section>
        )}

        {view === "memories" && <MemoriesPage memories={memories} loading={pageLoading} onRefresh={() => void loadMemories()} onUpdate={handleMemoryUpdate} onUndo={handleMemoryUndo} />}
        {view === "timeline" && <TimelinePage items={timeline} loading={pageLoading} review={review} reviewLoading={reviewLoading} periodDays={reviewPeriod} onPeriodChange={setReviewPeriod} onRefresh={() => void loadTimeline()} onGenerateReview={() => void handleGenerateReview()} />}
        {view === "settings" && <SettingsPage config={publicConfig} loading={pageLoading} onRefresh={() => void loadConfig()} />}
      </main>

      {view === "chat" && memoryPanelOpen && <MemoryPanel memories={memories.filter((memory) => memory.status === "active" || memory.status === "pending")} onClose={() => setMemoryPanelOpen(false)} onRefresh={() => void loadMemories()} onUpdate={(id, status) => void handleMemoryUpdate(id, { status })} />}
    </div>
  );
}

export default App;
