import { useCallback, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import SessionSidebar from "../components/SessionSidebar";
import ChatPanel from "../components/ChatPanel";
import GraphPanel from "../components/GraphPanel";
import "./Workspace.css";

export default function Workspace() {
  const [selectedSid, setSelectedSid] = useState<string | null>(null);
  const [leftCollapsed, setLeftCollapsed] = useState(false);
  const [rightCollapsed, setRightCollapsed] = useState(false);
  const queryClient = useQueryClient();

  const handleTurnComplete = useCallback(() => {
    if (selectedSid) {
      void queryClient.invalidateQueries({ queryKey: ["graph", selectedSid] });
    }
  }, [selectedSid, queryClient]);

  const cls = [
    "workspace",
    leftCollapsed ? "left-collapsed" : "",
    rightCollapsed ? "right-collapsed" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={cls}>
      {/* 左: 会话列表 (可折叠) */}
      <aside className="workspace-left">
        <div className="workspace-pane-header">
          <span className="workspace-pane-title">会话列表</span>
          <button
            className="workspace-toggle"
            onClick={() => setLeftCollapsed(true)}
            title="收起会话列表"
            aria-label="收起会话列表"
          >
            ‹
          </button>
        </div>
        <div className="workspace-pane-body">
          <SessionSidebar selectedSid={selectedSid} onSelect={setSelectedSid} />
        </div>
      </aside>

      {/* 左侧折叠后的展开条 */}
      <button
        className="workspace-strip workspace-strip-left"
        onClick={() => setLeftCollapsed(false)}
        title="展开会话列表"
        aria-label="展开会话列表"
      >
        <span className="workspace-strip-chevron">›</span>
        <span className="workspace-strip-label">会话列表</span>
      </button>

      <section className="workspace-center">
        {selectedSid ? (
          // key 让切换 session 时重建聊天面板, 清空上一段对话
          <ChatPanel
            key={selectedSid}
            sid={selectedSid}
            onTurnComplete={handleTurnComplete}
          />
        ) : (
          <div className="workspace-hint">
            从左侧选择一个 session，或新建一个开始对话。
          </div>
        )}
      </section>

      {/* 右侧折叠后的展开条 */}
      <button
        className="workspace-strip workspace-strip-right"
        onClick={() => setRightCollapsed(false)}
        title="展开知识图谱"
        aria-label="展开知识图谱"
      >
        <span className="workspace-strip-chevron">‹</span>
        <span className="workspace-strip-label">知识图谱</span>
      </button>

      {/* 右: 知识图谱 (可折叠) */}
      <aside className="workspace-right">
        <div className="workspace-pane-header">
          <button
            className="workspace-toggle"
            onClick={() => setRightCollapsed(true)}
            title="收起知识图谱"
            aria-label="收起知识图谱"
          >
            ›
          </button>
          <span className="workspace-pane-title">知识图谱</span>
        </div>
        <div className="workspace-pane-body">
          {selectedSid ? (
            <GraphPanel key={selectedSid} sid={selectedSid} />
          ) : (
            <div className="workspace-hint">
              选择 session 后，这里展示其知识图谱。
            </div>
          )}
        </div>
      </aside>
    </div>
  );
}
