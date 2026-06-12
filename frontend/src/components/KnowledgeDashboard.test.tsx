import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import KnowledgeDashboard from "./KnowledgeDashboard";
import type { KnowledgeOverview } from "../api/client";

const overview: KnowledgeOverview = {
  session_count: 7,
  variable_count: 23,
  theory_count: { stable: 2, tentative: 4 },
  top_variables: [
    {
      global_id: "v_001",
      name: "认知负载",
      reuse_count: 5,
      abstraction_level: 1,
    },
    {
      global_id: "v_002",
      name: "反馈延迟",
      reuse_count: 3,
      abstraction_level: 2,
    },
  ],
  theories: [
    {
      id: "t_001",
      summary: "高负载导致拖延",
      motif_type: "feedback_loop",
      predictive_power: 0.82,
      stability_status: "stable",
      supporting_session_count: 4,
    },
  ],
};

describe("KnowledgeDashboard", () => {
  it("渲染指标卡片数字", () => {
    render(<KnowledgeDashboard overview={overview} onRejectTheory={vi.fn()} />);
    expect(screen.getByText("7")).toBeInTheDocument();
    expect(screen.getByText("23")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("4")).toBeInTheDocument();
    expect(screen.getByText("会话数")).toBeInTheDocument();
    expect(screen.getByText("稳定理论")).toBeInTheDocument();
    expect(screen.getByText("暂定理论")).toBeInTheDocument();
  });

  it("渲染变量表行 (含中文层级)", () => {
    render(<KnowledgeDashboard overview={overview} onRejectTheory={vi.fn()} />);
    expect(screen.getByText("认知负载")).toBeInTheDocument();
    expect(screen.getByText("反馈延迟")).toBeInTheDocument();
    // abstraction_level 1 → 模式, 2 → 深层原因
    expect(screen.getByText("模式")).toBeInTheDocument();
    expect(screen.getByText("深层原因")).toBeInTheDocument();
  });

  it("渲染 theory 卡片字段", () => {
    render(<KnowledgeDashboard overview={overview} onRejectTheory={vi.fn()} />);
    const card = screen.getByTestId("theory-card");
    expect(card).toBeInTheDocument();
    expect(screen.getByText("高负载导致拖延")).toBeInTheDocument();
    expect(screen.getByText("feedback_loop")).toBeInTheDocument();
    // Phase T: 准确度标来源 (无台账 → 回溯); 无登记预测 → 叙事级标记
    expect(screen.getByText(/准确度: 0\.82/)).toBeInTheDocument();
    expect(screen.getByText(/回溯/)).toBeInTheDocument();
    expect(screen.getByText(/叙事级/)).toBeInTheDocument();
    expect(screen.getByText("状态: 稳定")).toBeInTheDocument();
    expect(screen.getByText("支持 4 个 session")).toBeInTheDocument();
  });

  it("weakened 理论显示已削弱并淡化", () => {
    const weakened = {
      ...overview,
      theories: [{
        ...overview.theories[0],
        stability_status: "weakened",
        predictive_power_source: "ledger",
        predictions: { total: 3, pending: 1, hits: 0, misses: 2 },
      }],
    };
    render(<KnowledgeDashboard overview={weakened} onRejectTheory={vi.fn()} />);
    expect(screen.getByText("状态: 已削弱")).toBeInTheDocument();
    expect(screen.getByText(/台账 0\/2/)).toBeInTheDocument();
    expect(screen.getByTestId("theory-card").className).toContain("kd-theory-weakened");
  });

  it("点击拒绝按钮以 theory id 调用回调", () => {
    const onReject = vi.fn();
    render(
      <KnowledgeDashboard overview={overview} onRejectTheory={onReject} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "拒绝" }));
    expect(onReject).toHaveBeenCalledWith("t_001");
  });

  it("空数据显示友好提示", () => {
    const empty: KnowledgeOverview = {
      session_count: 0,
      variable_count: 0,
      theory_count: { stable: 0, tentative: 0 },
      top_variables: [],
      theories: [],
    };
    render(<KnowledgeDashboard overview={empty} onRejectTheory={vi.fn()} />);
    expect(screen.getByText("暂无变量")).toBeInTheDocument();
    expect(screen.getByText("暂无理论")).toBeInTheDocument();
  });
});
