import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { Menu, PanelRight, Send, Sparkles } from "lucide-react";
import {
  fetchConversationMessages,
  fetchConversations,
  fetchMemories,
  streamChat,
  updateMemory,
} from "./api";
import type { ConversationSummary, Memory, RunEvent } from "./api";
import { ConversationSidebar } from "./components/ConversationSidebar";
import { MemoryPanel } from "./components/MemoryPanel";
import { RunDetails } from "./components/RunDetails";

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  memoryCount?: number;
  redacted?: boolean;
  memoryCommand?: string | null;
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
  "memory.embedding_fallback": "使用关键词检索长期记忆",
  "memory.retrieved": "已完成长期记忆检索",
  "model.completed": "正在整理回答",
  "memory.extraction_queued": "已排队更新长期记忆",
  "memory.command_applied": "已处理记忆指令",
};

function App() {
  const [messages, setMessages] = useState<ChatMessage[]>(welcomeMessages);
  const [input, setInput] = useState("");
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [memories, setMemories] = useState<Memory[]>([]);
  const [isSending, setIsSending] = useState(false);
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);
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

  const selectConversation = useCallback(async (id: string) => {
    setError(null);
    setIsLoadingHistory(true);
    setConversationId(id);
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
        const [loadedConversations] = await Promise.all([
          loadConversations(),
          loadMemories(),
        ]);
        if (cancelled || loadedConversations.length === 0) return;
        const selectedId = localStorage.getItem(selectedConversationKey);
        const initial = loadedConversations.find((item) => item.id === selectedId)
          ?? loadedConversations[0];
        await selectConversation(initial.id);
      } catch {
        if (!cancelled) setError("暂时无法连接服务，请检查 API 是否已启动");
      }
    }
    void loadInitialData();
    return () => { cancelled = true; };
  }, [loadConversations, loadMemories, selectConversation]);

  const currentTitle = useMemo(
    () => conversations.find((item) => item.id === conversationId)?.title || "新的对话",
    [conversationId, conversations],
  );

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
      setMessages((current) => [
        ...current,
        {
          id: response.run_id,
          role: "assistant",
          content: response.message,
          memoryCount: response.memory_count,
          redacted: response.redacted,
          memoryCommand: response.memory_command,
          runEvents: [...runEvents],
        },
      ]);
      await Promise.all([loadMemories(), loadConversations()]);
    } catch (sendError) {
      setError(sendError instanceof Error ? sendError.message : "请求失败，请重试");
    } finally {
      setIsSending(false);
      setLiveEvent(null);
      textareaRef.current?.focus();
    }
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void handleSend();
    }
  }

  async function handleMemoryUpdate(id: string, status: "active" | "rejected" | "deleted") {
    try {
      const updated = await updateMemory(id, status);
      setMemories((current) =>
        status === "deleted"
          ? current.filter((memory) => memory.id !== id)
          : current.map((memory) => (memory.id === id ? updated : memory)),
      );
    } catch (updateError) {
      setError(updateError instanceof Error ? updateError.message : "记忆更新失败");
    }
  }

  function resetConversation() {
    setMessages(welcomeMessages);
    setConversationId(null);
    setError(null);
    setMobileNavOpen(false);
    localStorage.removeItem(selectedConversationKey);
    textareaRef.current?.focus();
  }

  return (
    <div className={`app-shell ${memoryPanelOpen ? "memory-open" : ""}`}>
      <ConversationSidebar
        conversations={conversations}
        currentId={conversationId}
        open={mobileNavOpen}
        onClose={() => setMobileNavOpen(false)}
        onNew={resetConversation}
        onSelect={(id) => void selectConversation(id)}
      />

      {mobileNavOpen && <button className="scrim" aria-label="关闭导航" onClick={() => setMobileNavOpen(false)} />}

      <main className="main-column">
        <header className="topbar">
          <button className="icon-button mobile-menu" aria-label="打开导航" onClick={() => setMobileNavOpen(true)}><Menu size={20} /></button>
          <div className="topbar-title">
            <span>{currentTitle}</span>
            <span className="topbar-meta">在线 · Qwen Plus</span>
          </div>
          <button className="icon-button panel-toggle" aria-label="切换记忆面板" onClick={() => setMemoryPanelOpen((open) => !open)}><PanelRight size={19} /></button>
        </header>

        <section className="chat-area" aria-label="对话内容">
          <div className="message-list">
            {isLoadingHistory ? (
              <div className="history-loading">正在加载对话…</div>
            ) : messages.map((message) => (
              <article key={message.id} className={`message-row ${message.role}`}>
                {message.role === "assistant" && <div className="assistant-avatar"><Sparkles size={15} /></div>}
                <div className="message-content-wrap">
                  <div className="message-author">{message.role === "assistant" ? "Memory Agent" : "你"}</div>
                  <div className="message-text">{message.content}</div>
                  {message.role === "assistant" && message.runEvents && (
                    <RunDetails events={message.runEvents} />
                  )}
                  {message.redacted && <div className="redaction-note">已在本机脱敏敏感信息</div>}
                </div>
              </article>
            ))}
            {isSending && (
              <article className="message-row assistant">
                <div className="assistant-avatar"><Sparkles size={15} /></div>
                <div className="message-content-wrap"><div className="message-author">Memory Agent</div><div className="typing-indicator"><span /><span /><span /><em>{liveEvent ?? "正在思考"}</em></div></div>
              </article>
            )}
          </div>

          <div className="composer-wrap">
            {error && <div className="error-banner">{error}</div>}
            <div className="composer">
              <textarea
                ref={textareaRef}
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="告诉我你最近在思考什么..."
                rows={1}
                aria-label="输入消息"
              />
              <button className="send-button" aria-label="发送消息" disabled={!input.trim() || isSending} onClick={() => void handleSend()}><Send size={17} /></button>
            </div>
            <div className="composer-hint">Enter 发送 · Shift + Enter 换行</div>
          </div>
        </section>
      </main>

      {memoryPanelOpen && (
        <MemoryPanel
          memories={memories}
          onClose={() => setMemoryPanelOpen(false)}
          onRefresh={() => void loadMemories()}
          onUpdate={(id, status) => void handleMemoryUpdate(id, status)}
        />
      )}
    </div>
  );
}

export default App;
