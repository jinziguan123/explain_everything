export interface SSEEvent {
  event: string;
  data: { content: unknown; metadata?: unknown };
}

export async function streamChat(
  sid: string,
  message: string,
  onEvent: (ev: SSEEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch(`/api/sessions/${sid}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
    signal,
  });
  if (!resp.ok) throw new Error(`chat ${resp.status}`);
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
