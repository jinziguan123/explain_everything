export interface SessionSummary {
  sid: string;
  question: string;
  stage: string;
  created_at: string;
  updated_at: string;
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

export async function createSession(question: string): Promise<{ sid: string }> {
  const r = await fetch("/api/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
  if (!r.ok) throw new Error(`createSession ${r.status}`);
  return r.json();
}
