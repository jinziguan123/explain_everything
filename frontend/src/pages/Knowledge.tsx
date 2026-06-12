import { useCallback, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import cytoscape from "cytoscape";
import {
  getKnowledgeGraph,
  getKnowledgeOverview,
  rejectTheory,
} from "../api/client";
import type { KnowledgeOverview } from "../api/client";
import { COLA_LAYOUT } from "../lib/cyLayout";
import KnowledgeDashboard from "../components/KnowledgeDashboard";
import PredictionsPanel from "../components/PredictionsPanel";
import CytoGraph from "../components/CytoGraph";
import NodeDrawer from "../components/NodeDrawer";
import type { NodeData } from "../components/NodeDrawer";
import "./Knowledge.css";

interface KnowledgeGraphPayload {
  elements?: {
    nodes?: cytoscape.NodeDefinition[];
    edges?: cytoscape.EdgeDefinition[];
  };
}

// 主题调色板 (类 MiroFish): 按 theme_index 循环取色; 无主题 → 中性灰
const THEME_PALETTE = [
  "#2f6df6", "#f5921e", "#16a34a", "#9b5de5",
  "#e11d48", "#0891b2", "#db2777", "#ca8a04",
];
function colorForTheme(themeIndex: unknown): string {
  return typeof themeIndex === "number" && themeIndex >= 0
    ? THEME_PALETTE[themeIndex % THEME_PALETTE.length]
    : "#9aa0a6";
}
const REL_LABELS: Record<string, string> = {
  causes: "导致",
  manifests_as: "表现为",
  amplifies: "增强",
  suppresses: "抑制",
  constrains: "约束",
};

// 跨 session 知识图样式 (彩色, 类 MiroFish): 大小随 reuse, 按主题着色, 边带标签
const cyStyle = [
  {
    selector: "node",
    style: {
      label: "data(label)",
      "background-color": "data(nodeColor)",
      "border-width": 2,
      "border-color": "#ffffff",
      color: "#1f2937",
      "text-valign": "bottom",
      "text-halign": "center",
      "text-margin-y": 4,
      "text-wrap": "wrap",
      "text-max-width": "120px",
      "text-outline-color": "#ffffff",
      "text-outline-width": 2,
      "font-size": "11px",
      shape: "ellipse",
      width: "mapData(reuse, 0, 10, 24, 72)",
      height: "mapData(reuse, 0, 10, 24, 72)",
      "transition-property": "background-color, opacity",
      "transition-duration": "220ms",
    },
  },
  {
    // [?in_theory] = 真值选择器 (存在性 [in_theory] 会把 false 也选中 → 全高亮)
    selector: "node[?in_theory]",
    style: { "border-color": "#111114", "border-width": 3 },
  },
  {
    selector: "edge",
    style: {
      width: "mapData(confidence, 0, 1, 1, 3.5)",
      "line-color": "#c4cad3",
      "target-arrow-color": "#c4cad3",
      "target-arrow-shape": "triangle",
      "arrow-scale": 0.8,
      "curve-style": "bezier",
      label: "data(relLabel)",
      "font-size": "8px",
      color: "#8a93a0",
      "text-rotation": "autorotate",
      "text-background-color": "#ffffff",
      "text-background-opacity": 0.85,
      "text-background-padding": 2,
    },
  },
  { selector: 'edge[relation = "manifests_as"]', style: { "line-style": "dashed" } },
  { selector: ".faded", style: { opacity: 0.12, "text-opacity": 0.12 } },
  { selector: ".hl-node", style: { "border-color": "#111114", "border-width": 3 } },
] as unknown as cytoscape.StylesheetStyle[];

export default function Knowledge() {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<NodeData | null>(null);

  const overviewQuery = useQuery<KnowledgeOverview>({
    queryKey: ["knowledge", "overview"],
    queryFn: getKnowledgeOverview,
  });
  const graphQuery = useQuery<KnowledgeGraphPayload>({
    queryKey: ["knowledge", "graph"],
    queryFn: getKnowledgeGraph,
  });

  const rejectMutation = useMutation({
    mutationFn: rejectTheory,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["knowledge"] });
    },
  });

  // 稳定引用 (避免每次渲染让 CytoGraph 重建 → cola 持续布局被反复重启)
  const nodes = useMemo(() => {
    const raw = graphQuery.data?.elements?.nodes ?? [];
    return raw.map((n) => {
      const d = (n.data ?? {}) as Record<string, unknown>;
      return { data: { ...d, nodeColor: colorForTheme(d.theme_index) } };
    }) as unknown as cytoscape.NodeDefinition[];
  }, [graphQuery.data]);
  const edges = useMemo(() => {
    const raw = graphQuery.data?.elements?.edges ?? [];
    return raw.map((e) => {
      const d = (e.data ?? {}) as Record<string, unknown>;
      const rel = typeof d.relation === "string" ? d.relation : "";
      return { data: { ...d, relLabel: REL_LABELS[rel] ?? rel } };
    }) as unknown as cytoscape.EdgeDefinition[];
  }, [graphQuery.data]);
  const elements = useMemo(() => ({ nodes, edges }), [nodes, edges]);
  const onNodeTap = useCallback(
    (data: Record<string, unknown>) => setSelected(data as unknown as NodeData),
    [],
  );

  return (
    <div className="knowledge-page">
      {overviewQuery.isLoading && (
        <div className="knowledge-msg">加载知识总览中…</div>
      )}
      {overviewQuery.isError && (
        <div className="knowledge-msg knowledge-error">
          加载知识总览失败：
          {overviewQuery.error instanceof Error
            ? overviewQuery.error.message
            : String(overviewQuery.error)}
        </div>
      )}
      {overviewQuery.data && (
        <KnowledgeDashboard
          overview={overviewQuery.data}
          onRejectTheory={(id) => rejectMutation.mutate(id)}
        />
      )}

      <PredictionsPanel />

      <section className="knowledge-graph-section">
        <h3 className="knowledge-graph-title">跨 session 知识图</h3>
        <div className="knowledge-graph-wrap">
          <CytoGraph
            elements={elements}
            layout={COLA_LAYOUT}
            stylesheet={cyStyle}
            onNodeTap={onNodeTap}
            emptyHint="暂无跨 session 知识, 多进行几次探索后这里会出现复用的变量与理论"
          />
          {nodes.length > 0 && (
            <div className="graph-legend">
              <div className="graph-legend-title">图例</div>
              <div className="graph-legend-row">
                <span
                  className="graph-legend-dot"
                  style={{ background: "#2f6df6" }}
                />
                颜色 = 所属主题
              </div>
              <div className="graph-legend-row">
                <span
                  className="graph-legend-dot"
                  style={{ width: 14, height: 14, background: "#9aa0a6" }}
                />
                大小 = 复用次数
              </div>
              <div className="graph-legend-row">
                <span
                  className="graph-legend-dot"
                  style={{
                    background: "#9aa0a6",
                    border: "2px solid #111114",
                  }}
                />
                描边 = 参与理论
              </div>
            </div>
          )}
          <NodeDrawer node={selected} onClose={() => setSelected(null)} />
        </div>
      </section>
    </div>
  );
}
