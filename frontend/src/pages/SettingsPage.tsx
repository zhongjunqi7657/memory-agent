import { RotateCcw } from "lucide-react";
import type { PublicConfig } from "../api";

type Props = {
  config: PublicConfig | null;
  loading: boolean;
  onRefresh: () => void;
};

const weightLabels: Record<string, string> = {
  vector: "语义向量",
  keyword: "关键词",
  recency: "时间衰减",
  importance: "重要度",
  type: "记忆类型",
};

export function SettingsPage({ config, loading, onRefresh }: Props) {
  return (
    <section className="workspace-page settings-page">
      <header className="page-heading">
        <div><h1>运行设置</h1><p>这里只显示非敏感配置与连接状态，密钥始终由后端环境变量维护。</p></div>
        <button className="secondary-button" onClick={onRefresh}><RotateCcw size={15} />刷新</button>
      </header>
      {loading ? <div className="page-empty">正在读取配置…</div> : config && (
        <div className="settings-groups">
          <section><h2>模型</h2><dl><div><dt>对话模型</dt><dd>{config.models.chat}</dd></div><div><dt>Embedding</dt><dd>{config.models.embedding} · {config.models.embedding_dimensions} 维</dd></div><div><dt>服务连接</dt><dd>{config.provider_configured ? "已配置" : "未配置"}</dd></div></dl></section>
          <section><h2>记忆与上下文</h2><dl><div><dt>自动召回</dt><dd>{config.memory.auto_retrieve_limit} 条</dd></div><div><dt>上下文预算</dt><dd>{config.agent.context_token_budget.toLocaleString()} tokens</dd></div><div><dt>工具循环上限</dt><dd>{config.agent.max_tool_rounds} 轮</dd></div></dl></section>
          <section><h2>运行状态</h2><dl><div><dt>环境</dt><dd>{config.environment}</dd></div><div><dt>Checkpoint</dt><dd>{config.checkpoint.ready ? "已连接 PostgreSQL" : `不可用（${config.checkpoint.error ?? "未知"}）`}</dd></div></dl></section>
          <section><h2>召回权重</h2><dl>{Object.entries(config.memory.weights).map(([name, value]) => <div key={name}><dt>{weightLabels[name] ?? name}</dt><dd>{Math.round(value * 100)}%</dd></div>)}</dl></section>
        </div>
      )}
    </section>
  );
}
