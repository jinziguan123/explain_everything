import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";
import { streamChat } from "../api/chatStream";
import type { SSEEvent } from "../api/chatStream";
import { getTranscript } from "../api/client";
import type { TranscriptEntry } from "../api/client";
import "./ChatPanel.css";

export interface ChatPanelProps {
  sid: string;
  /** 是否为当前激活(可见)面板; keep-alive 下隐藏面板仍挂载、流式不中断 */
  active?: boolean;
  /** 父组件用来刷新图谱 (A11) */
  onTurnComplete?: () => void;
  /** 本会话首条消息发出时回调 (主流程之外并行起标题用); 传首条文本 */
  onFirstMessage?: (text: string) => void;
  /** 告知父组件当前会话是否为空 (无任何消息); 用于"空会话不重复新建" */
  onEmptyChange?: (empty: boolean) => void;
}

interface ToolChip {
  id: number;
  name: string;
  done: boolean;
  /** 工具入参 (展开卡片时显示) */
  input?: unknown;
  /** 工具返回结果文本 (展开卡片时显示) */
  result?: string | null;
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

/** 预扫整个 transcript, 收集 tool_use_id → 结果文本 (tool_result 在后续 user 消息里) */
function collectToolResults(entries: TranscriptEntry[]): Map<string, string> {
  const map = new Map<string, string>();
  for (const entry of entries) {
    if (entry.role !== "user" || !Array.isArray(entry.content)) continue;
    for (const raw of entry.content) {
      if (!raw || typeof raw !== "object") continue;
      const b = raw as Record<string, unknown>;
      if (b.type === "tool_result" && typeof b.tool_use_id === "string") {
        const c = b.content;
        map.set(b.tool_use_id, typeof c === "string" ? c : JSON.stringify(c));
      }
    }
  }
  return map;
}

/** 把持久化的 transcript (Anthropic 风格) 解析为 ChatPanel 的消息模型 */
function transcriptToMessages(entries: TranscriptEntry[]): ChatMessage[] {
  const out: ChatMessage[] = [];
  const resultsById = collectToolResults(entries);
  for (const entry of entries) {
    if (entry.role === "user") {
      // 纯文本 user 消息 -> UserMessage; 数组(tool_result 等管线)直接跳过
      if (typeof entry.content === "string") {
        // 合并连续重复的用户消息 (历史上"切换中断流/无反馈"导致的重复回车留下的)
        const last = out[out.length - 1];
        if (last && last.role === "user" && last.text === entry.content) continue;
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
        case "tool_use": {
          const tid = typeof b.id === "string" ? b.id : "";
          tools.push({
            id: tools.length,
            name: typeof b.name === "string" ? b.name : "工具",
            done: true,
            input: b.input,
            result: tid ? resultsById.get(tid) ?? null : null,
          });
          break;
        }
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

/* ── 工具卡片 (AI Studio 风格): 图标 + 工具名 + 状态徽章 + 展开箭头 ── */

function IconWrench() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14.7 6.3a4 4 0 0 0-5.4 5.3l-6 6a1.4 1.4 0 0 0 2 2l6-6a4 4 0 0 0 5.3-5.4l-2.5 2.5-2-2 2.6-2.4z" />
    </svg>
  );
}
function IconCheck() {
  return (
    <svg viewBox="0 0 24 24" width="13" height="13" fill="none"
      stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="9" />
      <path d="M8.5 12.5l2.3 2.3 4.7-5" />
    </svg>
  );
}
function IconClock() {
  return (
    <svg viewBox="0 0 24 24" width="13" height="13" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7.5V12l3 1.8" />
    </svg>
  );
}
function IconChevron() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M6 9l6 6 6-6" />
    </svg>
  );
}

function ToolCard({ tool }: { tool: ToolChip }) {
  const [open, setOpen] = useState(false);
  const inputStr =
    tool.input === undefined || tool.input === null
      ? ""
      : typeof tool.input === "string"
        ? tool.input
        : JSON.stringify(tool.input, null, 2);
  const hasResult = tool.result != null && tool.result !== "";
  const hasDetail = inputStr !== "" || hasResult;

  return (
    <div className={`tool-card${tool.done ? " done" : " running"}`}>
      <button
        type="button"
        className="tool-card-head"
        onClick={() => hasDetail && setOpen((v) => !v)}
        disabled={!hasDetail}
      >
        <span className="tool-card-icon">
          <IconWrench />
        </span>
        <span className="tool-card-name">{tool.name}</span>
        <span className={`tool-card-status${tool.done ? " done" : ""}`}>
          {tool.done ? <IconCheck /> : <IconClock />}
          {tool.done ? "Completed" : "Running"}
        </span>
        {hasDetail && (
          <span className={`tool-card-chevron${open ? " open" : ""}`}>
            <IconChevron />
          </span>
        )}
      </button>
      {open && hasDetail && (
        <div className="tool-card-body">
          {inputStr && (
            <div className="tool-card-section">
              <div className="tool-card-label">输入</div>
              <pre>{inputStr}</pre>
            </div>
          )}
          {hasResult && (
            <div className="tool-card-section">
              <div className="tool-card-label">结果</div>
              <pre>{tool.result}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function ChatPanel({
  sid,
  active = true,
  onTurnComplete,
  onFirstMessage,
  onEmptyChange,
}: ChatPanelProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [status, setStatus] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const toolIdRef = useRef(0);
  const listRef = useRef<HTMLDivElement | null>(null);
  // 用户一旦发起新对话, 晚到的历史回放不得覆盖现场消息
  const dirtyRef = useRef(false);
  // 是否"贴底跟随": 仅当用户本就在底部时才随新内容自动下滚; 用户上翻后停止跟随,
  // 避免流式生成时把正在阅读的视图硬拽到底。
  const stickToBottomRef = useRef(true);

  // 监听滚动: 距底足够近 → 继续跟随; 否则 (用户上翻) → 暂停跟随
  const onMessagesScroll = useCallback(() => {
    const el = listRef.current;
    if (!el) return;
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    stickToBottomRef.current = distance < 80;
  }, []);

  // 自动滚到底部: 仅在"贴底跟随"时执行 (消息变化时; 以及面板从隐藏→激活时,
  // 因隐藏期间 scroll 不生效)。用户上翻后 stickToBottomRef=false, 不再打扰。
  useEffect(() => {
    if (!active || !stickToBottomRef.current) return;
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, status, active]);

  // 上报当前会话是否为空 (无消息) — Workspace 据此决定"+"是否新建
  useEffect(() => {
    onEmptyChange?.(messages.length === 0);
  }, [messages, onEmptyChange]);

  // 卸载时中断进行中的请求
  useEffect(() => {
    return () => abortRef.current?.abort();
  }, []);

  // 选中(切换) session 时回放已持久化的历史对话; 新流式轮次随后 append
  useEffect(() => {
    let cancelled = false;
    stickToBottomRef.current = true; // 切到新会话 → 落到该会话底部
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
          const name = ev.data.tool_name || toolName(content, metadata);
          patchLastAssistant((m) => ({
            ...m,
            tools: [
              ...m.tools,
              { id, name, done: false, input: ev.data.tool_input, result: null },
            ],
          }));
          break;
        }
        case "tool_result":
          patchLastAssistant((m) => {
            const tools = m.tools.slice();
            const res = asString(ev.data.result) || asString(content);
            for (let i = tools.length - 1; i >= 0; i--) {
              if (!tools[i].done) {
                tools[i] = { ...tools[i], done: true, result: res };
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

    const isFirst = messages.length === 0; // 本会话首条消息
    setInput("");
    dirtyRef.current = true;
    stickToBottomRef.current = true; // 用户主动发消息 → 重新贴底跟随
    setMessages((prev) => [...prev, { role: "user", text }, emptyAssistant()]);
    setStreaming(true);
    // 主流程之外并行起标题: 不 await, 与下面的 streamChat 同时进行
    if (isFirst) onFirstMessage?.(text);

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
  }, [
    input,
    streaming,
    sid,
    messages.length,
    onFirstMessage,
    handleEvent,
    patchLastAssistant,
  ]);

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
      <div className="chat-messages" ref={listRef} onScroll={onMessagesScroll}>
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
                      <ToolCard key={t.id} tool={t} />
                    ))}
                  </div>
                )}
                {m.text && (
                  <div className="chat-markdown">
                    <ReactMarkdown
                      remarkPlugins={[remarkGfm, remarkMath]}
                      rehypePlugins={[rehypeKatex]}
                    >
                      {m.text}
                    </ReactMarkdown>
                  </div>
                )}
                {m.notice && <div className="chat-notice">{m.notice}</div>}
                {m.error && <div className="chat-error">{m.error}</div>}
              </div>
            </div>
          ),
        )}
        {streaming && (
          <div className="chat-status">
            <span className="chat-status-dots" aria-hidden="true">
              <span />
              <span />
              <span />
            </span>
            <span>{status || "AI 思考中…"}</span>
          </div>
        )}
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
