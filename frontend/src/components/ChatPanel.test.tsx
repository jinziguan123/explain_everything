import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import ChatPanel from "./ChatPanel";
import { streamChat } from "../api/chatStream";
import type { SSEEvent } from "../api/chatStream";
import { getTranscript } from "../api/client";
import type { TranscriptEntry } from "../api/client";

vi.mock("../api/chatStream", () => ({
  streamChat: vi.fn(),
}));

vi.mock("../api/client", () => ({
  getTranscript: vi.fn(async () => [] as TranscriptEntry[]),
}));

const mockStream = vi.mocked(streamChat);
const mockGetTranscript = vi.mocked(getTranscript);

beforeEach(() => {
  mockStream.mockReset();
  mockGetTranscript.mockReset();
  mockGetTranscript.mockResolvedValue([]);
});

function typeAndSend(text: string) {
  const input = screen.getByRole("textbox");
  fireEvent.change(input, { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: /发送|send/i }));
}

describe("ChatPanel", () => {
  it("发送消息并渲染流式 assistant 回复, 触发 onTurnComplete", async () => {
    mockStream.mockImplementation(async (_sid, _msg, onEvent) => {
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
    expect(mockStream).toHaveBeenCalledWith(
      "s_test",
      "在吗",
      expect.any(Function),
      expect.any(AbortSignal),
    );
  });

  it("渲染 thinking 折叠块和工具 chip(完成态)", async () => {
    mockStream.mockImplementation(async (_sid, _msg, onEvent) => {
      onEvent({ event: "thinking_delta", data: { content: "我在想…" } });
      onEvent({ event: "tool_use", data: { content: "search_graph" } });
      onEvent({ event: "tool_result", data: { content: "ok" } });
      onEvent({ event: "turn_complete", data: { content: null } });
    });
    render(<ChatPanel sid="s_test" />);
    typeAndSend("查一下");

    await waitFor(() => expect(screen.getByText("思考过程")).toBeInTheDocument());
    expect(screen.getByText("我在想…")).toBeInTheDocument();
    // 工具卡片: 名字在 .tool-card-name, 完成态在 .tool-card 容器 + Completed 徽章
    const name = screen.getByText("search_graph");
    expect(name.closest(".tool-card")?.className).toContain("done");
    expect(screen.getByText("Completed")).toBeInTheDocument();
  });

  it("error 事件渲染错误行并退出 streaming", async () => {
    mockStream.mockImplementation(async (_sid, _msg, onEvent) => {
      onEvent({ event: "error", data: { content: "炸了" } });
    });
    render(<ChatPanel sid="s_test" />);
    typeAndSend("hi");

    await waitFor(() =>
      expect(screen.getByText("炸了")).toBeInTheDocument(),
    );
    // 错误后回到发送按钮(非停止)
    expect(
      screen.getByRole("button", { name: /发送/ }),
    ).toBeInTheDocument();
  });

  it("budget_exhausted 显示提示", async () => {
    mockStream.mockImplementation(async (_sid, _msg, onEvent) => {
      onEvent({ event: "budget_exhausted", data: { content: "预算用尽了" } });
    });
    render(<ChatPanel sid="s_test" />);
    typeAndSend("hi");

    await waitFor(() =>
      expect(screen.getByText("预算用尽了")).toBeInTheDocument(),
    );
  });

  it("streaming 时显示停止按钮, 点击 abort 并恢复发送按钮", async () => {
    let captured: ((ev: SSEEvent) => void) | null = null;
    let abortFired = false;
    mockStream.mockImplementation(async (_sid, _msg, onEvent, signal) => {
      captured = onEvent;
      signal?.addEventListener("abort", () => {
        abortFired = true;
      });
      // 永不结束的流, 直到被 abort
      await new Promise<void>((resolve) => {
        signal?.addEventListener("abort", () => resolve());
      });
    });
    render(<ChatPanel sid="s_test" />);
    typeAndSend("hi");

    // 先推一点内容确认在流
    captured!({ event: "assistant_text_delta", data: { content: "进行中" } });
    await waitFor(() =>
      expect(screen.getByText("进行中")).toBeInTheDocument(),
    );
    const stopBtn = screen.getByRole("button", { name: /停止/ });
    fireEvent.click(stopBtn);

    expect(abortFired).toBe(true);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /发送/ })).toBeInTheDocument(),
    );
  });

  it("Enter 发送, Shift+Enter 不发送", () => {
    mockStream.mockResolvedValue(undefined);
    render(<ChatPanel sid="s_test" />);
    const input = screen.getByRole("textbox");

    fireEvent.change(input, { target: { value: "shift测试" } });
    fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
    expect(mockStream).not.toHaveBeenCalled();

    fireEvent.keyDown(input, { key: "Enter" });
    expect(mockStream).toHaveBeenCalledTimes(1);
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
});
