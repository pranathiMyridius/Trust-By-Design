// Methodology versioning, policy rules, the residual grid and reference-data
// snapshots. See backend/app/api/risk_methodology.py and
// backend/app/api/reference_data.py.
import { formatErrorDetail } from "./assessments";
import { authFetch } from "./http";
import { API_BASE_URL } from "./config";


async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await authFetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", Accept: "application/json", ...(init?.headers ?? {}) },
  });

  if (!response.ok) {
    let detail = "";
    try {
      const body = await response.json();
      detail = body?.detail ? formatErrorDetail(body.detail) : "";
    } catch {
      // not JSON
    }
    const error = new Error(`Request failed (${response.status}): ${detail}`);
    (error as Error & { status?: number }).status = response.status;
    throw error;
  }

  return response.json();
}

// ---------------------------------------------------------------------------
// Methodologies
// ---------------------------------------------------------------------------

export interface Methodology {
  id: number;
  name: string;
  is_active: boolean;
  version: number;
  parent_id: number | null;
  change_reason: string | null;
  locked: boolean;
  locked_at: string | null;
  approved_by: string | null;
  approved_at: string | null;
  approval_reason: string | null;
  effective_from: string | null;
  retired_at: string | null;
  fingerprint: string | null;
  created_at: string;
}

export interface PolicyRule {
  rule_code: string;
  rule_type: "override" | "minimum_band";
  status: "approved" | "draft" | "disabled";
  version: string;
  description: string | null;
  condition: {
    indicator?: string;
    categories?: string[];
    min_factor_band?: string;
    min_factor_score?: number;
    jurisdiction_tiers?: string[];
  };
  min_band: string;
  mandatory_review: boolean;
  non_mitigable: boolean;
}

export interface ResidualGrid {
  version: string;
  cells: Record<string, Record<string, string>>;
}

export interface MethodologyConfig {
  methodology_id: number | null;
  methodology_name: string;
  methodology_version: string;
  methodology_fingerprint: string;
  risk_bands: { name: string; min: number; max: number }[];
  escalation_rules: PolicyRule[];
  residual_grid: ResidualGrid;
  mitigant_categories: string[];
  factor_weights: Record<string, number>;
  // R6.1
  likelihood_scale?: ScaleStep[];
  impact_scale?: ScaleStep[];
  required_approvals?: Record<string, string[]>;
}

export interface ScaleStep {
  value: number;
  label: string;
}

// R6.1: factor weights, scales, bands and required approvals (admin, reason required).
export function saveScoringConfig(
  id: number,
  payload: {
    factor_weights?: Record<string, number>;
    likelihood_scale?: ScaleStep[];
    impact_scale?: ScaleStep[];
    risk_bands?: { name: string; min: number; max: number }[];
    required_approvals?: Record<string, string[]>;
    reason: string;
  }
) {
  return request(`/api/risk-methodologies/${id}/scoring-config`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function listMethodologies(): Promise<Methodology[]> {
  return request("/api/risk-methodologies");
}

export function getMethodologyConfig(id: number): Promise<Methodology & { config: MethodologyConfig }> {
  return request(`/api/risk-methodologies/${id}/config`);
}

export function getActiveRules(): Promise<{
  methodology_id: number | null;
  methodology_name: string;
  rules: PolicyRule[];
}> {
  return request("/api/risk-methodologies/active/escalation-rules");
}

/** An editable methodology row starting from the built-in defaults. */
export function createFromDefaults(name: string): Promise<Methodology> {
  return request("/api/risk-methodologies", {
    method: "POST",
    body: JSON.stringify({
      name,
      weights: { ALL: 1.0 },
      thresholds: { CRITICAL: 80, HIGH: 60, MEDIUM: 40 },
      is_active: false,
    }),
  });
}

export function cloneMethodology(id: number, reason: string, name?: string): Promise<Methodology> {
  return request(`/api/risk-methodologies/${id}/clone`, {
    method: "POST",
    body: JSON.stringify({ reason, name: name || null }),
  });
}

export function activateMethodology(id: number, reason: string): Promise<Methodology> {
  return request(`/api/risk-methodologies/${id}/activate`, {
    method: "PATCH",
    body: JSON.stringify({ reason }),
  });
}

export function saveRules(id: number, rules: PolicyRule[], reason: string) {
  return request(`/api/risk-methodologies/${id}/escalation-rules`, {
    method: "PUT",
    body: JSON.stringify({ rules, reason }),
  });
}

export function saveResidualGrid(id: number, residualGrid: ResidualGrid, reason: string) {
  return request(`/api/risk-methodologies/${id}/residual-grid`, {
    method: "PUT",
    body: JSON.stringify({ residual_grid: residualGrid, reason }),
  });
}

// ---------------------------------------------------------------------------
// Reference data
// ---------------------------------------------------------------------------

export interface ReferenceSnapshot {
  id: number;
  source: string;
  as_of: string;
  source_url: string | null;
  statement: string | null;
  checksum: string;
  entry_count: number;
  source_claims_verified: boolean;
  source_verification_note: string | null;
  attested: boolean;
  attested_by: string | null;
  attested_at: string | null;
  attestation_note: string | null;
  used_in_scoring: boolean;
  loaded_by: string | null;
  loaded_at: string;
  is_current: boolean;
  superseded_at: string | null;
}

export function listSnapshots(includeHistory = false): Promise<ReferenceSnapshot[]> {
  return request(`/api/reference-data/snapshots${includeHistory ? "?include_history=true" : ""}`);
}

export function reloadSnapshots(): Promise<
  { source: string; as_of: string; snapshot_id: number; unchanged: boolean }[]
> {
  return request("/api/reference-data/reload", { method: "POST" });
}

export function attestSnapshot(id: number, note: string): Promise<ReferenceSnapshot> {
  return request(`/api/reference-data/snapshots/${id}/attest`, {
    method: "POST",
    body: JSON.stringify({ note }),
  });
}

// ---------------------------------------------------------------------------
// Decision record
// ---------------------------------------------------------------------------

export interface DecisionRecordResponse {
  id: number;
  decision: string;
  decided_by: string;
  decided_at: string;
  checksum: string;
  intact: boolean;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  record: Record<string, any>;
}

export async function getDecisionRecord(assessmentId: number): Promise<DecisionRecordResponse | null> {
  try {
    return await request(`/api/assessments/${assessmentId}/decision-record`);
  } catch (err) {
    if ((err as { status?: number }).status === 404) return null;
    throw err;
  }
}
