import type { ReactNode } from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import SessionSidebar from "./SessionSidebar";
import { listSessions, createSession } from "../api/client";

vi.mock("../api/client", () => ({
  listSessions: vi.fn(),
  createSession: vi.fn(),
}));

const mockList = vi.mocked(listSessions);
const mockCreate = vi.mocked(createSession);

const SESSIONS = [
  {
    sid: "s_1",
    question: "为什么天是蓝的",
    stage: "exploring",
    created_at: "2026-06-01T00:00:00Z",
    updated_at: "2026-06-02T00:00:00Z",
  },
  {
    sid: "s_2",
    question: "黑洞是什么",
    stage: "synthesizing",
    created_at: "2026-06-03T00:00:00Z",
    updated_at: "2026-06-04T00:00:00Z",
  },
];

function wrap(ui: ReactNode) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>;
}

beforeEach(() => {
  mockList.mockReset();
  mockCreate.mockReset();
});

describe("SessionSidebar", () => {
  it("渲染 session 列表", async () => {
    mockList.mockResolvedValue(SESSIONS);
    render(wrap(<SessionSidebar selectedSid={null} onSelect={vi.fn()} />));

    await waitFor(() =>
      expect(screen.getByText("为什么天是蓝的")).toBeInTheDocument(),
    );
    expect(screen.getByText("黑洞是什么")).toBeInTheDocument();
    expect(screen.getByText("exploring")).toBeInTheDocument();
  });

  it("无 session 时显示空状态", async () => {
    mockList.mockResolvedValue([]);
    render(wrap(<SessionSidebar selectedSid={null} onSelect={vi.fn()} />));

    await waitFor(() =>
      expect(screen.getByText(/还没有 session/)).toBeInTheDocument(),
    );
  });

  it("点击 session 调用 onSelect", async () => {
    mockList.mockResolvedValue(SESSIONS);
    const onSelect = vi.fn();
    render(wrap(<SessionSidebar selectedSid={null} onSelect={onSelect} />));

    await waitFor(() =>
      expect(screen.getByText("黑洞是什么")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByText("黑洞是什么"));
    expect(onSelect).toHaveBeenCalledWith("s_2");
  });

  it("点击 + 按钮直接新建 session 并自动选中 (无需预输入问题)", async () => {
    mockList.mockResolvedValue(SESSIONS);
    mockCreate.mockResolvedValue({ sid: "s_new" });
    const onSelect = vi.fn();
    render(wrap(<SessionSidebar selectedSid={null} onSelect={onSelect} />));

    await waitFor(() => expect(mockList).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("button", { name: "新建会话" }));

    await waitFor(() => expect(mockCreate).toHaveBeenCalled());
    await waitFor(() => expect(onSelect).toHaveBeenCalledWith("s_new"));
  });

  it("高亮选中的 session", async () => {
    mockList.mockResolvedValue(SESSIONS);
    render(wrap(<SessionSidebar selectedSid="s_1" onSelect={vi.fn()} />));

    await waitFor(() =>
      expect(screen.getByText("为什么天是蓝的")).toBeInTheDocument(),
    );
    const selected = screen
      .getByText("为什么天是蓝的")
      .closest("button");
    expect(selected).toHaveClass("selected");
  });
});
