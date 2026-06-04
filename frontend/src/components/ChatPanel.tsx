import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { streamChat } from "../api/chatStream";
import type { SSEEvent } from "../api/chatStream";
import { getTranscript } from "../api/client";
import type { TranscriptEntry } from "../api/client";
import "./ChatPanel.css";

export interface ChatPanelProps {
  sid: string;
  /** 父组件用来刷新图谱 (A11) */
  onTurnComplete?: () => void;
}

interface ToolChip {
  id: number;
  name: string;
  done: boolean;
}

interface AssistantMessage {
  role: "assistant";
  /** markdown 正文 */
  text: string;
  /** 折叠的思考过程 */
  thinking: string;
  tools: ToolChip[];
  error: string | null;
  notice: string | null;
}

interface UserMessage {
  role: "user";
  text: string;
}

type ChatMessage = UserMessage | AssistantMessage;

function emptyAssistant(): AssistantMessage {
  return {
    role: "assistant",
    text: "",
    thinking: "",
    tools: [],
    error: null,
    notice: null,
  };
}

/** 从 SSE payload 里尽量解析出工具名 */
function toolName(content: unknown, metadata: unknown): string {
  if (typeof content === "string" && content.trim()) return content;
  if (content && typeof content === "object") {
    const c = content as Record<string, unknown>;
    if (typeof c.name === "string") return c.name;
    if (typeof c.tool === "string") return c.tool;
  }
  if (metadata && typeof metadata === "object") {
    const m = metadata as Record<string, unknown>;
    if (typeof m.name === "string") return m.name;
    if (typeof m.tool === "string") return m.tool;
  }
  return "工具";
}

function asString(content: unknown): string {
  return typeof content === "string" ? content : "";
}

/** 把持久化的 transcript (Anthropic 风格) 解析为 ChatPanel 的消息模型 */
function transcriptToMessages(entries: TranscriptEntry[]): ChatMessage[] {
  const out: ChatMessage[] = [];
  for (const entry of entries) {
    if (entry.role === "user") {
      // 纯文本 user 消息 -> UserMessage; 数组(tool_result 等管线)直接跳过
      if (typeof entry.content === "string") {
        out.push({ role: "user", text: entry.content });
      }
      continue;
    }
    // assistant: 聚合所有 block
    const blocks = Array.isArray(entry.content) ? entry.content : [];
    const texts: string[] = [];
    const thinkings: string[] = [];
    const tools: ToolChip[] = [];
    for (const raw of blocks) {
      if (!raw || typeof raw !== "object") continue;
      const b = raw as Record<string, unknown>;
      switch (b.type) {
        case "text":
          if (typeof b.text === "string") texts.push(b.text);
          break;
        case "thinking":
          if (typeof b.thinking === "string") thinkings.push(b.thinking);
          break;
        case "tool_use":
          tools.push({
            id: tools.length,
            name: typeof b.name === "string" ? b.name : "工具",
            done: true,
          });
          break;
        default:
          break;
      }
    }
    const text = texts.join("\n");
    const thinking = thinkings.join("");
    // 完全空的 assistant 条目跳过; 仅有 thinking 也保留
    if (!text && !thinking && tools.length === 0) continue;
    out.push({
      role: "assistant",
      text,
      thinking,
      tools,
      error: null,
      notice: null,
    });
  }
  return out;
}

