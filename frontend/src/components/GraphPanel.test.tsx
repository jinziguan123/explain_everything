import type { ReactNode } from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import GraphPanel from "./GraphPanel";
import { getGraph } from "../api/client";

// 捕获每个 cytoscape() 实例 + 注册的 tap 处理器
interface FakeCy {
  opts: unknown;
  handlers: { sel: string | null; fn: (evt: unknown) => void }[];
  destroyed: boolean;
}
const instances: FakeCy[] = [];

const cyFactory = vi.fn((opts: unknown) => {
  const inst: FakeCy = { opts, handlers: [], destroyed: false };
  instances.push(inst);
  const api = {
    on: (_evt: string, b: unknown, c?: unknown) => {
      if (typeof b === "function") {
        // cy.on("tap", fn) — 背景点击
        inst.handlers.push({ sel: null, fn: b as (e: unknown) => void });
      } else {
        // cy.on("tap", "node", fn) — 选择器形式
        inst.handlers.push({
          sel: b as string,
          fn: c as (e: unknown) => void,
        });
      }
    },
    destroy: () => {
      inst.destroyed = true;
    },
  };
  return api;
});

vi.mock("cytoscape", () => {
  const fn = (opts: unknown) => cyFactory(opts);
  (fn as unknown as { use: unknown }).use = vi.fn();
  return { default: fn };
});
vi.mock("cytoscape-dagre", () => ({ default: {} }));

vi.mock("../api/client", () => ({
  getGraph: vi.fn(),
}));

const mockGetGraph = vi.mocked(getGraph);

const NODE = {
  id: "c_001",
  label: "不确定性",
  level: 1,
  epistemic: "insight",
  confidence: 0.8,
  description: "一段描述",
  lifecycle: "active",
  activation: 1,
  stability: 0,
};

function graphWithNode() {
  return {
    root_question: "q",
    elements: { nodes: [{ data: { ...NODE } }], edges: [] },
  };
}

function emptyGraph() {
  return { root_question: "q", elements: { nodes: [], edges: [] } };
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
  mockGetGraph.mockReset();
});

describe("GraphPanel", () => {
  it("拉取图谱并把 elements 传给 cytoscape", async () => {
    mockGetGraph.mockResolvedValue(graphWithNode());
    render(wrap(<GraphPanel sid="s_test" />));

    await waitFor(() => expect(cyFactory).toHaveBeenCalled());
    expect(mockGetGraph).toHaveBeenCalledWith("s_test");

    const opts = instances[0].opts as {
      elements: { nodes: { data: { id: string } }[] };
      layout: { name: string };
    };
    expect(opts.elements.nodes[0].data.id).toBe("c_001");
    // Obsidian 风格力导向布局
    expect(opts.layout).toMatchObject({ name: "cose" });
  });

  it("空图谱显示友好空状态且不初始化 cytoscape", async () => {
    mockGetGraph.mockResolvedValue(emptyGraph());
    render(wrap(<GraphPanel sid="s_empty" />));

    await waitFor(() =>
      expect(
        screen.getByText(/暂无图谱, 开始对话以构建/),
      ).toBeInTheDocument(),
    );
    expect(cyFactory).not.toHaveBeenCalled();
  });

  it("点击节点打开抽屉, 显示中文层级与字段", async () => {
    mockGetGraph.mockResolvedValue(graphWithNode());
    render(wrap(<GraphPanel sid="s_test" />));

    await waitFor(() => expect(cyFactory).toHaveBeenCalled());

    // 找到 node tap 处理器并触发
    const nodeTap = instances[0].handlers.find((h) => h.sel === "node");
    expect(nodeTap).toBeTruthy();
    nodeTap!.fn({ target: { data: () => ({ ...NODE }) } });

    await waitFor(() =>
      expect(screen.getByRole("dialog")).toBeInTheDocument(),
    );
    // 限定在抽屉内断言 (图例里也有"模式"字样, 全局 getByText 会撞)
    const drawer = within(screen.getByRole("dialog"));
    expect(drawer.getByText("模式")).toBeInTheDocument(); // level 1 → 模式
    expect(drawer.getByText("insight")).toBeInTheDocument();
    expect(drawer.getByText("一段描述")).toBeInTheDocument();
    expect(drawer.getByText("0.80")).toBeInTheDocument(); // confidence

    // 关闭抽屉
    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
  });

  it("卸载时销毁 cytoscape 实例", async () => {
    mockGetGraph.mockResolvedValue(graphWithNode());
    const { unmount } = render(wrap(<GraphPanel sid="s_test" />));

    await waitFor(() => expect(cyFactory).toHaveBeenCalled());
    expect(instances[0].destroyed).toBe(false);

    unmount();
    expect(instances[0].destroyed).toBe(true);
  });
});
