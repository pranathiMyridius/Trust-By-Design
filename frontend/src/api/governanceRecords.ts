// P2 governance records: append-only committee votes, the override ledger
// and its independent review, the mandatory challenge-review sign-off,
// versioned control changes, and calculated-vs-human comparisons.
// See backend/app/api/approvals.py, assessments.py (overrides),
// challenge_review.py and controls.py.
import { formatErrorDetail, type Control } from "./assessments";
import { API_BASE_URL } from "./config";
import { authFetch } from "./http";

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
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
    const error = new Error(detail || `Request failed (${response.status})`);
    (error as Error & { status?: number }).status = response.status;
    throw error;
  }
  return response.json();
}

// -- R12.6 committee votes ---------------------------------------------------

export type VoteValue = "APPROVE" | "DISSENT" | "ABSTAIN";

export interface VoteRecord {
  id: number;
  assessment_id: number;
  member_id: number;
  member_name: string | null;
  delegate_id?: number | null;
  delegate_name?: string | null;
  delegation_id?: number | null;
  vote: VoteValue;
  comment: string | null;
  voted_at: string;
  version: number;
  is_current: boolean;
  superseded_at: string | null;
  superseded_by_id: number | null;
  cast_by_id: number | null;
  recast_reason: string | null;
}

/** Every vote ever cast on the assessment, superseded ones included. */
export function getVoteHistory(assessmentId: number): Promise<VoteRecord[]> {
  return request(`/api/assessments/${assessmentId}/committee-votes?include_history=true`);
}

/** A first vote, or a re-cast (which needs a reason; the earlier vote is kept). */
export function castVote(
  assessmentId: number,
  vote: VoteValue,
  comment?: string,
  recastReason?: string
): Promise<VoteRecord> {
  return request(`/api/assessments/${assessmentId}/committee-votes`, {
    method: "POST",
    body: JSON.stringify({ vote, comment: comment || null, recast_reason: recastReason || null }),
  });
}

// -- R6.7 / R10.2-R10.4 override ledger --------------------------------------

export type ReviewStatus = "APPLIED" | "PROPOSED" | "CONFIRMED" | "REJECTED";

export interface LedgerEntry {
  id: number;
  assessment_id: number;
  section: string;
  field_name: string;
  entity_id: string | null;
  ai_value: string | null;
  ai_value_source: "SYSTEM" | null;
  human_value: string;
  reason: string;
  overridden_by: string | null;
  overridden_by_id: number | null;
  created_at: string;
  review_status: ReviewStatus | null;
  reviewed_by: string | null;
  reviewed_by_id: number | null;
  reviewed_at: string | null;
  review_note: string | null;
  // P3: server-classified materiality, approval and workflow state, and
  // what the requesting user may do (computed by the backend).
  origin?: "TYPED" | "PROPOSAL" | null;
  materiality?: "NONMATERIAL" | "MATERIAL" | "CRITICAL" | null;
  materiality_reasons?: string[];
  approval_status?: "NOT_REQUIRED" | "PENDING" | "APPROVED" | "REJECTED" | null;
  approved_by?: string | null;
  approved_at?: string | null;
  approval_rationale?: string | null;
  state?: string | null;
  actions?: { review?: Allowed; approve?: Allowed };
  // Status of the review/approval rules (PROVISIONAL_PENDING_GOVERNANCE_APPROVAL).
  policy_status?: string;
}

/** A server-computed permission: whether, and if not, why not. */
export interface Allowed {
  allowed: boolean;
  reason: string | null;
}

export interface OverrideProposal {
  section: string;
  field_name: string;
  entity_id?: string | null;
  human_value: string;
  reason: string;
}

export function getLedger(assessmentId: number): Promise<LedgerEntry[]> {
  return request(`/api/assessments/${assessmentId}/overrides`);
}

/** The system value is read from the record by the server, never sent. */
export function proposeOverride(assessmentId: number, payload: OverrideProposal): Promise<LedgerEntry> {
  return request(`/api/assessments/${assessmentId}/overrides`, { method: "POST", body: JSON.stringify(payload) });
}

export function reviewOverride(
  assessmentId: number,
  overrideId: number,
  decision: "CONFIRM" | "REJECT",
  note: string
): Promise<LedgerEntry> {
  return request(`/api/assessments/${assessmentId}/overrides/${overrideId}/review`, {
    method: "PATCH",
    body: JSON.stringify({ decision, note }),
  });
}

/** P3: approve or reject a material/critical override after its review. */
export function approveOverride(
  assessmentId: number,
  overrideId: number,
  decision: "APPROVE" | "REJECT",
  rationale: string
): Promise<LedgerEntry> {
  return request(`/api/assessments/${assessmentId}/overrides/${overrideId}/approval`, {
    method: "PATCH",
    body: JSON.stringify({ decision, rationale }),
  });
}

/** Calculated value with the human value beside it (decision package). */
export interface ValueComparison {
  section: string;
  item: string;
  calculated_value: string | null;
  human_value: string | null;
  difference: string | null;
  reason: string | null;
  by: string | null;
  at: string | null;
  review_status: ReviewStatus | "LEGACY_UNREVIEWED";
  counts_in_decision: boolean;
  reviewed_by?: string;
  review_note?: string;
}

