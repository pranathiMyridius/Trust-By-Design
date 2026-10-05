// The read-only assistant (backend: app/api/assistant.py). It answers
// questions about the assessments the signed-in user can already see and
// cannot change anything.
import { API_BASE_URL } from "./config";
import { authFetch } from "./http";

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
}

export interface ChatReference {
  id: number;
  reference_id: string | null;
  title: string | null;
}

export interface ChatReply {
  reply: string;
  tools_used: string[];
  references: ChatReference[];
}

// Carries the HTTP status so the UI can show the server's own wording for
// "not configured" (503) and "slow down" (429), which are already written
// for people.
export class AssistantError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "AssistantError";
    this.status = status;
  }
}

export async function sendChat(
  messages: ChatTurn[],
  assessmentId: number | null,
  signal?: AbortSignal
): Promise<ChatReply> {
  const response = await authFetch(`${API_BASE_URL}/api/assistant/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      messages,
      ...(assessmentId !== null ? { assessment_id: assessmentId } : {}),
    }),
    signal,
  });

  if (!response.ok) {
    let detail = "";
    try {
      const body = await response.json();
      detail = typeof body?.detail === "string" ? body.detail : "";
    } catch {
      // Not JSON -- fall through to the generic message.
    }
    throw new AssistantError(
      detail || "The assistant couldn't answer just now.",
      response.status
    );
  }

  return (await response.json()) as ChatReply;
}
