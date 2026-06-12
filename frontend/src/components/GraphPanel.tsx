import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import cytoscape from "cytoscape";
import { getGraph, groundSession } from "../api/client";
import { COLA_LAYOUT, ensureColaLayout } from "../lib/cyLayout";
import NodeDrawer from "./NodeDrawer";
import type { NodeData } from "./NodeDrawer";
import ReportModal from "./ReportModal";
import "./GraphPanel.css";

export interface GraphPanelProps {
  sid: string;
  /** 父组件改变它来触发重新拉取 (turn_complete/action 后); 也可用 react-query invalidate */
  refreshKey?: number;
}

interface GraphPayload {
  root_question?: string;
  elements?: {
    nodes?: cytoscape.NodeDefinition[];
    edges?: cytoscape.EdgeDefinition[];
  };
}

// 按层级着色 (彩色, 类 MiroFish): 0 现象 / 1 模式 / 2 深层原因
const LEVEL_COLORS: Record<string, string> = {
  "0": "#2f6df6",
  "1": "#f5921e",
  "2": "#9b5de5",
};
const LEVEL_NAMES: Record<string, string> = {
  "0": "现象",
  "1": "模式",
  "2": "深层原因",
};
// 边关系类型 → 中文标签
const REL_LABELS: Record<string, string> = {
  causes: "导致",
  manifests_as: "表现为",
  amplifies: "增强",
  suppresses: "抑制",
  constrains: "约束",
};

function buildCyStyle(showEdgeLabels: boolean): cytoscape.StylesheetStyle[] {
  // 数值/过渡等属性 @types/cytoscape 标注偏严, 整体断言一次避免逐属性报错。
  return [
    {
      selector: "node",
      style: {
        label: "data(label)",
        shape: "ellipse",
        width: 24,
        height: 24,
        "background-color": "#9aa0a6",
        "border-width": 2,
        "border-color": "#ffffff",
        color: "#1f2937",
        "font-size": "10px",
        "text-valign": "bottom",
        "text-halign": "center",
        "text-margin-y": 4,
        "text-wrap": "wrap",
        "text-max-width": "120px",
        "text-outline-color": "#ffffff",
        "text-outline-width": 2,
        // 颜色/尺寸变化带过渡动画
        "transition-property": "background-color, width, height, opacity",
        "transition-duration": "220ms",
      },
    },
    { selector: "node[level = 0]", style: { "background-color": LEVEL_COLORS["0"] } },
    {
      selector: "node[level = 1]",
      style: { "background-color": LEVEL_COLORS["1"], width: 30, height: 30 },
    },
    {
      selector: "node[level = 2]",
      style: { "background-color": LEVEL_COLORS["2"], width: 38, height: 38 },
    },
    { selector: 'node[lifecycle = "decayed"]', style: { opacity: 0.35 } },
    {
      selector: "edge",
      style: {
        width: "mapData(confidence, 0, 1, 1, 3.5)",
        "line-color": "#c4cad3",
        "target-arrow-color": "#c4cad3",
        "target-arrow-shape": "triangle",
        "arrow-scale": 0.8,
        "curve-style": "bezier",
        label: showEdgeLabels ? "data(relLabel)" : "",
        "font-size": "8px",
        color: "#8a93a0",
        "text-rotation": "autorotate",
        "text-background-color": "#ffffff",
        "text-background-opacity": 0.85,
        "text-background-padding": 2,
        "transition-property": "line-color, opacity",
        "transition-duration": "220ms",
      },
    },
    { selector: 'edge[relation = "manifests_as"]', style: { "line-style": "dashed" } },
    // Phase G 证据等级 (后置选择器覆盖 relation 样式: 证据状态优先于关系类型)
    {
      selector: 'edge[tier = "fact"]',
      style: {
        "line-color": "#16a34a",
        "target-arrow-color": "#16a34a",
        "line-style": "solid",
      },
    },
    {
      selector: 'edge[tier = "hypothesis"]',
      style: { "line-color": "#d7dce3", "target-arrow-color": "#d7dce3", "line-style": "dashed" },
    },
    {
      selector: 'edge[tier = "contested"]',
      style: { "line-color": "#dc2626", "target-arrow-color": "#dc2626", "line-style": "dashed" },
    },
    {
      selector: 'node[evidence_state = "verified"]',
      style: { "border-color": "#16a34a", "border-width": 3 },
    },
    {
      selector: 'node[evidence_state = "contested"]',
      style: { "border-color": "#dc2626", "border-width": 3 },
    },
    // 悬停高亮: 非邻域淡出
    { selector: ".faded", style: { opacity: 0.12, "text-opacity": 0.12 } },
    { selector: ".hl-node", style: { "border-color": "#111114", "border-width": 3 } },
  ] as unknown as cytoscape.StylesheetStyle[];
}

