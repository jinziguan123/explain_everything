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

// 跨 session 知识图样式: 节点大小随 reuse, 颜色随 theme, in_theory 高亮
const cyStyle: cytoscape.StylesheetStyle[] = [
  {
    selector: "node",
    style: {
      label: "data(label)",
      "background-color": "mapData(theme, 0, 9, #4f9da6, #9b5de5)",
      color: "#fff",
      "text-valign": "center",
      "text-halign": "center",
      "text-wrap": "wrap",
      "text-max-width": "120px",
      "font-size": "11px",
      shape: "ellipse",
      width: "mapData(reuse, 0, 10, 24, 72)",
      height: "mapData(reuse, 0, 10, 24, 72)",
    },
  },
  {
    selector: "node[in_theory]",
    style: {
      "border-width": 3,
      "border-color": "#ffd166",
    },
  },
  {
    selector: "edge",
    style: {
      width: 1.5,
      "line-color": "#bbb",
      "target-arrow-color": "#bbb",
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
