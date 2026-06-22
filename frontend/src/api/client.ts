export interface SessionSummary {
  sid: string;
  question: string;
  stage: string;
  created_at: number; // Unix 时间戳 (秒)
  updated_at: number; // Unix 时间戳 (秒)
  last_user_message_at: number; // 最近用户提问时间 (秒); 0=尚未提问
}

export async function listSessions(): Promise<SessionSummary[]> {
  const r = await fetch("/api/sessions");
  if (!r.ok) throw new Error(`listSessions ${r.status}`);
  return r.json();
}

export async function getGraph(sid: string) {
  const r = await fetch(`/api/sessions/${sid}/graph`);
  if (!r.ok) throw new Error(`getGraph ${r.status}`);
  return r.json();
}

export interface TranscriptEntry {
  role: "user" | "assistant";
  content: unknown; // string | content-block[]
  turn?: number;
}

export async function getTranscript(sid: string): Promise<TranscriptEntry[]> {
  const r = await fetch(`/api/sessions/${sid}/transcript`);
  if (!r.ok) throw new Error(`getTranscript ${r.status}`);
  return r.json();
}

export async function autotitleSession(
  sid: string,
  message?: string,
): Promise<{ title: string }> {
  // message: 首发并行场景下把首条消息直接传给后端, 免去读尚未落盘的 transcript。
  const r = await fetch(`/api/sessions/${sid}/autotitle`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message: message ?? null }),
  });
  if (!r.ok) throw new Error(`autotitleSession ${r.status}`);
  return r.json();
}

export async function deleteSession(sid: string): Promise<void> {
  const r = await fetch(`/api/sessions/${sid}`, { method: "DELETE" });
  if (!r.ok) throw new Error(`deleteSession ${r.status}`);
}

export async function createSession(question: string): Promise<{ sid: string }> {
  const r = await fetch("/api/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
  if (!r.ok) throw new Error(`createSession ${r.status}`);
  return r.json();
}

export interface KnowledgeOverview {
  session_count: number;
  variable_count: number;
  theory_count: { stable: number; tentative: number; weakened?: number };
  /** Phase T: H3 复用率持续测量 (复用 ≥2 次变量占比) */
  h3_reuse?: { vars_reused: number; reuse_rate: number };
  /** Phase X3: per-session 复用率真埋点 (flush 时记录), 最近 10 条 */
  h3_sessions?: {
    sid: string;
    ts: string;
    flushed: number;
    reused: number;
    created: number;
    reuse_rate: number;
  }[];
  top_variables: {
    global_id: string;
    name: string;
    reuse_count: number;
    abstraction_level: number;
  }[];
  theories: {
    id: string;
    summary: string;
    motif_type: string;
    predictive_power: number;
    /** "ledger" = 预测台账命中率, "retrodiction" = 回溯估计 */
    predictive_power_source?: string;
    stability_status: string;
    supporting_session_count: number;
    predictions?: { total: number; pending: number; hits: number; misses: number };
  }[];
}

export async function getKnowledgeOverview(): Promise<KnowledgeOverview> {
  const r = await fetch("/api/knowledge/overview");
  if (!r.ok) throw new Error(`getKnowledgeOverview ${r.status}`);
  return r.json();
}

export async function getKnowledgeGraph() {
  const r = await fetch("/api/knowledge/graph");
  if (!r.ok) throw new Error(`getKnowledgeGraph ${r.status}`);
  return r.json();
}

export async function rejectTheory(id: string): Promise<{ rejected: boolean }> {
  const r = await fetch(`/api/theories/${id}/reject`, { method: "POST" });
  if (!r.ok) throw new Error(`rejectTheory ${r.status}`);
  return r.json();
}

// ── Phase X1: 报告 / 接地 / 预测台账 ──────────────────────────

export async function generateReport(sid: string, topic?: string): Promise<{ markdown: string }> {
  const r = await fetch(`/api/sessions/${sid}/report`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ topic: topic || null }),
  });
  if (!r.ok) throw new Error(`generateReport ${r.status}`);
  return r.json();
}

export interface GroundResult {
  summary: {
    targets_total: number;
    verified: number;
    contested: number;
    unverified: number;
    evidence_added: number;
    edges_reweighted: number;
    errors: string[];
  };
  tier_distribution: Record<string, number>;
}

export async function groundSession(sid: string, incremental = true): Promise<GroundResult> {
  const r = await fetch(`/api/sessions/${sid}/ground`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ incremental }),
  });
  if (!r.ok) throw new Error(`groundSession ${r.status}`);
  return r.json();
}

export interface PredictionItem {
  id: string;
  theory_id: string;
  assertion: string;
  method: "retrodiction" | "search" | "time_window";
  deadline: string | null;
  status: "pending" | "hit" | "miss";
  origin: "user" | "llm";
  note: string | null;
  due: boolean;
}

export async function listPredictions(theory?: string): Promise<PredictionItem[]> {
  const qs = theory ? `?theory=${encodeURIComponent(theory)}` : "";
  const r = await fetch(`/api/predictions${qs}`);
  if (!r.ok) throw new Error(`listPredictions ${r.status}`);
  return r.json();
}

export async function addPrediction(body: {
  theory_id: string;
  assertion: string;
  method?: string;
  deadline?: string | null;
}): Promise<PredictionItem> {
  const r = await fetch("/api/predictions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`addPrediction ${r.status}`);
  return r.json();
}

export async function resolvePrediction(
  id: string,
  hit: boolean,
  note?: string,
): Promise<{
  prediction: PredictionItem;
  theory_stats: { weakened: boolean; predictive_power: number | null };
}> {
  const r = await fetch(`/api/predictions/${id}/resolve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ hit, note: note ?? null }),
  });
  if (!r.ok) throw new Error(`resolvePrediction ${r.status}`);
  return r.json();
}

export async function draftPredictions(
  theoryId?: string,
): Promise<PredictionItem[]> {
  const r = await fetch("/api/predictions/draft", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ theory_id: theoryId ?? null }),
  });
  if (!r.ok) throw new Error(`draftPredictions ${r.status}`);
  return r.json();
}
