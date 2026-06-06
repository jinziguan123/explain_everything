export interface SessionSummary {
  sid: string;
  question: string;
  stage: string;
  created_at: number; // Unix 时间戳 (秒)
  updated_at: number; // Unix 时间戳 (秒)
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

export async function autotitleSession(sid: string): Promise<{ title: string }> {
  const r = await fetch(`/api/sessions/${sid}/autotitle`, { method: "POST" });
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
  theory_count: { stable: number; tentative: number };
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
    stability_status: string;
    supporting_session_count: number;
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
