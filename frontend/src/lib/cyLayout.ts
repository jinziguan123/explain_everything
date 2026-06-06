import cytoscape from "cytoscape";
import cola from "cytoscape-cola";

let registered = false;

/** 注册 cola 布局 (幂等)。需在 cytoscape() 前调用。 */
export function ensureColaLayout(): void {
  if (registered) return;
  try {
    cytoscape.use(cola);
  } catch {
    /* 已注册 / 重复注册, 忽略 */
  }
  registered = true;
}

/**
 * 持续力导向布局 (cola, infinite)。
 * infinite:true → 模拟不停止, 拖动某节点时其相连节点被牵引一起动 (类 MiroFish / d3-force)。
 * fit:false → 不在每帧重新缩放视图。
 */
export const COLA_LAYOUT = {
  name: "cola",
  infinite: true,
  fit: false,
  animate: true,
  edgeLength: 120,
  nodeSpacing: 16,
  randomize: false,
  handleDisconnected: true,
  centerGraph: false,
} as unknown as cytoscape.LayoutOptions;
