import { Brain, CalendarRange, MessageCircle, Plus, Settings, Sparkles, X } from "lucide-react";
import type { ConversationSummary } from "../api";

function formatConversationDate(value: string) {
  const date = new Date(value);
  const now = new Date();
  if (date.toDateString() === now.toDateString()) {
    return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  }
  return date.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
}

type ConversationSidebarProps = {
  conversations: ConversationSummary[];
  currentId: string | null;
  open: boolean;
  onClose: () => void;
  onNew: () => void;
  onSelect: (id: string) => void;
  view: "chat" | "memories" | "timeline" | "settings";
  onNavigate: (view: "chat" | "memories" | "timeline" | "settings") => void;
};

export function ConversationSidebar({
  conversations,
  currentId,
  open,
  onClose,
  onNew,
  onSelect,
  view,
  onNavigate,
}: ConversationSidebarProps) {
  return (
    <aside className={`sidebar ${open ? "sidebar-open" : ""}`}>
      <div className="brand-row">
        <div className="brand-mark"><Sparkles size={16} strokeWidth={1.8} /></div>
        <span>Memory Agent</span>
        <button className="icon-button mobile-close" aria-label="关闭导航" onClick={onClose}>
          <X size={18} />
        </button>
      </div>
      <button className="new-chat-button" onClick={onNew}>
        <Plus size={17} />
        新建对话
      </button>
      <nav className="workspace-nav" aria-label="工作区导航">
        <button className={view === "chat" ? "active" : ""} onClick={() => onNavigate("chat")}><MessageCircle size={16} />对话</button>
        <button className={view === "memories" ? "active" : ""} onClick={() => onNavigate("memories")}><Brain size={16} />长期记忆</button>
        <button className={view === "timeline" ? "active" : ""} onClick={() => onNavigate("timeline")}><CalendarRange size={16} />时间线与回顾</button>
        <button className={view === "settings" ? "active" : ""} onClick={() => onNavigate("settings")}><Settings size={16} />设置</button>
      </nav>
      <div className="sidebar-label">最近对话</div>
      <div className="conversation-list">
        {conversations.length === 0 ? (
          <p className="conversation-empty">发送第一条消息后，对话会保存在这里。</p>
        ) : conversations.map((conversation) => (
          <button
            className={`conversation-item ${view === "chat" && conversation.id === currentId ? "active" : ""}`}
            key={conversation.id}
            onClick={() => onSelect(conversation.id)}
          >
            <span className="conversation-title">{conversation.title || "未命名对话"}</span>
            <span className="conversation-date">{formatConversationDate(conversation.updated_at)}</span>
          </button>
        ))}
      </div>
      <div className="sidebar-bottom">
        <div className="provider-line"><span>模型</span><strong>Qwen Plus</strong></div>
        <div className="provider-line"><span>记忆</span><strong className="green-text">跨会话开启</strong></div>
      </div>
    </aside>
  );
}
