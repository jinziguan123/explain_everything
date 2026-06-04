import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import cytoscape from "cytoscape";
import dagre from "cytoscape-dagre";
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

// 防止 dagre 在热重载 / 多实例下重复注册
let dagreRegistered = false;
function registerDagre() {
  if (dagreRegistered) return;
  try {
    cytoscape.use(dagre);
  } catch {
    // 已注册时 cytoscape 会抛 warning/throw, 忽略即可
  }
  dagreRegistered = true;
}

// 按层级着色: 0 现象 / 1 模式 / 2 深层原因
const LEVEL_COLORS: Record<string, string> = {
  "0": "#4f9da6",
  "1": "#c98a3a",
  "2": "#9b5de5",
};

const cyStyle: cytoscape.StylesheetStyle[] = [
  {
    selector: "node",
    style: {
      label: "data(label)",
      "background-color": "#888",
      color: "#fff",
      "text-valign": "center",
      "text-halign": "center",
      "text-wrap": "wrap",
      "text-max-width": "120px",
      "font-size": "11px",
      shape: "round-rectangle",
      width: "label",
      height: "label",
      padding: "8px",
    },
  },
  {
    selector: 'node[level = 0]',
    style: { "background-color": LEVEL_COLORS["0"] },
  },
  {
    selector: 'node[level = 1]',
    style: { "background-color": LEVEL_COLORS["1"] },
  },
  {
    selector: 'node[level = 2]',
    style: { "background-color": LEVEL_COLORS["2"] },
  },
  {
    selector: 'node[lifecycle = "decayed"]',
    style: { opacity: 0.4 },
  },
  {
    selector: "edge",
    style: {
      width: "mapData(confidence, 0, 1, 1, 6)",
      "line-color": "#bbb",
      "target-arrow-color": "#bbb",
      "target-arrow-shape": "triangle",
      "curve-style": "bezier",
      "line-style": "solid",
    },
  },
  {
    selector: 'edge[relation = "manifests_as"]',
    style: { "line-style": "dashed" },
  },
];

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

    registerDagre();

    const cy = cytoscape({
      container,
      elements: { nodes, edges },
      style: cyStyle,
      // dagre 的 rankDir 不在 @types/cytoscape 的 BaseLayoutOptions 里, 这里断言
      layout: {
        name: "dagre",
        rankDir: "BT",
      } as unknown as cytoscape.LayoutOptions,
    });
    cyRef.current = cy;

    cy.on("tap", "node", (evt) => {
      setSelected(evt.target.data() as NodeData);
    });
    // 点击空白处关闭抽屉
    cy.on("tap", (evt) => {
      if (evt.target === cy) setSelected(null);
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