export default function ChatPanel({ sid, onTurnComplete }: ChatPanelProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [status, setStatus] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const toolIdRef = useRef(0);
  const listRef = useRef<HTMLDivElement | null>(null);
  // 用户一旦发起新对话, 晚到的历史回放不得覆盖现场消息
  const dirtyRef = useRef(false);

  // 自动滚到底部
  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, status]);

  // 卸载时中断进行中的请求
  useEffect(() => {
    return () => abortRef.current?.abort();
  }, []);

  // 选中(切换) session 时回放已持久化的历史对话; 新流式轮次随后 append
  useEffect(() => {
    let cancelled = false;
    getTranscript(sid)
      .then((entries) => {
        if (!cancelled && !dirtyRef.current)
          setMessages(transcriptToMessages(entries));
      })
      .catch(() => {
        if (!cancelled && !dirtyRef.current) setMessages([]); // 新/空 session 或失败 -> 空
      });
    return () => {
      cancelled = true;
    };
  }, [sid]);

  /** 修改最后一条 assistant 消息 */
  const patchLastAssistant = useCallback(
    (fn: (m: AssistantMessage) => AssistantMessage) => {
      setMessages((prev) => {
        for (let i = prev.length - 1; i >= 0; i--) {
          if (prev[i].role === "assistant") {
            const next = prev.slice();
            next[i] = fn(prev[i] as AssistantMessage);
            return next;
          }
        }
        return prev;
      });
    },
    [],
  );

  const handleEvent = useCallback(
    (ev: SSEEvent) => {
      const { content, metadata } = ev.data;
      switch (ev.event) {
        case "assistant_text_delta":
          patchLastAssistant((m) => ({ ...m, text: m.text + asString(content) }));
          break;
        case "thinking_delta":
          patchLastAssistant((m) => ({
            ...m,
            thinking: m.thinking + asString(content),
          }));
          break;
        case "tool_use": {
          const id = ++toolIdRef.current;
          patchLastAssistant((m) => ({
            ...m,
            tools: [...m.tools, { id, name: toolName(content, metadata), done: false }],
          }));
          break;
        }
        case "tool_result":
          patchLastAssistant((m) => {
            const tools = m.tools.slice();
            for (let i = tools.length - 1; i >= 0; i--) {
              if (!tools[i].done) {
                tools[i] = { ...tools[i], done: true };
                break;
              }
            }
            return { ...m, tools };
          });
          break;
        case "status_start":
          setStatus(asString(content) || "处理中…");
          break;
        case "status_end":
          setStatus(null);
          break;
        case "turn_complete":
          setStreaming(false);
          setStatus(null);
          onTurnComplete?.();
          break;
        case "budget_exhausted":
          patchLastAssistant((m) => ({
            ...m,
            notice: asString(content) || "预算已用尽，本轮提前结束。",
          }));
          setStreaming(false);
          setStatus(null);
          break;
        case "error":
          patchLastAssistant((m) => ({
            ...m,
            error: asString(content) || "发生错误。",
          }));
          setStreaming(false);
          setStatus(null);
          break;
        default:
          break;
      }
    },
    [patchLastAssistant, onTurnComplete],
  );

  const send = useCallback(async () => {
    const text = input.trim();
    if (!text || streaming) return;

    setInput("");
    dirtyRef.current = true;
    setMessages((prev) => [...prev, { role: "user", text }, emptyAssistant()]);
    setStreaming(true);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      await streamChat(sid, text, handleEvent, controller.signal);
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        // 用户主动停止，静默处理
      } else {
        const msg = err instanceof Error ? err.message : String(err);
        patchLastAssistant((m) => ({ ...m, error: `请求失败：${msg}` }));
      }
    } finally {
      // turn_complete 没来时兜底（如网络断开）
      setStreaming(false);
      setStatus(null);
      abortRef.current = null;
    }
  }, [input, streaming, sid, handleEvent, patchLastAssistant]);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setStreaming(false);
    setStatus(null);
  }, []);

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void send();
    }
  };

  return (
    <div className="chat-panel">
      <div className="chat-messages" ref={listRef}>
        {messages.map((m, i) =>
          m.role === "user" ? (
            <div key={i} className="chat-msg chat-msg-user">
              <div className="chat-bubble">{m.text}</div>
            </div>
          ) : (
            <div key={i} className="chat-msg chat-msg-assistant">
              <div className="chat-bubble">
                {m.thinking && (
                  <details className="chat-thinking">
                    <summary>思考过程</summary>
                    <pre>{m.thinking}</pre>
                  </details>
                )}
                {m.tools.length > 0 && (
                  <div className="chat-tools">
                    {m.tools.map((t) => (
                      <span
                        key={t.id}
                        className={`chat-tool-chip${t.done ? " done" : ""}`}
                      >
                        {t.done ? "✅" : <span className="chat-spinner">⏳</span>} 🔧{" "}
                        {t.name}
                      </span>
                    ))}
                  </div>
                )}
                {m.text && (
                  <div className="chat-markdown">
                    <ReactMarkdown>{m.text}</ReactMarkdown>
                  </div>
                )}
                {m.notice && <div className="chat-notice">{m.notice}</div>}
                {m.error && <div className="chat-error">{m.error}</div>}
              </div>
            </div>
          ),
        )}
        {status && <div className="chat-status">{status}</div>}
      </div>

      <div className="chat-input-row">
        <div className="chat-input-wrap">
          <textarea
            className="chat-input"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="输入消息，回车发送（Shift+回车换行）"
            rows={2}
          />
          {streaming ? (
            <button
              className="chat-icon-btn chat-icon-stop"
              onClick={stop}
              title="停止"
              aria-label="停止"
            >
              ◼
            </button>
          ) : (
            <button
              className="chat-icon-btn chat-icon-send"
              onClick={() => void send()}
              disabled={!input.trim()}
              title="发送（回车）"
              aria-label="发送"
            >
              ↵
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
