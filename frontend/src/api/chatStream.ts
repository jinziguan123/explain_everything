export interface SSEEvent {
  event: string;
  data: {
    content: unknown;
    metadata?: unknown;
    // tool_use / tool_result 事件携带的额外字段 (后端 sse.py 转发)
    tool_name?: string;
    tool_input?: unknown;
    result?: unknown;
  };
}

/** 读取一个 SSE Response 流, 按帧解析并回调。流结束 (或 abort) 时 resolve。 */
async function pumpSSE(
  resp: Response,
  onEvent: (ev: SSEEvent) => void,
): Promise<void> {
  if (!resp.body) throw new Error("no stream body");
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const frame = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const ev: Partial<SSEEvent> = {};
      for (const line of frame.split("\n")) {
        if (line.startsWith("event: ")) ev.event = line.slice(7);
        else if (line.startsWith("data: ")) ev.data = JSON.parse(line.slice(6));
      }
      if (ev.event) onEvent(ev as SSEEvent);
    }
  }
}

/** 启动一轮后台生成 (POST)。404=会话不存在, 409=已有进行中的生成。 */
export async function startChat(
  sid: string,
  message: string,
  signal?: AbortSignal,
): Promise<void> {
  const r = await fetch(`/api/sessions/${sid}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
    signal,
  });
  if (!r.ok) throw new Error(`startChat ${r.status}`);
}

/** 订阅当前轮事件流 (GET)。刷新后重连即调用此函数; 无进行中的生成时
 * 后端会发一个 no_active_run 事件后立即结束。 */
export async function openChatStream(
  sid: string,
  onEvent: (ev: SSEEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch(`/api/sessions/${sid}/chat/stream`, { signal });
  if (!resp.ok) throw new Error(`openChatStream ${resp.status}`);
  await pumpSSE(resp, onEvent);
}

/** 取消进行中的后台生成 (POST)。best-effort, 失败静默。 */
export async function stopChat(sid: string): Promise<void> {
  try {
    await fetch(`/api/sessions/${sid}/chat/stop`, { method: "POST" });
  } catch {
    // 忽略: 停止失败不影响 UI
  }
}
