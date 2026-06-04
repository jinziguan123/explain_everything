import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createSession, listSessions } from "../api/client";
import type { SessionSummary } from "../api/client";
import "./SessionSidebar.css";

export interface SessionSidebarProps {
  selectedSid: string | null;
  onSelect: (sid: string) => void;
}

/** 新建 session 的默认标题 (无需用户预输入; 时间在列表项另行展示以区分)。 */
const NEW_SESSION_TITLE = "新会话";

/** Unix 秒时间戳 → 本地可读时间; 非法值回退为空串。 */
function formatTs(ts: number): string {
  if (!ts || Number.isNaN(ts)) return "";
  return new Date(ts * 1000).toLocaleString();
}

export default function SessionSidebar({
  selectedSid,
  onSelect,
}: SessionSidebarProps) {
  const queryClient = useQueryClient();

  const { data: sessions, isLoading, isError, error } = useQuery<
    SessionSummary[]
  >({
    queryKey: ["sessions"],
    queryFn: listSessions,
  });

  const createMut = useMutation({
    mutationFn: () => createSession(NEW_SESSION_TITLE),
    onSuccess: async (res) => {
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
      onSelect(res.sid); // 选中新 session → ChatPanel(key=sid) 重挂 → 聊天清空
    },
  });

  return (
    <aside className="session-sidebar">
      {/* 纯 + 长条按钮: 点击直接新建空 session (类似 TUI /new), 不预输入问题 */}
      <button
        className="session-new-btn"
        type="button"
        onClick={() => !createMut.isPending && createMut.mutate()}
        disabled={createMut.isPending}
        title="新建会话"
        aria-label="新建会话"
      >
        ＋
      </button>
      {createMut.isError && (
        <div className="session-error">
          创建失败：
          {createMut.error instanceof Error
            ? createMut.error.message
            : String(createMut.error)}
        </div>
      )}

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
          <button
            key={s.sid}
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
              <span className="session-time">{formatTs(s.updated_at)}</span>
            </div>
          </button>
        ))}
      </div>
    </aside>
  );
}
