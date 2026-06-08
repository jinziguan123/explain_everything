import { useQuery } from "@tanstack/react-query";
import { listSessions } from "../api/client";
import type { SessionSummary } from "../api/client";
import "./SessionSidebar.css";

export interface SessionSidebarProps {
  selectedSid: string | null;
  onSelect: (sid: string) => void;
  /** 点击 + 新建会话 (创建逻辑在 Workspace, 含空会话守卫) */
  onNew: () => void;
  /** 删除某会话 (确认逻辑在 Workspace) */
  onDelete: (sid: string) => void;
  /** 新建进行中 → 禁用 + 按钮 */
  creating?: boolean;
}

/** Unix 秒时间戳 → 本地可读时间; 非法值回退为空串。 */
function formatTs(ts: number): string {
  if (!ts || Number.isNaN(ts)) return "";
  return new Date(ts * 1000).toLocaleString();
}

export default function SessionSidebar({
  selectedSid,
  onSelect,
  onNew,
  onDelete,
  creating = false,
}: SessionSidebarProps) {
  const { data: sessions, isLoading, isError, error } = useQuery<
    SessionSummary[]
  >({
    queryKey: ["sessions"],
    queryFn: listSessions,
  });

  return (
    <aside className="session-sidebar">
      {/* 纯 + 长条按钮: 点击直接新建空 session (类似 TUI /new), 不预输入问题 */}
      <button
        className="session-new-btn"
        type="button"
        onClick={onNew}
        disabled={creating}
        title="新建会话"
        aria-label="新建会话"
      >
        ＋
      </button>

      <div className="session-list">
        {isLoading && <div className="session-msg">加载中…</div>}
        {isError && (
          <div className="session-msg session-error">
            加载失败：{error instanceof Error ? error.message : String(error)}
          </div>
        )}
        {!isLoading && !isError && (sessions?.length ?? 0) === 0 && (
          <div className="session-msg session-empty">
            还没有 session，新建一个开始吧。
          </div>
        )}
        {sessions?.map((s) => (
          <div key={s.sid} className="session-item-wrap">
            <button
              type="button"
              className={`session-item${
                s.sid === selectedSid ? " selected" : ""
              }`}
              onClick={() => onSelect(s.sid)}
              aria-current={s.sid === selectedSid}
            >
              <div className="session-item-q">{s.question}</div>
              <div className="session-item-meta">
                <span className="session-stage">{s.stage}</span>
                <span className="session-time">
                  {formatTs(s.last_user_message_at || s.updated_at)}
                </span>
              </div>
            </button>
            <button
              type="button"
              className="session-del"
              onClick={() => onDelete(s.sid)}
              title="删除会话"
              aria-label={`删除会话 ${s.question}`}
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </aside>
  );
}
