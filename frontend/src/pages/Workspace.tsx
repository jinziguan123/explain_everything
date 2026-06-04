import { useCallback, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import SessionSidebar from "../components/SessionSidebar";
import ChatPanel from "../components/ChatPanel";
import GraphPanel from "../components/GraphPanel";
import "./Workspace.css";

export default function Workspace() {
  const [selectedSid, setSelectedSid] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const handleTurnComplete = useCallback(() => {
    if (selectedSid) {
      void queryClient.invalidateQueries({ queryKey: ["graph", selectedSid] });
    }
  }, [selectedSid, queryClient]);

  return (
    <div className="workspace">
      <SessionSidebar selectedSid={selectedSid} onSelect={setSelectedSid} />

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

      <section className="workspace-right">
        {selectedSid ? (
          <GraphPanel key={selectedSid} sid={selectedSid} />
        ) : (
          <div className="workspace-hint">选择 session 后，这里展示其知识图谱。</div>
        )}
      </section>
    </div>
  );
}
