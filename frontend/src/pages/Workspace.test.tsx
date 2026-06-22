import type { ReactNode } from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createSession } from "../api/client";

// 轻量替身, 隔离重依赖 (cytoscape / react-markdown / 网络)
vi.mock("../components/ChatPanel", () => ({
  default: ({ sid, active }: { sid: string; active?: boolean }) => (
    <div data-testid={`chat-${sid}`} data-active={String(!!active)}>
      chatpanel:{sid}
    </div>
  ),
}));
vi.mock("../components/DraftChatPanel", () => ({
  default: ({ onSend }: { onSend: (t: string) => void }) => (
    <button onClick={() => onSend("你好")}>draft-send</button>
  ),
}));
vi.mock("../components/GraphPanel", () => ({ default: () => <div /> }));
vi.mock("../components/SessionSidebar", () => ({
  default: ({ onNew }: { onNew: () => void }) => (
    <button onClick={onNew}>新建</button>
  ),
}));
vi.mock("../api/client", () => ({
  createSession: vi.fn(async () => ({ sid: "s_new00001" })),
  deleteSession: vi.fn(),
  autotitleSession: vi.fn(async () => ({ title: "x" })),
}));

const mockCreate = vi.mocked(createSession);

function wrap(ui: ReactNode) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>;
}

beforeEach(() => {
  localStorage.clear();
  mockCreate.mockClear();
});

describe("Workspace 视图持久化", () => {
  it("刷新(重新挂载)时从 localStorage 恢复选中会话 → 渲染其 ChatPanel(active)", async () => {
    localStorage.setItem("ee.selectedSid", "s_abc12345");
    localStorage.setItem("ee.openSids", JSON.stringify(["s_abc12345"]));

    const { default: Workspace } = await import("./Workspace");
    render(wrap(<Workspace />));

    const panel = screen.getByTestId("chat-s_abc12345");
    expect(panel).toBeInTheDocument();
    expect(panel).toHaveAttribute("data-active", "true");
  });

  it("localStorage 仅有 selectedSid 也能恢复 (openSids 自动并入)", async () => {
    localStorage.setItem("ee.selectedSid", "s_def67890");

    const { default: Workspace } = await import("./Workspace");
    render(wrap(<Workspace />));

    expect(screen.getByTestId("chat-s_def67890")).toBeInTheDocument();
  });

  it("无 localStorage → 显示空提示, 不渲染任何 ChatPanel", async () => {
    const { default: Workspace } = await import("./Workspace");
    render(wrap(<Workspace />));

    expect(screen.getByText(/从左侧选择一个 session/)).toBeInTheDocument();
    expect(screen.queryByText(/chatpanel:/)).not.toBeInTheDocument();
  });
});

describe("Workspace 新建会话 (草稿态, 发消息才落数据)", () => {
  it("点+新建只进入草稿态, 不调 createSession、不渲染实会话面板", async () => {
    const { default: Workspace } = await import("./Workspace");
    render(wrap(<Workspace />));

    fireEvent.click(screen.getByText("新建"));

    // 进入草稿: 显示草稿面板, 但未创建后端会话、未挂载实 ChatPanel
    expect(screen.getByText("draft-send")).toBeInTheDocument();
    expect(mockCreate).not.toHaveBeenCalled();
    expect(screen.queryByText(/chatpanel:/)).not.toBeInTheDocument();
  });

  it("草稿发出首条消息 → 此时才 createSession 并挂载实会话面板", async () => {
    const { default: Workspace } = await import("./Workspace");
    render(wrap(<Workspace />));

    fireEvent.click(screen.getByText("新建"));
    fireEvent.click(screen.getByText("draft-send"));

    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
    // 实会话面板挂载 (sid 来自 createSession)
    await waitFor(() =>
      expect(screen.getByTestId("chat-s_new00001")).toBeInTheDocument(),
    );
    // 草稿面板退场
    expect(screen.queryByText("draft-send")).not.toBeInTheDocument();
  });
});
