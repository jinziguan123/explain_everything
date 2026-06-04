import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import cytoscape from "cytoscape";
import {
  getKnowledgeGraph,
  getKnowledgeOverview,
  rejectTheory,
} from "../api/client";
import type { KnowledgeOverview } from "../api/client";
import KnowledgeDashboard from "../components/KnowledgeDashboard";
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

// 跨 session 知识图样式 (浅色单色): 节点大小随 reuse, 统一深灰, in_theory 高亮
const cyStyle: cytoscape.StylesheetStyle[] = [
  {
    selector: "node",
    style: {
      label: "data(label)",
      "background-color": "#6b6b72",
      color: "#3a3a3f",
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
    },
  },
  {
    // [?in_theory] = 真值选择器 (存在性 [in_theory] 会把 false 也选中 → 全高亮)
    selector: "node[?in_theory]",
    style: {
      "background-color": "#111114",
      "border-width": 3,
      "border-color": "#111114",
    },
  },
  {
    selector: "edge",
    style: {
      width: 1.5,
      "line-color": "#d4d4d8",
      "target-arrow-color": "#d4d4d8",
      "target-arrow-shape": "triangle",
      "curve-style": "bezier",
    },
  },
  {
    selector: 'edge[relation = "manifests_as"]',
    style: { "line-style": "dashed" },
  },
];

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

  const nodes = graphQuery.data?.elements?.nodes ?? [];
  const edges = graphQuery.data?.elements?.edges ?? [];

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

      <section className="knowledge-graph-section">
        <h3 className="knowledge-graph-title">跨 session 知识图</h3>
        <div className="knowledge-graph-wrap">
          <CytoGraph
            elements={{ nodes, edges }}
            layout={{ name: "cose" }}
            stylesheet={cyStyle}
            onNodeTap={(data) => setSelected(data as unknown as NodeData)}
            emptyHint="暂无跨 session 知识, 多进行几次探索后这里会出现复用的变量与理论"
          />
          <NodeDrawer node={selected} onClose={() => setSelected(null)} />
        </div>
      </section>
    </div>
  );
}
