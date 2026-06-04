import { useCallback, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import SessionSidebar from "../components/SessionSidebar";
import ChatPanel from "../components/ChatPanel";
import GraphPanel from "../components/GraphPanel";
import { createSession, deleteSession } from "../api/client";
import "./Workspace.css";

const NEW_SESSION_TITLE = "新会话";

export default function Workspace() {
  const [selectedSid, setSelectedSid] = useState<string | null>(null);
  const [leftCollapsed, setLeftCollapsed] = useState(false);
  const [rightCollapsed, setRightCollapsed] = useState(false);
  // 当前选中会话是否为空 (无任何消息); 由 ChatPanel 上报
  const [currentEmpty, setCurrentEmpty] = useState(true);
  const queryClient = useQueryClient();

  const handleTurnComplete = useCallback(() => {
    if (selectedSid) {
      void queryClient.invalidateQueries({ queryKey: ["graph", selectedSid] });
    }
  }, [selectedSid, queryClient]);

  const createMut = useMutation({
    mutationFn: () => createSession(NEW_SESSION_TITLE),
    onSuccess: async (res) => {
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
      setCurrentEmpty(true); // 新会话本就空
      setSelectedSid(res.sid); // 选中 → ChatPanel(key=sid) 重挂 → 聊天清空
    },
  });

  // 新建守卫: 当前已是一个空会话 → 留在原地, 不再堆一条空记录
  const handleNew = useCallback(() => {
    if (createMut.isPending) return;
    if (selectedSid && currentEmpty) return;
    createMut.mutate();
  }, [createMut, selectedSid, currentEmpty]);

  const deleteMut = useMutation({
    mutationFn: (sid: string) => deleteSession(sid),
    onSuccess: async (_res, sid) => {
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
      if (sid === selectedSid) setSelectedSid(null); // 删的是当前 → 回到空白
    },
  });

  const handleDelete = useCallback(
    (sid: string) => {
      if (deleteMut.isPending) return;
      if (!window.confirm("删除该会话? 此操作不可恢复。")) return;
      deleteMut.mutate(sid);
    },
    [deleteMut],
  );

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
          <SessionSidebar
            selectedSid={selectedSid}
            onSelect={setSelectedSid}
            onNew={handleNew}
            onDelete={handleDelete}
            creating={createMut.isPending}
          />
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
            onEmptyChange={setCurrentEmpty}
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
