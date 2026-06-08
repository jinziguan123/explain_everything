import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import ChatPanel from "./ChatPanel";
import { openChatStream, startChat, stopChat } from "../api/chatStream";
import type { SSEEvent } from "../api/chatStream";
import { getTranscript } from "../api/client";
import type { TranscriptEntry } from "../api/client";

vi.mock("../api/chatStream", () => ({
  startChat: vi.fn(async () => {}),
  openChatStream: vi.fn(async () => {}),
  stopChat: vi.fn(async () => {}),
}));

vi.mock("../api/client", () => ({
  getTranscript: vi.fn(async () => [] as TranscriptEntry[]),
}));

const mockOpen = vi.mocked(openChatStream);
const mockStart = vi.mocked(startChat);
const mockStop = vi.mocked(stopChat);
const mockGetTranscript = vi.mocked(getTranscript);

// 挂载时 ChatPanel 会自动订阅一次 (重连探测); 真实后端无活动轮时回 no_active_run。
const mountNoActiveRun = async (_sid: string, onEvent: (ev: SSEEvent) => void) => {
  onEvent({ event: "no_active_run", data: { content: null } });
};

beforeEach(() => {
  mockOpen.mockReset();
  mockStart.mockReset();
  mockStop.mockReset();
  mockGetTranscript.mockReset();
  mockStart.mockResolvedValue(undefined);
  mockStop.mockResolvedValue(undefined);
  mockOpen.mockImplementation(mountNoActiveRun); // 默认: 无活动轮
  mockGetTranscript.mockResolvedValue([]);
});

function typeAndSend(text: string) {
  const input = screen.getByRole("textbox");
  fireEvent.change(input, { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: /发送|send/i }));
}

describe("ChatPanel", () => {
  it("发送消息并渲染流式 assistant 回复, 触发 onTurnComplete", async () => {
    mockOpen
      .mockImplementationOnce(mountNoActiveRun) // 挂载订阅
      .mockImplementationOnce(async (_sid, onEvent) => {
        // 发送后的订阅: run_start 重建气泡 → 文本 → 完成
        onEvent({ event: "run_start", data: { content: null } });
        onEvent({ event: "assistant_text_delta", data: { content: "你好世界" } });
        onEvent({ event: "turn_complete", data: { content: null } });
      });
    const onTurnComplete = vi.fn();
    render(<ChatPanel sid="s_test" onTurnComplete={onTurnComplete} />);

    typeAndSend("在吗");

    expect(screen.getByText("在吗")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByText("你好世界")).toBeInTheDocument(),
    );
    expect(onTurnComplete).toHaveBeenCalledTimes(1);
    expect(mockStart).toHaveBeenCalledWith("s_test", "在吗");
  });

  it("渲染 thinking 折叠块和工具卡片(完成态)", async () => {
    mockOpen
      .mockImplementationOnce(mountNoActiveRun)
      .mockImplementationOnce(async (_sid, onEvent) => {
        onEvent({ event: "run_start", data: { content: null } });
        onEvent({ event: "thinking_delta", data: { content: "我在想…" } });
        onEvent({ event: "tool_use", data: { content: null, tool_name: "search_graph" } });
        onEvent({ event: "tool_result", data: { content: "ok" } });
        onEvent({ event: "turn_complete", data: { content: null } });
      });
    render(<ChatPanel sid="s_test" />);
    typeAndSend("查一下");

    await waitFor(() => expect(screen.getByText("思考过程")).toBeInTheDocument());
    expect(screen.getByText("我在想…")).toBeInTheDocument();
    const name = screen.getByText("search_graph");
    expect(name.closest(".tool-card")?.className).toContain("done");
    expect(screen.getByText("Completed")).toBeInTheDocument();
  });

  it("error 事件渲染错误行并退出 streaming", async () => {
    mockOpen
      .mockImplementationOnce(mountNoActiveRun)
      .mockImplementationOnce(async (_sid, onEvent) => {
        onEvent({ event: "run_start", data: { content: null } });
        onEvent({ event: "error", data: { content: "炸了" } });
      });
    render(<ChatPanel sid="s_test" />);
    typeAndSend("hi");

    await waitFor(() => expect(screen.getByText("炸了")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /发送/ })).toBeInTheDocument();
  });

  it("budget_exhausted 显示提示", async () => {
    mockOpen
      .mockImplementationOnce(mountNoActiveRun)
      .mockImplementationOnce(async (_sid, onEvent) => {
        onEvent({ event: "run_start", data: { content: null } });
        onEvent({ event: "budget_exhausted", data: { content: "预算用尽了" } });
      });
    render(<ChatPanel sid="s_test" />);
    typeAndSend("hi");

    await waitFor(() =>
      expect(screen.getByText("预算用尽了")).toBeInTheDocument(),
    );
  });

  it("streaming 时显示停止按钮, 点击调用 stopChat 并恢复发送按钮", async () => {
    let captured: ((ev: SSEEvent) => void) | null = null;
    mockOpen
      .mockImplementationOnce(mountNoActiveRun)
      .mockImplementationOnce(async (_sid, onEvent, signal) => {
        captured = onEvent;
        // 永不结束的订阅, 直到被 abort
        await new Promise<void>((resolve) => {
          signal?.addEventListener("abort", () => resolve());
        });
      });
    render(<ChatPanel sid="s_test" />);
    typeAndSend("hi");

    await waitFor(() => expect(captured).not.toBeNull());
    captured!({ event: "run_start", data: { content: null } });
    captured!({ event: "assistant_text_delta", data: { content: "进行中" } });
    await waitFor(() => expect(screen.getByText("进行中")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /停止/ }));

    expect(mockStop).toHaveBeenCalledWith("s_test");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /发送/ })).toBeInTheDocument(),
    );
  });

  it("Enter 发送, Shift+Enter 不发送", async () => {
    render(<ChatPanel sid="s_test" />);
    const input = screen.getByRole("textbox");

    fireEvent.change(input, { target: { value: "shift测试" } });
    fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
    expect(mockStart).not.toHaveBeenCalled();

    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(mockStart).toHaveBeenCalledTimes(1));
  });

  it("挂载时回放已持久化的历史对话", async () => {
    mockGetTranscript.mockResolvedValue([
      { role: "user", content: "历史问题" },
      {
        role: "assistant",
        content: [{ type: "text", text: "历史回答" }],
      },
    ] as TranscriptEntry[]);

    render(<ChatPanel sid="s_test" />);

    await waitFor(() =>
      expect(screen.getByText("历史问题")).toBeInTheDocument(),
    );
    expect(screen.getByText("历史回答")).toBeInTheDocument();
    expect(mockGetTranscript).toHaveBeenCalledWith("s_test");
  });

  it("挂载时若有进行中的生成则自动重连续传", async () => {
    // 模拟刷新后重连: 挂载订阅直接收到 run_start + 续传内容 (无需用户再发送)
    mockOpen.mockImplementation(async (_sid, onEvent) => {
      onEvent({ event: "run_start", data: { content: null } });
      onEvent({ event: "assistant_text_delta", data: { content: "续传中的回答" } });
      onEvent({ event: "turn_complete", data: { content: null } });
    });
    render(<ChatPanel sid="s_test" />);

    await waitFor(() =>
      expect(screen.getByText("续传中的回答")).toBeInTheDocument(),
    );
    // 用户未发送 → startChat 不应被调用
    expect(mockStart).not.toHaveBeenCalled();
  });
});
