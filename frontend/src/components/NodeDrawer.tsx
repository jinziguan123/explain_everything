import "./GraphPanel.css";

export interface EvidenceItem {
  url: string;
  title: string;
  stance: "support" | "contradict";
}

export interface NodeData {
  id: string;
  label: string;
  level: number;
  epistemic?: string;
  confidence?: number;
  description?: string;
  lifecycle?: string;
  activation?: number;
  stability?: number;
  /** Phase G 证据字段 (后端 grounding.compute_tiers) */
  tier?: string;
  evidence_state?: string;
  evidence?: EvidenceItem[];
}

const TIER_LABELS: Record<string, string> = {
  fact: "实证",
  inference: "推断",
  hypothesis: "假设",
  contested: "争议",
};

export interface NodeDrawerProps {
  node: NodeData | null;
  onClose: () => void;
}

const LEVEL_LABELS: Record<number, string> = {
  0: "现象",
  1: "模式",
  2: "深层原因",
};

function levelLabel(level: number): string {
  return LEVEL_LABELS[level] ?? `层级 ${level}`;
}

function fmtNum(v: number | undefined): string {
  if (v === undefined || v === null || Number.isNaN(v)) return "—";
  return Number.isInteger(v) ? String(v) : v.toFixed(2);
}

export default function NodeDrawer({ node, onClose }: NodeDrawerProps) {
  if (!node) return null;

  return (
    <div className="node-drawer" role="dialog" aria-label="节点详情">
      <div className="node-drawer-header">
        <span className="node-drawer-title">{node.label}</span>
        <button
          className="node-drawer-close"
          onClick={onClose}
          aria-label="关闭"
        >
          ✕
        </button>
      </div>
      <dl className="node-drawer-body">
        <dt>层级</dt>
        <dd>{levelLabel(node.level)}</dd>
        <dt>认知等级</dt>
        <dd>
          {node.tier ? (
            <span className={`tier-badge tier-${node.tier}`}>
              {TIER_LABELS[node.tier] ?? node.tier}
            </span>
          ) : (
            node.epistemic || "—"
          )}
        </dd>
        <dt>置信度</dt>
        <dd>{fmtNum(node.confidence)}</dd>
        <dt>描述</dt>
        <dd>{node.description || "—"}</dd>
        <dt>激活度</dt>
        <dd>{fmtNum(node.activation)}</dd>
        <dt>稳定度</dt>
        <dd>{fmtNum(node.stability)}</dd>
      </dl>
      {node.evidence && node.evidence.length > 0 && (
        <div className="node-drawer-evidence">
          <div className="node-drawer-evidence-title">
            证据来源 ({node.evidence.length})
          </div>
          <ul>
            {node.evidence.map((ev, i) => (
              <li key={i} className={`evidence-${ev.stance}`}>
                <span className="evidence-stance">
                  {ev.stance === "support" ? "支持" : "反驳"}
                </span>
                <a href={ev.url} target="_blank" rel="noreferrer">
                  {ev.title || ev.url}
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
