import type { KnowledgeOverview } from "../api/client";
import "./KnowledgeDashboard.css";

export interface KnowledgeDashboardProps {
  overview: KnowledgeOverview;
  onRejectTheory: (id: string) => void;
}

const LEVEL_LABELS: Record<number, string> = {
  0: "现象",
  1: "模式",
  2: "深层原因",
};

function levelLabel(level: number): string {
  return LEVEL_LABELS[level] ?? `层级 ${level}`;
}

const STATUS_LABELS: Record<string, string> = {
  stable: "稳定",
  tentative: "暂定",
  weakened: "已削弱",
};

function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status;
}

function fmtPower(v: number): string {
  if (v === undefined || v === null || Number.isNaN(v)) return "—";
  return v.toFixed(2);
}

export default function KnowledgeDashboard({
  overview,
  onRejectTheory,
}: KnowledgeDashboardProps) {
  const { theory_count, top_variables, theories } = overview;

  return (
    <div className="kd">
      <div className="kd-cards">
        <div className="kd-card">
          <div className="kd-card-num">{overview.session_count}</div>
          <div className="kd-card-label">会话数</div>
        </div>
        <div className="kd-card">
          <div className="kd-card-num">{overview.variable_count}</div>
          <div className="kd-card-label">变量数</div>
        </div>
        <div className="kd-card">
          <div className="kd-card-num">{theory_count.stable}</div>
          <div className="kd-card-label">稳定理论</div>
        </div>
        <div className="kd-card">
          <div className="kd-card-num">{theory_count.tentative}</div>
          <div className="kd-card-label">暂定理论</div>
        </div>
        <div className="kd-card" title="复用 ≥2 次的变量占比 — 跨 session 累积价值 (H3) 的代理指标">
          <div className="kd-card-num">
            {overview.h3_reuse ? `${Math.round(overview.h3_reuse.reuse_rate * 100)}%` : "—"}
          </div>
          <div className="kd-card-label">
            变量复用率{overview.h3_reuse ? ` (${overview.h3_reuse.vars_reused} 个)` : ""}
          </div>
        </div>
      </div>

      <section className="kd-section">
        <h3 className="kd-section-title">高频变量</h3>
        {top_variables.length === 0 ? (
          <div className="kd-empty">暂无变量</div>
        ) : (
          <table className="kd-table">
            <thead>
              <tr>
                <th>名称</th>
                <th>复用次数</th>
                <th>层级</th>
              </tr>
            </thead>
            <tbody>
              {top_variables.map((v) => (
                <tr key={v.global_id}>
                  <td>{v.name}</td>
                  <td>{v.reuse_count}</td>
                  <td>{levelLabel(v.abstraction_level)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="kd-section">
        <h3 className="kd-section-title">理论</h3>
        {theories.length === 0 ? (
          <div className="kd-empty">暂无理论</div>
        ) : (
          <div className="kd-theories">
            {theories.map((t) => (
              <div
                className={`kd-theory${t.stability_status === "weakened" ? " kd-theory-weakened" : ""}`}
                key={t.id}
                data-testid="theory-card"
              >
                <div className="kd-theory-summary">{t.summary}</div>
                <div className="kd-theory-meta">
                  <span className="kd-tag">{t.motif_type}</span>
                  <span className="kd-tag">状态: {statusLabel(t.stability_status)}</span>
                  <span className="kd-tag">
                    准确度: {fmtPower(t.predictive_power)}
                    {t.predictive_power_source === "ledger"
                      ? ` (台账 ${t.predictions?.hits ?? 0}/${(t.predictions?.hits ?? 0) + (t.predictions?.misses ?? 0)})`
                      : " (回溯)"}
                  </span>
                  <span className="kd-tag">支持 {t.supporting_session_count} 个 session</span>
                  {(t.predictions?.total ?? 0) === 0 && (
                    <span className="kd-tag kd-tag-narrative">叙事级 · 未登记预测</span>
                  )}
                </div>
                <button
                  className="kd-theory-reject"
                  onClick={() => onRejectTheory(t.id)}
                >
                  拒绝
                </button>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
