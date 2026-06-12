import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  draftPredictions,
  listPredictions,
  resolvePrediction,
} from "../api/client";
import type { PredictionItem } from "../api/client";
import "./KnowledgeDashboard.css";

const METHOD_LABELS: Record<string, string> = {
  retrodiction: "回溯",
  search: "检索",
  time_window: "时间窗",
};

const STATUS_LABELS: Record<string, string> = {
  pending: "待结算",
  hit: "命中",
  miss: "落空",
};

/** Phase X1: 预测台账面板 — 机器提案 (起草), 人签字 (结算)。 */
export default function PredictionsPanel() {
  const qc = useQueryClient();
  const [note, setNote] = useState<string | null>(null);

  const { data: predictions = [], isLoading } = useQuery<PredictionItem[]>({
    queryKey: ["predictions"],
    queryFn: () => listPredictions(),
  });

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ["predictions"] });
    void qc.invalidateQueries({ queryKey: ["knowledge"] });  // overview 含理论台账徽标
  };

  const resolveMut = useMutation({
    mutationFn: ({ id, hit }: { id: string; hit: boolean }) =>
      resolvePrediction(id, hit),
    onSuccess: (res) => {
      invalidate();
      if (res.theory_stats.weakened) {
        setNote(
          `理论 ${res.prediction.theory_id} 已连续落空 2 次, 将降级为"已削弱"`,
        );
      }
    },
    onError: (e) => setNote(`结算失败: ${e instanceof Error ? e.message : String(e)}`),
  });

  const draftMut = useMutation({
    mutationFn: () => draftPredictions(),
    onSuccess: (drafted) => {
      invalidate();
      setNote(
        drafted.length === 0
          ? "没有起草出新预测 (理论都已有台账记录)"
          : `已为理论起草并登记 ${drafted.length} 条预测 (机器提案), 请适时结算`,
      );
    },
    onError: (e) => setNote(`起草失败: ${e instanceof Error ? e.message : String(e)}`),
  });

  return (
    <section className="kd-section" data-testid="predictions-panel">
      <div className="kd-section-head">
        <h3 className="kd-section-title">预测台账</h3>
        <button
          className="kd-draft-btn"
          onClick={() => draftMut.mutate()}
          disabled={draftMut.isPending}
          title="LLM 为没有台账记录的理论起草可检验预测 (origin=llm)"
        >
          {draftMut.isPending ? "起草中…" : "机器起草预测"}
        </button>
      </div>
      {note && (
        <div className="kd-note" onClick={() => setNote(null)}>
          {note}
        </div>
      )}
      {isLoading ? (
        <div className="kd-empty">加载中…</div>
      ) : predictions.length === 0 ? (
        <div className="kd-empty">
          台账为空 — 理论需要登记可检验预测才能晋升 (可点"机器起草预测")
        </div>
      ) : (
        <table className="kd-table">
          <thead>
            <tr>
              <th>断言</th>
              <th>理论</th>
              <th>方式</th>
              <th>期限</th>
              <th>来源</th>
              <th>状态</th>
              <th>结算</th>
            </tr>
          </thead>
          <tbody>
            {predictions.map((p) => (
              <tr key={p.id} className={p.due ? "kd-pred-due" : ""}>
                <td className="kd-pred-assertion">{p.assertion}</td>
                <td className="kd-dim">{p.theory_id}</td>
                <td>{METHOD_LABELS[p.method] ?? p.method}</td>
                <td>
                  {p.deadline ?? "—"}
                  {p.due && <span className="kd-due-mark"> 已到期</span>}
                </td>
                <td>{p.origin === "llm" ? "机器" : "人工"}</td>
                <td className={`kd-pred-${p.status}`}>
                  {STATUS_LABELS[p.status]}
                </td>
                <td>
                  {p.status === "pending" ? (
                    <span className="kd-resolve-btns">
                      <button
                        className="kd-resolve-hit"
                        disabled={resolveMut.isPending}
                        onClick={() => resolveMut.mutate({ id: p.id, hit: true })}
                      >
                        命中
                      </button>
                      <button
                        className="kd-resolve-miss"
                        disabled={resolveMut.isPending}
                        onClick={() => resolveMut.mutate({ id: p.id, hit: false })}
                      >
                        落空
                      </button>
                    </span>
                  ) : (
                    <span className="kd-dim">已结算</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
