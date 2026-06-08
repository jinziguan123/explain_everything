import type { ReactNode } from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import SessionSidebar from "./SessionSidebar";
import { listSessions } from "../api/client";

vi.mock("../api/client", () => ({
  listSessions: vi.fn(),
}));

const mockList = vi.mocked(listSessions);

const SESSIONS = [
  {
    sid: "s_1",
    question: "为什么天是蓝的",
    stage: "exploring",
    created_at: 1781000000,
    updated_at: 1781100000,
    last_user_message_at: 1781100000,
  },
  {
    sid: "s_2",
    question: "黑洞是什么",
    stage: "synthesizing",
    created_at: 1781200000,
    updated_at: 1781300000,
    last_user_message_at: 1781300000,
  },
];

function wrap(ui: ReactNode) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>;
}

/** 带默认 props 渲染 */
function renderSidebar(
  props: Partial<React.ComponentProps<typeof SessionSidebar>> = {},
) {
  const all = {
    selectedSid: null,
    onSelect: vi.fn(),
    onNew: vi.fn(),
    onDelete: vi.fn(),
    ...props,
  };
  render(wrap(<SessionSidebar {...all} />));
  return all;
}

beforeEach(() => {
  mockList.mockReset();
});

describe("SessionSidebar", () => {
  it("渲染 session 列表", async () => {
    mockList.mockResolvedValue(SESSIONS);
    renderSidebar();

    await waitFor(() =>
      expect(screen.getByText("为什么天是蓝的")).toBeInTheDocument(),
    );
    expect(screen.getByText("黑洞是什么")).toBeInTheDocument();
    expect(screen.getByText("exploring")).toBeInTheDocument();
  });

  it("无 session 时显示空状态", async () => {
    mockList.mockResolvedValue([]);
    renderSidebar();

    await waitFor(() =>
      expect(screen.getByText(/还没有 session/)).toBeInTheDocument(),
    );
  });

  it("点击 session 调用 onSelect", async () => {
    mockList.mockResolvedValue(SESSIONS);
    const onSelect = vi.fn();
    renderSidebar({ onSelect });

    await waitFor(() =>
      expect(screen.getByText("黑洞是什么")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByText("黑洞是什么"));
    expect(onSelect).toHaveBeenCalledWith("s_2");
  });

  it("点击 + 按钮调用 onNew (新建逻辑在父组件)", async () => {
    mockList.mockResolvedValue(SESSIONS);
    const onNew = vi.fn();
    renderSidebar({ onNew });

    await waitFor(() => expect(mockList).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "新建会话" }));
    expect(onNew).toHaveBeenCalled();
  });

  it("点击删除按钮调用 onDelete(sid)", async () => {
    mockList.mockResolvedValue(SESSIONS);
    const onDelete = vi.fn();
    renderSidebar({ onDelete });

    await waitFor(() =>
      expect(screen.getByText("黑洞是什么")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: "删除会话 黑洞是什么" }));
    expect(onDelete).toHaveBeenCalledWith("s_2");
  });

  it("高亮选中的 session", async () => {
    mockList.mockResolvedValue(SESSIONS);
    renderSidebar({ selectedSid: "s_1" });

    await waitFor(() =>
      expect(screen.getByText("为什么天是蓝的")).toBeInTheDocument(),
    );
    const selected = screen.getByText("为什么天是蓝的").closest("button");
    expect(selected).toHaveClass("selected");
  });
});
