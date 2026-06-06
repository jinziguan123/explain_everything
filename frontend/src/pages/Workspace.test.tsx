import type { ReactNode } from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// 轻量替身, 隔离重依赖 (cytoscape / react-markdown / 网络)
vi.mock("../components/ChatPanel", () => ({
  default: ({ sid, active }: { sid: string; active?: boolean }) => (
    <div data-testid={`chat-${sid}`} data-active={String(!!active)}>
      chatpanel:{sid}
    </div>
  ),
}));
vi.mock("../components/GraphPanel", () => ({ default: () => <div /> }));
vi.mock("../components/SessionSidebar", () => ({ default: () => <div /> }));

function wrap(ui: ReactNode) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>;
}

beforeEach(() => {
  localStorage.clear();
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
