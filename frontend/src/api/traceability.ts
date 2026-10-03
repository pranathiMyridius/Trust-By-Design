import { API_BASE_URL } from "./config";
import { authFetch } from "./http";
import type { DocumentWarning, RiskFactor } from "./assessments";

// P4: evidence traceability -- expired-evidence decisions (R2.6), intake
// history (R3.4), analyst indicator edits and the fixed Stage 4 rules.

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await authFetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", Accept: "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    let detail = "";
    try {
      const body = await response.json();
      detail =
        typeof body?.detail === "string"
          ? body.detail
          : typeof body?.detail?.message === "string"
          ? body.detail.message
          : Array.isArray(body?.detail)
          ? body.detail.map((item: { msg?: string }) => item.msg).filter(Boolean).join(" ")
          : "";
    } catch {
      // not JSON
    }
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return response.json();
}

export type EvidenceDecision = "USE_AS_EVIDENCE" | "EXCLUDE_FROM_EVIDENCE";

export function acknowledgeExpiredDocument(
  assessmentId: number,
  documentId: number,
  decision: EvidenceDecision,
  reason: string
): Promise<DocumentWarning> {
  return request(`/api/assessments/${assessmentId}/documents/${documentId}/expiry-acknowledgement`, {
    method: "POST",
    body: JSON.stringify({ decision, reason }),
  });
}

export interface IntakeChange {
  field: string;
  old: unknown;
  new: unknown;
}

export interface IntakeVersion {
  id: number;
  record_type: "ASSESSMENT_REQUEST" | "BUSINESS_PROFILE";
  version: number;
  trigger: string;
  snapshot: Record<string, unknown>;
  changes: IntakeChange[];
  reason: string | null;
  was_validated: boolean;
  changed_by: string;
  changed_by_id: number | null;
  created_at: string;
}

export function getIntakeHistory(assessmentId: number): Promise<{ assessment_id: number; versions: IntakeVersion[] }> {
  return request(`/api/assessments/${assessmentId}/intake-history`);
}

export function updateFactorIndicators(
  assessmentId: number,
  riskFactorId: number,
  indicators: string[],
  reason: string
): Promise<RiskFactor> {
  return request(`/api/assessments/${assessmentId}/risk-factors/${riskFactorId}/indicators`, {
    method: "PATCH",
    body: JSON.stringify({ indicators, reason }),
  });
}
