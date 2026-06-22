import { useState } from "react";
import "./ChatPanel.css";

/** 新建会话的"草稿"面板: 只有输入框, 不落任何后端数据。
 * 用户发出首条消息时才回调 onSend → 由父组件真正 createSession 并挂载实面板。
 * 复用 ChatPanel 的样式, 视觉与空会话一致。 */
export default function DraftChatPanel({
  onSend,
}: {
  onSend: (text: string) => void;
}) {
  const [input, setInput] = useState("");

  const submit = () => {
    const text = input.trim();
    if (!text) return;
    setInput("");
    onSend(text);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <div className="chat-panel">
      <div className="chat-messages">
        <div className="chat-draft-hint">输入消息，开始一个新会话</div>
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
            autoFocus
          />
          <button
            className="chat-icon-btn chat-icon-send"
            onClick={submit}
            disabled={!input.trim()}
            title="发送（回车）"
            aria-label="发送"
          >
            ↵
          </button>
        </div>
      </div>
    </div>
  );
}
