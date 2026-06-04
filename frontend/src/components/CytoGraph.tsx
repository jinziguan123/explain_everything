import { useEffect, useRef } from "react";
import cytoscape from "cytoscape";
import dagre from "cytoscape-dagre";
import "./GraphPanel.css";

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

export interface CytoGraphProps {
  elements: { nodes: cytoscape.NodeDefinition[]; edges: cytoscape.EdgeDefinition[] };
  // dagre 等布局的额外字段不在 @types 里, 用 any 放宽
  layout: cytoscape.LayoutOptions | Record<string, unknown>;
  stylesheet?: cytoscape.StylesheetStyle[];
  onNodeTap?: (data: Record<string, unknown>) => void;
  emptyHint?: string;
}

/**
 * 通用 Cytoscape 渲染器: 从 GraphPanel 抽出的可复用核心。
 * 负责 cytoscape 初始化 (dagre 注册一次)、挂载到 ref div、应用
 * elements+layout+stylesheet、节点 tap 回调、卸载清理、空状态。
 */
export default function CytoGraph({
  elements,
  layout,
  stylesheet,
  onNodeTap,
  emptyHint = "暂无数据",
}: CytoGraphProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const cyRef = useRef<cytoscape.Core | null>(null);

  const nodes = elements?.nodes ?? [];
  const edges = elements?.edges ?? [];
  const hasNodes = nodes.length > 0;

  useEffect(() => {
    const container = containerRef.current;
    if (!container || !hasNodes) return;

    registerDagre();

    const cy = cytoscape({
      container,
      elements: { nodes, edges },
      style: stylesheet,
      layout: layout as cytoscape.LayoutOptions,
    });
    cyRef.current = cy;

    if (onNodeTap) {
      cy.on("tap", "node", (evt) => {
        onNodeTap(evt.target.data() as Record<string, unknown>);
      });
    }

    return () => {
      cy.destroy();
      cyRef.current = null;
    };
  }, [nodes, edges, hasNodes, layout, stylesheet, onNodeTap]);

  return (
    <div className="graph-panel">
      {!hasNodes && <div className="graph-msg graph-empty">{emptyHint}</div>}
      <div
        ref={containerRef}
        className="graph-canvas"
        style={{ display: hasNodes ? "block" : "none" }}
        data-testid="cyto-graph-canvas"
      />
    </div>
  );
}
