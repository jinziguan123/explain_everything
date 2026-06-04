import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createSession, listSessions } from "../api/client";
import type { SessionSummary } from "../api/client";
import "./SessionSidebar.css";

export interface SessionSidebarProps {
  selectedSid: string | null;
  onSelect: (sid: string) => void;
}

export default function SessionSidebar({
  selectedSid,
  onSelect,
}: SessionSidebarProps) {
  const queryClient = useQueryClient();
  const [question, setQuestion] = useState("");

  const { data: sessions, isLoading, isError, error } = useQuery<
    SessionSummary[]
  >({
    queryKey: ["sessions"],
    queryFn: listSessions,
  });

  const createMut = useMutation({
    mutationFn: (q: string) => createSession(q),
    onSuccess: async (res) => {
      setQuestion("");
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
      onSelect(res.sid);
    },
  });

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const q = question.trim();
    if (!q || createMut.isPending) return;
    createMut.mutate(q);
  };

  return (
    <aside className="session-sidebar">
      <form className="session-new" onSubmit={submit}>
        <input
          className="session-new-input"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="想搞懂的问题…"
          aria-label="新建 session 的问题"
        />
        <button
          className="session-new-btn"
          type="submit"
          disabled={!question.trim() || createMut.isPending}
        >
          新建
        </button>
      </form>
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
              <span className="session-time">{s.updated_at}</span>
            </div>
          </button>
        ))}
      </div>
    </aside>
  );
}