export default function GraphPanel({ sid, refreshKey }: GraphPanelProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const cyRef = useRef<cytoscape.Core | null>(null);
  const [selected, setSelected] = useState<NodeData | null>(null);
  const [showEdgeLabels, setShowEdgeLabels] = useState(true);
  const [reportOpen, setReportOpen] = useState(false);
  const [groundNote, setGroundNote] = useState<string | null>(null);
  const showEdgeLabelsRef = useRef(showEdgeLabels);
  showEdgeLabelsRef.current = showEdgeLabels;

  const { data, isLoading, isError, error, refetch, isFetching } =
    useQuery<GraphPayload>({
      queryKey: ["graph", sid],
      queryFn: () => getGraph(sid),
    });

  // 父组件改 refreshKey 时主动重新拉取 (A11 也可改用 invalidateQueries)
  useEffect(() => {
    if (refreshKey !== undefined) void refetch();
  }, [refreshKey, refetch]);

  // Phase X1: 证据接地 (增量) — 完成后重拉图 (边变色)
  const groundMut = useMutation({
    mutationFn: () => groundSession(sid),
    onSuccess: (res) => {
      const s = res.summary;
      setGroundNote(
        s.targets_total === 0
          ? "无新对象需要接地"
          : `已接地 ${s.targets_total} 个新对象: 实证 ${s.verified} / 争议 ${s.contested} / 未验证 ${s.unverified}`,
      );
      void refetch();
    },
    onError: (e) =>
      setGroundNote(`接地失败: ${e instanceof Error ? e.message : String(e)}`),
  });

  const nodes = data?.elements?.nodes ?? [];
  const edges = data?.elements?.edges ?? [];
  const hasNodes = nodes.length > 0;

  useEffect(() => {
    const container = containerRef.current;
    if (!container || !hasNodes) return;

    // 给边补中文关系标签
    const edgesWithLabels = edges.map((e) => {
      const d = (e.data ?? {}) as Record<string, unknown>;
      const rel = typeof d.relation === "string" ? d.relation : "";
      return { data: { ...d, relLabel: REL_LABELS[rel] ?? rel } };
    });

    ensureColaLayout();
    const cy = cytoscape({
      container,
      elements: {
        nodes,
        edges: edgesWithLabels as unknown as cytoscape.EdgeDefinition[],
      },
      style: buildCyStyle(showEdgeLabelsRef.current),
      layout: COLA_LAYOUT,
      wheelSensitivity: 0.2,
    });
    cyRef.current = cy;

    cy.on("tap", "node", (evt) => {
      setSelected(evt.target.data() as NodeData);
    });
    // 点击空白处关闭抽屉
    cy.on("tap", (evt) => {
      if (evt.target === cy) setSelected(null);
    });
    // 悬停高亮当前节点 + 邻域, 其余淡出
    cy.on("mouseover", "node", (evt) => {
      const node = evt.target;
      cy.elements().addClass("faded");
      node.closedNeighborhood().removeClass("faded");
      node.addClass("hl-node");
    });
    cy.on("mouseout", "node", (evt) => {
      cy.elements().removeClass("faded");
      evt.target.removeClass("hl-node");
    });

    return () => {
      cy.destroy();
      cyRef.current = null;
    };
  }, [nodes, edges, hasNodes]);

  // 切换边标签: 重应用样式 (不重新布局, 保持节点位置)。mock 无 style() → 跳过。
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy || typeof cy.style !== "function") return;
    cy.style(buildCyStyle(showEdgeLabels));
  }, [showEdgeLabels]);

  return (
    <div className="graph-panel">
      {isLoading && <div className="graph-msg">加载图谱中…</div>}
      {isError && (
        <div className="graph-msg graph-error">
          加载图谱失败：{error instanceof Error ? error.message : String(error)}
        </div>
      )}
      {!isLoading && !isError && !hasNodes && (
        <div className="graph-msg graph-empty">
          该 session 暂无图谱, 开始对话以构建
        </div>
      )}

      {hasNodes && (
        <>
          {/* 顶部工具条: 边标签开关 + 报告 / 接地 (Phase X1) */}
          <div className="graph-toolbar">
            <button
              type="button"
              className={`graph-toggle${showEdgeLabels ? " on" : ""}`}
              onClick={() => setShowEdgeLabels((v) => !v)}
              aria-pressed={showEdgeLabels}
              title="显示/隐藏关系标签"
            >
              <span className="graph-toggle-track">
                <span className="graph-toggle-knob" />
              </span>
              关系标签
            </button>
            <button
              type="button"
              className="graph-action-btn"
              onClick={() => groundMut.mutate()}
              disabled={groundMut.isPending}
              title="对新增的现象与核心因果边检索证据 (增量)"
            >
              {groundMut.isPending ? "接地中…" : "证据接地"}
            </button>
            <button
              type="button"
              className="graph-action-btn"
              onClick={() => setReportOpen(true)}
              title="把图谱生成叙事报告 (可下载 markdown)"
            >
              生成报告
            </button>
            <button
              type="button"
              className="graph-action-btn"
              onClick={() => {
                const cy = cyRef.current;
                if (!cy || typeof cy.png !== "function") return;
                const uri = cy.png({ full: true, scale: 2, bg: "#ffffff" });
                const a = document.createElement("a");
                a.href = uri;
                a.download = `graph-${sid}.png`;
                a.click();
              }}
              title="导出当前图谱为 PNG"
            >
              导出 PNG
            </button>
          </div>
          {groundNote && (
            <div className="graph-ground-note" onClick={() => setGroundNote(null)}>
              {groundNote}
            </div>
          )}

          {/* 图例 */}
          <div className="graph-legend">
            <div className="graph-legend-title">节点层级</div>
            {(["0", "1", "2"] as const).map((lv) => (
              <div key={lv} className="graph-legend-row">
                <span
                  className="graph-legend-dot"
                  style={{ background: LEVEL_COLORS[lv] }}
                />
                {LEVEL_NAMES[lv]}
              </div>
            ))}
            <div className="graph-legend-title graph-legend-sub">证据等级 (边)</div>
            <div className="graph-legend-row">
              <span className="graph-legend-line" style={{ background: "#16a34a" }} />
              实证
            </div>
            <div className="graph-legend-row">
              <span className="graph-legend-line" style={{ background: "#c4cad3" }} />
              推断
            </div>
            <div className="graph-legend-row">
              <span className="graph-legend-line graph-legend-dashed" style={{ background: "#d7dce3" }} />
              假设
            </div>
            <div className="graph-legend-row">
              <span className="graph-legend-line graph-legend-dashed" style={{ background: "#dc2626" }} />
              争议
            </div>
          </div>

          {/* 实时更新指示 */}
          {isFetching && (
            <div className="graph-updating">
              <span className="graph-updating-dot" />
              实时更新中…
            </div>
          )}
        </>
      )}

      <div
        ref={containerRef}
        className="graph-canvas"
        style={{ display: hasNodes ? "block" : "none" }}
        data-testid="graph-canvas"
      />
      <NodeDrawer node={selected} onClose={() => setSelected(null)} />
      <ReportModal sid={sid} open={reportOpen} onClose={() => setReportOpen(false)} />
    </div>
  );
}
