import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import cytoscape from "cytoscape";
import { getGraph } from "../api/client";
import NodeDrawer from "./NodeDrawer";
import type { NodeData } from "./NodeDrawer";
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

// 按层级灰度: 0 现象 (浅) / 1 模式 (中) / 2 深层原因 (黑)
const LEVEL_COLORS: Record<string, string> = {
  "0": "#c4c4c8",
  "1": "#8a8a90",
  "2": "#111114",
};

// 浅色单色风格: 力导向(cose) + 圆形节点 + 标签在下 + 悬停高亮邻居
const cyStyle: cytoscape.StylesheetStyle[] = [
  {
    selector: "node",
    style: {
      label: "data(label)",
      shape: "ellipse",
      width: 22,
      height: 22,
      "background-color": "#8a8a90",
      "border-width": 0,
      // 标签在节点下方
      color: "#3a3a3f",
      "font-size": "10px",
      "text-valign": "bottom",
      "text-halign": "center",
      "text-margin-y": 4,
      "text-wrap": "wrap",
      "text-max-width": "120px",
      "text-outline-color": "#ffffff",
      "text-outline-width": 2,
    },
  },
  { selector: "node[level = 0]", style: { "background-color": LEVEL_COLORS["0"] } },
  { selector: "node[level = 1]", style: { "background-color": LEVEL_COLORS["1"], width: 28, height: 28 } },
  { selector: "node[level = 2]", style: { "background-color": LEVEL_COLORS["2"], width: 34, height: 34 } },
  { selector: 'node[lifecycle = "decayed"]', style: { opacity: 0.35 } },
  {
    selector: "edge",
    style: {
      width: "mapData(confidence, 0, 1, 1, 4)",
      "line-color": "#d4d4d8",
      "target-arrow-color": "#d4d4d8",
      "target-arrow-shape": "triangle",
      "arrow-scale": 0.8,
      "curve-style": "bezier",
      "line-style": "solid",
      opacity: 0.9,
    },
  },
  { selector: 'edge[relation = "manifests_as"]', style: { "line-style": "dashed" } },
  // 悬停高亮: 非邻域淡出
  { selector: ".faded", style: { opacity: 0.12, "text-opacity": 0.12 } },
  {
    selector: ".hl-node",
    style: { "border-width": 3, "border-color": "#111114" },
  },
];

const COSE_LAYOUT = {
  name: "cose",
  animate: false,
  padding: 30,
  idealEdgeLength: 90,
  nodeRepulsion: 9000,
  nodeOverlap: 16,
  gravity: 0.3,
  randomize: false,
} as unknown as cytoscape.LayoutOptions;

export default function GraphPanel({ sid, refreshKey }: GraphPanelProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const cyRef = useRef<cytoscape.Core | null>(null);
  const [selected, setSelected] = useState<NodeData | null>(null);

  const { data, isLoading, isError, error, refetch } = useQuery<GraphPayload>({
    queryKey: ["graph", sid],
    queryFn: () => getGraph(sid),
  });

  // 父组件改 refreshKey 时主动重新拉取 (A11 也可改用 invalidateQueries)
  useEffect(() => {
    if (refreshKey !== undefined) void refetch();
  }, [refreshKey, refetch]);

  const nodes = data?.elements?.nodes ?? [];
  const edges = data?.elements?.edges ?? [];
  const hasNodes = nodes.length > 0;

  useEffect(() => {
    const container = containerRef.current;
    if (!container || !hasNodes) return;

    const cy = cytoscape({
      container,
      elements: { nodes, edges },
      style: cyStyle,
      layout: COSE_LAYOUT,
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
    // Obsidian 风格悬停: 高亮当前节点 + 邻域, 其余淡出
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
      <div
        ref={containerRef}
        className="graph-canvas"
        style={{ display: hasNodes ? "block" : "none" }}
        data-testid="graph-canvas"
      />
      <NodeDrawer node={selected} onClose={() => setSelected(null)} />
    </div>
  );
}
