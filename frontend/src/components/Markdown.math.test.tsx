import { render } from "@testing-library/react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import { describe, expect, it } from "vitest";

/** 复刻 ChatPanel 里的 Markdown 渲染配置, 验证 LaTeX 经 KaTeX 渲染。 */
function renderMd(src: string) {
  return render(
    <ReactMarkdown
      remarkPlugins={[remarkGfm, remarkMath]}
      rehypePlugins={[rehypeKatex]}
    >
      {src}
    </ReactMarkdown>,
  );
}

describe("Markdown 数学公式渲染", () => {
  it("$$...$$ 块级公式渲染为 KaTeX (不再是原始文本)", () => {
    const { container } = renderMd(
      "傅里叶部分和：\n\n$$S_N f(x) = (f * D_N)(x)$$",
    );
    // KaTeX 渲染后会产出 .katex (含 .katex-html 可见排版); 不再是原始文本
    expect(container.querySelector(".katex")).toBeTruthy();
    expect(container.querySelector(".katex-html")).toBeTruthy();
  });

  it("$...$ 行内公式同样渲染", () => {
    const { container } = renderMd("当 $D_N(t) = \\frac{1}{2}$ 时");
    expect(container.querySelector(".katex")).toBeTruthy();
  });
});
