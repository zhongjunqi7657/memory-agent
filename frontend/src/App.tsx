import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import {
  BookOpen,
  Check,
  ChevronDown,
  ChevronRight,
  Clock3,
  Menu,
  PanelRight,
  Plus,
  RotateCcw,
  Send,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import { fetchMemories, sendChat, updateMemory } from "./api";
import type { Memory } from "./api";

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  memoryCount?: number;
  redacted?: boolean;
};

const initialMessages: ChatMessage[] = [
  {
    id: "welcome",
    role: "assistant",
    content:
      "你好，我是你的长期学习伙伴。你可以直接告诉我正在学什么、遇到什么困难，或者接着上次的计划聊。",
  },
];

function formatKind(kind: Memory["kind"]) {
  return kind === "semantic" ? "长期偏好" : "阶段经历";
}

function formatStatus(status: Memory["status"]) {
  return status === "pending" ? "待确认" : status === "active" ? "已生效" : status;
}

function App() {
  const [messages, setMessages] = useState<ChatMessage[]>(initialMessages);
  const [input, setInput] = useState("");
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [memories, setMemories] = useState<Memory[]>([]);
  const [isSending, setIsSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [openNotice, setOpenNotice] = useState<string | null>(null);
  const [memoryPanelOpen, setMemoryPanelOpen] = useState(() => window.innerWidth > 680);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const loadMemories = useCallback(async () => {
    try {
      setMemories(await fetchMemories());
    } catch {
      // The memory panel remains usable when the API is unavailable.
    }
  }, []);

  useEffect(() => {
    void loadMemories();
  }, [loadMemories]);

  const pendingCount = useMemo(
    () => memories.filter((memory) => memory.status === "pending").length,
    [memories],
  );

  async function handleSend() {
    const content = input.trim();
    if (!content || isSending) return;
    setError(null);
    setInput("");
    setMessages((current) => [
      ...current,
      { id: crypto.randomUUID(), role: "user", content },
    ]);
    setIsSending(true);
    try {
      const response = await sendChat(content, conversationId);
      setConversationId(response.conversation_id);
      const assistantMessage = {
        id: response.run_id,
        role: "assistant" as const,
        content: response.message,
        memoryCount: response.memory_count,
        redacted: response.redacted,
      };
      setMessages((current) => [...current, assistantMessage]);
      setOpenNotice(response.run_id);
      await loadMemories();
    } catch (sendError) {
      setError(sendError instanceof Error ? sendError.message : "请求失败，请重试");
    } finally {
      setIsSending(false);
      textareaRef.current?.focus();
    }
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void handleSend();
    }
  }

  async function handleMemoryUpdate(id: string, status: "active" | "deleted") {
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
    setMessages(initialMessages);
    setConversationId(null);
    setError(null);
    setOpenNotice(null);
  }

  return (
    <div className="app-shell">
      <aside className={`sidebar ${mobileNavOpen ? "sidebar-open" : ""}`}>
        <div className="brand-row">
          <div className="brand-mark"><Sparkles size={16} strokeWidth={1.8} /></div>
          <span>Memory Agent</span>
          <button className="icon-button mobile-close" aria-label="关闭导航" onClick={() => setMobileNavOpen(false)}>
            <X size={18} />
          </button>
        </div>
        <button className="new-chat-button" onClick={resetConversation}>
          <Plus size={17} />
          新建对话
        </button>
        <div className="sidebar-label">最近对话</div>
        <button className="conversation-item active" onClick={() => setMobileNavOpen(false)}>
          <span className="conversation-title">个人成长与学习伙伴</span>
          <span className="conversation-date">刚刚</span>
        </button>
        <div className="sidebar-bottom">
          <div className="provider-line"><span>模型</span><strong>Qwen Plus</strong></div>
          <div className="provider-line"><span>记忆</span><strong className="green-text">跨会话开启</strong></div>
        </div>
      </aside>

      {mobileNavOpen && <button className="scrim" aria-label="关闭导航" onClick={() => setMobileNavOpen(false)} />}

      <main className="main-column">
        <header className="topbar">
          <button className="icon-button mobile-menu" aria-label="打开导航" onClick={() => setMobileNavOpen(true)}>
            <Menu size={20} />
          </button>
          <div className="topbar-title">
            <span>个人成长与学习伙伴</span>
            <span className="topbar-meta"><span className="status-label">在线</span> · Qwen Plus</span>
          </div>
          <button className="icon-button panel-toggle" aria-label="切换记忆面板" onClick={() => setMemoryPanelOpen((open) => !open)}>
            <PanelRight size={19} />
          </button>
        </header>

        <section className="chat-area" aria-label="对话内容">
          <div className="message-list">
            {messages.map((message) => (
              <article key={message.id} className={`message-row ${message.role}`}>
                {message.role === "assistant" && <div className="assistant-avatar"><Sparkles size={15} /></div>}
                <div className="message-content-wrap">
                  <div className="message-author">{message.role === "assistant" ? "Memory Agent" : "你"}</div>
                  <div className="message-text">{message.content}</div>
                  {message.role === "assistant" && message.memoryCount !== undefined && (
                    <div className="run-notice">
                      <button className="notice-toggle" onClick={() => setOpenNotice((current) => current === message.id ? null : message.id)}>
                        <BookOpen size={14} />
                        <span>{message.memoryCount > 0 ? `本次参考了 ${message.memoryCount} 条长期记忆` : "本次没有匹配到长期记忆"}</span>
                        {openNotice === message.id ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                      </button>
                      {openNotice === message.id && (
                        <div className="notice-detail">
                          {message.redacted ? "已在本机识别并脱敏敏感凭据。" : "本次消息已加入记忆提取队列，普通明确事实会自动处理。"}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </article>
            ))}
            {isSending && (
              <article className="message-row assistant">
                <div className="assistant-avatar"><Sparkles size={15} /></div>
                <div className="message-content-wrap"><div className="message-author">Memory Agent</div><div className="typing-indicator"><span /><span /><span /></div></div>
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
              <button className="send-button" aria-label="发送消息" disabled={!input.trim() || isSending} onClick={() => void handleSend()}>
                <Send size={17} />
              </button>
            </div>
            <div className="composer-hint">Enter 发送 · Shift + Enter 换行</div>
          </div>
        </section>
      </main>

      {memoryPanelOpen && (
        <aside className="memory-panel">
          <div className="memory-header">
            <div><div className="section-kicker">长期上下文</div><h2>我的记忆</h2></div>
            <div className="memory-header-actions"><span className="memory-count">{memories.length}</span><button className="icon-button memory-close" aria-label="关闭记忆面板" onClick={() => setMemoryPanelOpen(false)}><X size={17} /></button></div>
          </div>
          <p className="memory-intro">这里保存的是你在对话中明确表达、且未来可能有帮助的信息。</p>
          {pendingCount > 0 && <div className="pending-summary"><Clock3 size={15} /><span>{pendingCount} 条记忆等待确认</span></div>}
          <div className="memory-list">
            {memories.length === 0 ? (
              <div className="memory-empty"><BookOpen size={20} /><strong>还没有长期记忆</strong><span>聊几次之后，这里会出现可复用的目标、偏好和阶段经历。</span></div>
            ) : memories.map((memory) => (
              <article className="memory-item" key={memory.id}>
                <div className="memory-item-top"><span className="memory-kind">{formatKind(memory.kind)}</span><span className={`memory-status status-${memory.status}`}>{formatStatus(memory.status)}</span></div>
                <p>{memory.content}</p>
                {memory.status === "pending" && <div className="memory-actions"><button onClick={() => void handleMemoryUpdate(memory.id, "active")}><Check size={14} />确认</button><button onClick={() => void handleMemoryUpdate(memory.id, "deleted")}><Trash2 size={14} />删除</button></div>}
                {memory.status === "active" && <button className="memory-delete" aria-label="删除记忆" onClick={() => void handleMemoryUpdate(memory.id, "deleted")}><Trash2 size={14} /></button>}
              </article>
            ))}
          </div>
          <button className="refresh-button" onClick={() => void loadMemories()}><RotateCcw size={14} />刷新记忆</button>
        </aside>
      )}
    </div>
  );
}

export default App;
