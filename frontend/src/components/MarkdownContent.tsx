import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";
import "./MarkdownContent.css";

const remarkPlugins = [remarkGfm, remarkBreaks];
const components: Components = {
  table: ({ children }) => (
    <div className="markdown-table-scroll" role="region" aria-label="表格" tabIndex={0}>
      <table>{children}</table>
    </div>
  ),
  a: ({ href, children, title }) => (
    <a href={href} title={title} target="_blank" rel="noopener noreferrer">{children}</a>
  ),
};

type Props = {
  content: string;
};

export function MarkdownContent({ content }: Props) {
  return (
    <div className="markdown-content">
      <ReactMarkdown remarkPlugins={remarkPlugins} components={components} skipHtml>
        {content}
      </ReactMarkdown>
    </div>
  );
}
