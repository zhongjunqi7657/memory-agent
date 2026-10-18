import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { TimelinePage } from "../pages/TimelinePage";
import { MarkdownContent } from "./MarkdownContent";

function render(content: string) {
  return renderToStaticMarkup(<MarkdownContent content={content} />);
}

describe("MarkdownContent", () => {
  it("renders generated document structure, including GFM tables", () => {
    const html = render([
      "### 检查结果",
      "",
      "**重点**与`app.py`。",
      "",
      "- 问题一",
      "  - 依据",
      "",
      "1. 修复配置",
      "",
      "> 保留原始内容",
      "",
      "---",
      "",
      "| 问题 | 位置 |",
      "| --- | --- |",
      "| 配置错误 | `app.py` |",
      "",
      "- [x] 已检查",
    ].join("\n"));

    expect(html).toContain("<h3>检查结果</h3>");
    expect(html).toContain("<strong>重点</strong>");
    expect(html).toContain("<code>app.py</code>");
    expect(html).toContain("<ul>\n<li>问题一\n<ul>");
    expect(html).toContain("<ol>");
    expect(html).toContain("<blockquote>");
    expect(html).toContain("<hr/>");
    expect(html).toContain('class="markdown-table-scroll" role="region" aria-label="表格" tabindex="0"><table>');
    expect(html).toContain("<th>问题</th>");
    expect(html).toContain("<td>配置错误</td>");
    expect(html).toContain('type="checkbox"');
    expect(html).not.toContain("###");
  });

  it("preserves ordinary line breaks and separates paragraphs", () => {
    const html = render("第一行\n第二行\n\n另一个段落");

    expect(html).toContain("<p>第一行<br/>\n第二行</p>");
    expect(html).toContain("<p>另一个段落</p>");
  });

  it("preserves indentation and literal markup in fenced code", () => {
    const html = render('```python\ndef check():\n    return "<script>**原文**</script>"\n```');

    expect(html).toContain('<pre><code class="language-python">def check():\n    return &quot;&lt;script&gt;**原文**&lt;/script&gt;&quot;\n</code></pre>');
    expect(html).not.toContain("<strong>");
    expect(html).not.toContain("<br");
  });

  it("rejects raw HTML and unsafe links while preserving safe links", () => {
    const html = render([
      '<script>alert("xss")</script>',
      "",
      '<img src="x" onerror="alert(1)">',
      "",
      "[危险链接](javascript:alert%281%29)",
      "",
      "[文档](https://example.com/docs)",
    ].join("\n"));

    expect(html).not.toContain("<script");
    expect(html).not.toContain("<img");
    expect(html).not.toContain("onerror");
    expect(html).not.toContain("javascript:");
    expect(html).toContain('href="https://example.com/docs" target="_blank" rel="noopener noreferrer">文档</a>');
  });
});

describe("review output", () => {
  it("uses the same Markdown rendering for generated reviews", () => {
    const html = renderToStaticMarkup(
      <TimelinePage
        items={[]}
        loading={false}
        review={"## 本周回顾\n\n- **已完成**学习计划"}
        reviewLoading={false}
        periodDays={7}
        onPeriodChange={() => undefined}
        onRefresh={() => undefined}
        onGenerateReview={() => undefined}
      />,
    );

    expect(html).toContain('<section class="review-output"><h2>阶段回顾</h2><div class="markdown-content">');
    expect(html).toContain("<h2>本周回顾</h2>");
    expect(html).toContain("<strong>已完成</strong>");
  });
});
