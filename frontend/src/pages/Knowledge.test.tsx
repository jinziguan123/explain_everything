import type { ReactNode } from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Knowledge from "./Knowledge";
import {
  getKnowledgeOverview,
  getKnowledgeGraph,
  rejectTheory,
} from "../api/client";

// 捕获 cytoscape 实例 (jsdom 无 canvas, 必须 mock)
interface FakeCy {
  opts: unknown;
  destroyed: boolean;
}
const instances: FakeCy[] = [];
const cyFactory = vi.fn((opts: unknown) => {
  const inst: FakeCy = { opts, destroyed: false };
  instances.push(inst);
  return {
    on: () => {},
    destroy: () => {
      inst.destroyed = true;
    },
  };
});

vi.mock("cytoscape", () => {
  const fn = (opts: unknown) => cyFactory(opts);
  (fn as unknown as { use: unknown }).use = vi.fn();
  return { default: fn };
});
vi.mock("cytoscape-dagre", () => ({ default: {} }));

vi.mock("../api/client", () => ({
  getKnowledgeOverview: vi.fn(),
  getKnowledgeGraph: vi.fn(),
  rejectTheory: vi.fn(),
}));

const mockOverview = vi.mocked(getKnowledgeOverview);
const mockGraph = vi.mocked(getKnowledgeGraph);
const mockReject = vi.mocked(rejectTheory);

function overviewData() {
  return {
    session_count: 3,
    variable_count: 10,
    theory_count: { stable: 1, tentative: 2 },
    top_variables: [
      { global_id: "v_1", name: "拖延", reuse_count: 4, abstraction_level: 0 },
    ],
    theories: [
      {
        id: "t_9",
        summary: "理论摘要",
        motif_type: "loop",
        predictive_power: 0.5,
        stability_status: "tentative",
        supporting_session_count: 2,
      },
    ],
  };
}

function graphData() {
  return {
    elements: {
      nodes: [{ data: { id: "n1", label: "拖延", reuse: 4, theme: 1 } }],
      edges: [],
    },
  };
}

function wrap(ui: ReactNode) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>;
}

beforeEach(() => {
  instances.length = 0;
  cyFactory.mockClear();
  mockOverview.mockReset();
  mockGraph.mockReset();
  mockReject.mockReset();
});

describe("Knowledge page", () => {
  it("渲染仪表盘并用 cose 布局初始化跨 session 图", async () => {
    mockOverview.mockResolvedValue(overviewData());
    mockGraph.mockResolvedValue(graphData());
    render(wrap(<Knowledge />));

    await waitFor(() => expect(screen.getByText("拖延")).toBeInTheDocument());
    await waitFor(() => expect(cyFactory).toHaveBeenCalled());

    const opts = instances[0].opts as {
      layout: { name: string };
      elements: { nodes: { data: { id: string } }[] };
    };
    expect(opts.layout).toMatchObject({ name: "cose" });
    expect(opts.elements.nodes[0].data.id).toBe("n1");
  });

  it("点击拒绝调用 rejectTheory", async () => {
    mockOverview.mockResolvedValue(overviewData());
    mockGraph.mockResolvedValue(graphData());
    mockReject.mockResolvedValue({ rejected: true });
    render(wrap(<Knowledge />));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "拒绝" })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: "拒绝" }));
    await waitFor(() => expect(mockReject).toHaveBeenCalled());
    expect(mockReject.mock.calls[0][0]).toBe("t_9");
  });
});
