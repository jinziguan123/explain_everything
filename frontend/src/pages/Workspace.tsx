import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import SessionSidebar from "../components/SessionSidebar";
import ChatPanel from "../components/ChatPanel";
import DraftChatPanel from "../components/DraftChatPanel";
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
const LS_LEFT_W = "ee.leftWidth";
const LS_RIGHT_W = "ee.rightWidth";
const MIN_LEFT = 180;
const MIN_CENTER = 320;
const MIN_RIGHT = 200;
const DEFAULT_LEFT = 280;
const DEFAULT_RIGHT = 420;

function computeGridCols(lw: number, rw: number, lc: boolean, rc: boolean) {
  const colLeft = lc ? "0" : `${lw}px`;
  const colStrip1 = lc ? "30px" : "0";
  const colStrip2 = rc ? "30px" : "0";
  const colRight = rc ? "0" : `${rw}px`;
  return `${colLeft} ${colStrip1} minmax(${MIN_CENTER}px, 1fr) ${colStrip2} ${colRight}`;
}

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
  const [leftWidth, setLeftWidth] = useState(() => {
    const v = localStorage.getItem(LS_LEFT_W);
    return v ? Math.max(MIN_LEFT, parseInt(v, 10)) : DEFAULT_LEFT;
  });
  const [rightWidth, setRightWidth] = useState(() => {
    const v = localStorage.getItem(LS_RIGHT_W);
    return v ? Math.max(MIN_RIGHT, parseInt(v, 10)) : DEFAULT_RIGHT;
  });
  const dragRef = useRef<{
    side: "left" | "right";
    startX: number;
    startW: number;
  } | null>(null);
  const workspaceRef = useRef<HTMLDivElement | null>(null);
  const leftWidthRef = useRef(leftWidth);
  const rightWidthRef = useRef(rightWidth);
  const leftCollapsedRef = useRef(leftCollapsed);
  const rightCollapsedRef = useRef(rightCollapsed);
  const leftHandleRef = useRef<HTMLDivElement | null>(null);
  const rightHandleRef = useRef<HTMLDivElement | null>(null);
  leftWidthRef.current = leftWidth;
  rightWidthRef.current = rightWidth;
  leftCollapsedRef.current = leftCollapsed;
  rightCollapsedRef.current = rightCollapsed;
  // 草稿态: 点"+新建"后进入, 此时不在后端落任何数据; 用户发出首条消息才 createSession。
  const [composing, setComposing] = useState(false);
  // 新建会话待自动发出的首条消息 (草稿 → 实会话的交接); key=sid。
  const [pendingMsg, setPendingMsg] = useState<Record<string, string>>({});
  // 新建但尚未自动生成标题的会话 — 首条消息后触发 autotitle
  const needTitleRef = useRef<Set<string>>(new Set());
  // 防草稿首条消息重复 createSession
  const creatingRef = useRef(false);
  const queryClient = useQueryClient();

  useEffect(() => {
    localStorage.setItem(LS_OPEN, JSON.stringify(openSids));
  }, [openSids]);
  useEffect(() => {
    if (selectedSid) localStorage.setItem(LS_SELECTED, selectedSid);
    else localStorage.removeItem(LS_SELECTED);
  }, [selectedSid]);

  // ── 面板宽度拖拽 ──
  useEffect(() => {
    localStorage.setItem(LS_LEFT_W, String(leftWidth));
  }, [leftWidth]);
  useEffect(() => {
    localStorage.setItem(LS_RIGHT_W, String(rightWidth));
  }, [rightWidth]);

  const startDrag = useCallback(
    (side: "left" | "right") => (e: React.MouseEvent) => {
      e.preventDefault();
      dragRef.current = {
        side,
        startX: e.clientX,
        startW: side === "left" ? leftWidthRef.current : rightWidthRef.current,
      };
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
    },
    [],
  );

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      const d = dragRef.current;
      if (!d) return;
      const total = workspaceRef.current?.clientWidth ?? window.innerWidth;
      const delta = e.clientX - d.startX;
      if (d.side === "left") {
        const otherW = rightCollapsedRef.current ? 30 : rightWidthRef.current;
        const max = total - otherW - MIN_CENTER;
        const newW = Math.max(MIN_LEFT, Math.min(max, d.startW + delta));
        leftWidthRef.current = newW;
        if (leftHandleRef.current) leftHandleRef.current.style.left = `${newW - 3}px`;
      } else {
        const otherW = leftCollapsedRef.current ? 30 : leftWidthRef.current;
        const max = total - otherW - MIN_CENTER;
        const newW = Math.max(MIN_RIGHT, Math.min(max, d.startW - delta));
        rightWidthRef.current = newW;
        if (rightHandleRef.current) rightHandleRef.current.style.right = `${newW - 3}px`;
      }
      if (workspaceRef.current) {
        workspaceRef.current.style.gridTemplateColumns = computeGridCols(
          leftWidthRef.current, rightWidthRef.current,
          leftCollapsedRef.current, rightCollapsedRef.current,
        );
      }
    };
    const onUp = () => {
      if (!dragRef.current) return;
      const side = dragRef.current.side;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      dragRef.current = null;
      if (side === "left") setLeftWidth(leftWidthRef.current);
      else setRightWidth(rightWidthRef.current);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, []);

  // 选中会话: 退出草稿态, 确保进入 openSids (常驻), 再设为当前
  const selectSession = useCallback((sid: string) => {
    setComposing(false);
    setOpenSids((prev) => (prev.includes(sid) ? prev : [...prev, sid]));
    setSelectedSid(sid);
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

  // 点"+新建": 仅进入草稿态, 不在后端创建数据 (避免列表里立刻多一条空"新会话")。
  const handleNew = useCallback(() => {
    if (composing) return; // 已在草稿中
    setSelectedSid(null);
    setComposing(true);
  }, [composing]);

  // 草稿首条消息发出 → 此刻才真正 createSession, 挂载实会话面板并把消息自动发出。
  const handleDraftSend = useCallback(
    async (text: string) => {
      if (creatingRef.current) return;
      creatingRef.current = true;
      try {
        const { sid } = await createSession(NEW_SESSION_TITLE);
        needTitleRef.current.add(sid); // 首条消息后自动起标题
        setPendingMsg((m) => ({ ...m, [sid]: text })); // 实面板挂载后自动发
        setOpenSids((prev) => (prev.includes(sid) ? prev : [...prev, sid]));
        setSelectedSid(sid);
        setComposing(false);
        void queryClient.invalidateQueries({ queryKey: ["sessions"] });
      } finally {
        creatingRef.current = false;
      }
    },
    [queryClient],
  );

  const handleDelete = useCallback(
    async (sid: string) => {
      if (!window.confirm("删除该会话? 此操作不可恢复。")) return;
      try {
        await deleteSession(sid);
      } catch {
        return; // 失败保持原状
      }
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
      setOpenSids((prev) => prev.filter((s) => s !== sid)); // 卸载其 ChatPanel
      setPendingMsg((m) => {
        const next = { ...m };
        delete next[sid];
        return next;
      });
      if (sid === selectedSid) setSelectedSid(null); // 删的是当前 → 回到空白
    },
    [queryClient, selectedSid],
  );

  const cls = [
    "workspace",
    leftCollapsed ? "left-collapsed" : "",
    rightCollapsed ? "right-collapsed" : "",
  ]
    .filter(Boolean)
    .join(" ");

  const gridCols = computeGridCols(leftWidth, rightWidth, leftCollapsed, rightCollapsed);

  return (
    <div className={cls} ref={workspaceRef} style={{ gridTemplateColumns: gridCols }}>
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
            selectedSid={composing ? null : selectedSid}
            onSelect={selectSession}
            onNew={handleNew}
            onDelete={handleDelete}
            creating={composing}
          />
        </div>
      </aside>

      {/* 左侧拖拽手柄 */}
      {!leftCollapsed && (
        <div
          ref={leftHandleRef}
          className="workspace-drag-handle"
          style={{ left: leftWidth - 3 }}
          onMouseDown={startDrag("left")}
        />
      )}

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
        {composing && (
          <div className="chat-host" style={{ display: "flex" }}>
            <DraftChatPanel onSend={handleDraftSend} />
          </div>
        )}
        {!composing && !selectedSid && (
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
            style={{
              display: !composing && sid === selectedSid ? "flex" : "none",
            }}
          >
            <ChatPanel
              sid={sid}
              active={!composing && sid === selectedSid}
              initialMessage={pendingMsg[sid]}
              onTurnComplete={() => handleTurnComplete(sid)}
              onFirstMessage={(text) => handleFirstMessage(sid, text)}
            />
          </div>
        ))}
      </section>

      {/* 右侧拖拽手柄 */}
      {!rightCollapsed && (
        <div
          ref={rightHandleRef}
          className="workspace-drag-handle"
          style={{ right: rightWidth - 3 }}
          onMouseDown={startDrag("right")}
        />
      )}

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
          {!composing && selectedSid ? (
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
