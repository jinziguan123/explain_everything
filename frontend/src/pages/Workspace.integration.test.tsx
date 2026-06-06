import type { ReactNode } from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// 用真实 ChatPanel, 只 stub 重依赖 (cytoscape 图谱 / 侧栏)
vi.mock("../components/GraphPanel", () => ({ default: () => <div /> }));
vi.mock("../components/SessionSidebar", () => ({ default: () => <div /> }));
vi.mock("../api/chatStream", () => ({ streamChat: vi.fn() }));

const REAL_TRANSCRIPT = [
  { role: "user", content: "日本经济为什么停滞三十年" },
  {
    role: "assistant",
    content: [
      { type: "thinking", thinking: "先看现象" },
      { type: "text", text: "核心是资产负债表衰退" },
      { type: "tool_use", id: "t1", name: "read_node", input: {} },
    ],
  },
  { role: "user", content: [{ type: "tool_result", tool_use_id: "t1", content: "x" }] },
];

vi.mock("../api/client", () => ({
  listSessions: vi.fn(async () => []),
  getTranscript: vi.fn(async () => REAL_TRANSCRIPT),
  createSession: vi.fn(),
  deleteSession: vi.fn(),
  autotitleSession: vi.fn(),
}));

function wrap(ui: ReactNode) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>;
}

beforeEach(() => {
  localStorage.clear();
});

describe("Workspace 刷新恢复 → 真实渲染历史对话", () => {
  it("从 localStorage 恢复的会话, 其历史 transcript 应渲染出来", async () => {
    localStorage.setItem("ee.selectedSid", "s_abc12345");
    localStorage.setItem("ee.openSids", JSON.stringify(["s_abc12345"]));

    const { default: Workspace } = await import("./Workspace");
    render(wrap(<Workspace />));

    await waitFor(() =>
      expect(screen.getByText("日本经济为什么停滞三十年")).toBeInTheDocument(),
    );
    expect(screen.getByText("核心是资产负债表衰退")).toBeInTheDocument();
  });
});
