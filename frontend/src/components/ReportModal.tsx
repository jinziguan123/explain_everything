import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { generateReport } from "../api/client";
import "./GraphPanel.css";

export interface ReportModalProps {
  sid: string;
  open: boolean;
  onClose: () => void;
}

/** Phase X1: 叙事报告面板 — 滩头用户的一等交付物 (生成 + 渲染 + 下载)。 */
export default function ReportModal({ sid, open, onClose }: ReportModalProps) {
  const [markdown, setMarkdown] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setMarkdown(null);
    generateReport(sid)
      .then((r) => {
        if (!cancelled) setMarkdown(r.markdown);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, sid]);

  if (!open) return null;

  const download = () => {
    if (!markdown) return;
    const blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `report-${sid}.md`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="report-overlay" role="dialog" aria-label="叙事报告">
      <div className="report-modal">
        <div className="report-modal-header">
          <span className="report-modal-title">叙事报告</span>
          <div className="report-modal-actions">
            <button
              className="report-btn"
              onClick={download}
              disabled={!markdown}
            >
              下载 .md
            </button>
            <button className="node-drawer-close" onClick={onClose} aria-label="关闭">
              ✕
            </button>
          </div>
        </div>
        <div className="report-modal-body">
          {loading && (
            <div className="graph-msg">生成报告中 (1 次 LLM 调用, 约半分钟)…</div>
          )}
          {error && <div className="graph-msg graph-error">报告生成失败：{error}</div>}
          {markdown && (
            <div className="report-markdown">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{markdown}</ReactMarkdown>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
