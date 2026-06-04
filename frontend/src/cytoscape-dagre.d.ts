// cytoscape-dagre 不带类型声明, 这里提供最小声明:
// 它是一个 cytoscape 扩展, 形如 (cy: typeof cytoscape) => void, 传给 cytoscape.use()
declare module "cytoscape-dagre" {
  import cytoscape from "cytoscape";
  const ext: cytoscape.Ext;
  export default ext;
}
