import { describe, it, expect, vi } from "vitest";
import { streamChat } from "./chatStream";

function mockStreamResponse(text: string): Response {
  const body = new ReadableStream({
    start(c) {
      c.enqueue(new TextEncoder().encode(text));
      c.close();
    },
  });
  return new Response(body, { status: 200 });
}

describe("streamChat", () => {
  it("parses SSE frames into events", async () => {
    const sse =
      'event: assistant_text_delta\ndata: {"content":"你好"}\n\n' +
      'event: turn_complete\ndata: {"content":null}\n\n';
    vi.spyOn(global, "fetch").mockResolvedValue(mockStreamResponse(sse));
    const got: string[] = [];
    await streamChat("s_x", "hi", (ev) => got.push(ev.event));
    expect(got).toEqual(["assistant_text_delta", "turn_complete"]);
  });

  it("passes content through", async () => {
    const sse = 'event: assistant_text_delta\ndata: {"content":"答案"}\n\n';
    vi.spyOn(global, "fetch").mockResolvedValue(mockStreamResponse(sse));
    const contents: unknown[] = [];
    await streamChat("s_x", "hi", (ev) => contents.push(ev.data.content));
    expect(contents).toEqual(["答案"]);
  });

  it("handles frames split across chunks", async () => {
    // 一帧被拆成两个网络块, 解析器须靠 buffer 拼接
    const part1 = 'event: assistant_text_delta\nda';
    const part2 = 'ta: {"content":"x"}\n\n';
    const body = new ReadableStream({
      start(c) {
        c.enqueue(new TextEncoder().encode(part1));
        c.enqueue(new TextEncoder().encode(part2));
        c.close();
      },
    });
    vi.spyOn(global, "fetch").mockResolvedValue(new Response(body, { status: 200 }));
    const got: string[] = [];
    await streamChat("s_x", "hi", (ev) => got.push(ev.event));
    expect(got).toEqual(["assistant_text_delta"]);
  });
});