// -- R11 challenge-review sign-off -------------------------------------------

export interface ChallengeSignoff {
  id: number;
  assessment_id: number;
  // P3: REVIEW (independent review) or SIGNOFF.
  stage: "REVIEW" | "SIGNOFF";
  committee_escalation: boolean;
  version: number;
  is_current: boolean;
  outcome: "NO_TRIGGERS_FIRED" | "FINDINGS_ADDRESSED";
  reason: string;
  triggers: { triggered: boolean; triggers: { name: string; fired: boolean }[] };
  findings: { id: number; category: string; severity: string; resolution_status: string }[];
  reviewer: string;
  reviewer_id: number;
  reviewer_role: string;
  completed_at: string | null;
  superseded_at: string | null;
  superseded_reason: string | null;
}

export interface ChallengeSignoffState {
  assessment_id: number;
  // P3: the independent review, then the sign-off.
  review: ChallengeSignoff | null;
  current: ChallengeSignoff | null;
  valid: boolean;
  problem: string | null;
  history: ChallengeSignoff[];
  actions?: { review: Allowed; signoff: Allowed };
  policy_status?: string;
}

export function getChallengeSignoff(assessmentId: number): Promise<ChallengeSignoffState> {
  return request(`/api/assessments/${assessmentId}/challenge-review/signoff`);
}

/** P3: the independent challenge review (before the sign-off). */
export function completeChallengeReview(assessmentId: number, summary: string): Promise<ChallengeSignoff> {
  return request(`/api/assessments/${assessmentId}/challenge-review/review`, {
    method: "POST",
    body: JSON.stringify({ reason: summary }),
  });
}

export function signOffChallengeReview(assessmentId: number, reason: string): Promise<ChallengeSignoff> {
  return request(`/api/assessments/${assessmentId}/challenge-review/signoff`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
}

// -- P3 committee readiness (authoritative, computed by the backend) ------------

export interface ReadinessBlocker {
  code: string;
  message: string;
  responsible_role: string;
  next_action: string;
  items: unknown[];
}

export interface Readiness {
  assessment_id: number;
  purpose: "COMMITTEE_SUBMISSION" | "FINAL_DECISION";
  ready: boolean;
  status: "READY" | "BLOCKED";
  blockers: ReadinessBlocker[];
  warnings: { code: string; message: string; items?: unknown[] }[];
  next_action: string;
  findings_by_severity: Record<string, Record<string, number>>;
  sod: {
    active_exceptions: { id: number; reference: string; type: string; end_at: string; declared: boolean }[];
    pending_exceptions: string[];
  };
  policy_status: string;
}

export function getReadiness(
  assessmentId: number,
  purpose: Readiness["purpose"] = "COMMITTEE_SUBMISSION"
): Promise<Readiness> {
  return request(`/api/assessments/${assessmentId}/readiness?purpose=${purpose}`);
}

/** The provisional governance status shown wherever R-GOV rules apply. */
export const POLICY_PENDING_LABEL = "Pending Governance Approval";

// -- R7.2 / R10.2 control versioning -----------------------------------------

export interface ControlChange {
  risk_factor_id?: number;
  control_type?: string;
  description?: string | null;
  owner?: string | null;
  performing_department?: string | null;
  frequency?: string | null;
  trigger?: string | null;
  scope?: string | null;
  evidence_source?: string | null;
  operating_status?: string;
  reason: string;
}

export interface ControlRevision {
  id: number;
  control_id: number;
  assessment_id: number;
  change_type: "EDIT" | "REMAP" | "UNMAP";
  version: number;
  previous_config: Record<string, unknown>;
  new_config: Record<string, unknown> | null;
  changed_fields: string[];
  reason: string;
  changed_by: string;
  changed_by_id: number;
  changed_at: string;
}

export function changeControl(assessmentId: number, controlId: number, change: ControlChange): Promise<Control> {
  return request(`/api/assessments/${assessmentId}/controls/${controlId}`, {
    method: "PATCH",
    body: JSON.stringify(change),
  });
}

export function unmapControl(assessmentId: number, controlId: number, reason: string): Promise<Control> {
  return request(`/api/assessments/${assessmentId}/controls/${controlId}/unmap`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
}

export function getControlRevisions(assessmentId: number, controlId: number): Promise<ControlRevision[]> {
  return request(`/api/assessments/${assessmentId}/controls/${controlId}/revisions`);
}

// -- P3 governance designations (Admin only) ----------------------------------

export interface GovernancePolicy {
  policy_status: string;
  policy: { designation_base_roles: Record<string, string[]> } & Record<string, unknown>;
}

export function getGovernancePolicy(): Promise<GovernancePolicy> {
  return request("/api/governance/policy");
}

export function setUserDesignations(userId: number, designations: string[], reason: string) {
  return request(`/api/users/${userId}/designations`, {
    method: "PUT",
    body: JSON.stringify({ designations, reason }),
  });
}
