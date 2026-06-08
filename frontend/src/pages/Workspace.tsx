import { useCallback, useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import SessionSidebar from "../components/SessionSidebar";
import ChatPanel from "../components/ChatPanel";
import GraphPanel from "../components/GraphPanel";
import {
  autotitleSession,
  createSession,
  deleteSession,
} from "../api/client";
import "./Workspace.css";

const NEW_SESSION_TITLE = "新会话";
const LS_SELECTED = "ee.selectedSid";
const LS_OPEN = "ee.openSids";

export default function Workspace() {
  // 视图状态持久化到 localStorage → 刷新/HMR 后恢复上次会话与历史 (避免"内容不见了")
  const [selectedSid, setSelectedSid] = useState<string | null>(
    () => localStorage.getItem(LS_SELECTED),
  );
  // 本次运行打开过的所有 session — 它们的 ChatPanel 常驻 (keep-alive), 切换只切
  // 显隐, 后台的 AI 流式不中断 → 支持同时操控多个 session。
  const [openSids, setOpenSids] = useState<string[]>(() => {
    let arr: string[] = [];
    try {
      const raw = localStorage.getItem(LS_OPEN);
      if (raw) arr = JSON.parse(raw) as string[];
    } catch {
      arr = [];
    }
    const sel = localStorage.getItem(LS_SELECTED);
    if (sel && !arr.includes(sel)) arr = [...arr, sel];
    return arr;
  });
  const [leftCollapsed, setLeftCollapsed] = useState(false);
  const [rightCollapsed, setRightCollapsed] = useState(false);
  // 每个会话是否为空 (无任何消息); 由各 ChatPanel 上报, 供"空会话不重复新建"判断
  const [emptyMap, setEmptyMap] = useState<Record<string, boolean>>({});
  // 新建但尚未自动生成标题的会话 — 首轮对话完成后触发 autotitle
  const needTitleRef = useRef<Set<string>>(new Set());
  const queryClient = useQueryClient();

  useEffect(() => {
    localStorage.setItem(LS_OPEN, JSON.stringify(openSids));
  }, [openSids]);
  useEffect(() => {
    if (selectedSid) localStorage.setItem(LS_SELECTED, selectedSid);
    else localStorage.removeItem(LS_SELECTED);
  }, [selectedSid]);

  // 选中会话: 确保进入 openSids (常驻), 再设为当前
  const selectSession = useCallback((sid: string) => {
    setOpenSids((prev) => (prev.includes(sid) ? prev : [...prev, sid]));
    setSelectedSid(sid);
  }, []);

  const setEmpty = useCallback((sid: string, empty: boolean) => {
    setEmptyMap((m) => (m[sid] === empty ? m : { ...m, [sid]: empty }));
  }, []);

  // 一轮对话完成: 刷新该会话图谱 + 会话列表 (last_user_message_at 已落盘,
  // 触发按最近提问时间重排序, 把刚用过的会话置顶)。
  const handleTurnComplete = useCallback(
    (sid: string) => {
      void queryClient.invalidateQueries({ queryKey: ["graph", sid] });
      void queryClient.invalidateQueries({ queryKey: ["sessions"] });
    },
    [queryClient],
  );

  // 新建会话首条消息发出 → 主流程之外并行用 light LLM 起标题 (不等整轮跑完)。
  // 把首条文本直传后端, 避免读尚未落盘的 transcript。
  const handleFirstMessage = useCallback(
    (sid: string, text: string) => {
      if (!needTitleRef.current.has(sid)) return;
      needTitleRef.current.delete(sid);
      autotitleSession(sid, text)
        .then(() => queryClient.invalidateQueries({ queryKey: ["sessions"] }))
        .catch(() => {});
    },
    [queryClient],
  );

  const createMut = useMutation({
    mutationFn: () => createSession(NEW_SESSION_TITLE),
    onSuccess: async (res) => {
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
      setEmpty(res.sid, true); // 新会话本就空
      needTitleRef.current.add(res.sid); // 首轮后自动起标题
      selectSession(res.sid);
    },
  });

  // 新建守卫: 当前已是一个空会话 → 留在原地, 不再堆一条空记录
  const handleNew = useCallback(() => {
    if (createMut.isPending) return;
    if (selectedSid && (emptyMap[selectedSid] ?? true)) return;
    createMut.mutate();
  }, [createMut, selectedSid, emptyMap]);

  const deleteMut = useMutation({
    mutationFn: (sid: string) => deleteSession(sid),
    onSuccess: async (_res, sid) => {
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
      setOpenSids((prev) => prev.filter((s) => s !== sid)); // 卸载其 ChatPanel
      setEmptyMap((m) => {
        const next = { ...m };
        delete next[sid];
        return next;
      });
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
            onSelect={selectSession}
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
        {!selectedSid && (
          <div className="workspace-hint">
            从左侧选择一个 session，或新建一个开始对话。
          </div>
        )}
        {/* keep-alive: 每个打开过的 session 各保留常驻面板, 仅切显隐。
            后台 session 的 AI 流式不被卸载中断 → 可同时操控多个 session。 */}
        {openSids.map((sid) => (
          <div
            key={sid}
            className="chat-host"
            style={{ display: sid === selectedSid ? "flex" : "none" }}
          >
            <ChatPanel
              sid={sid}
              active={sid === selectedSid}
              onTurnComplete={() => handleTurnComplete(sid)}
              onFirstMessage={(text) => handleFirstMessage(sid, text)}
              onEmptyChange={(empty) => setEmpty(sid, empty)}
            />
          </div>
        ))}
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
