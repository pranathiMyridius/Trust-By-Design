// P3: Segregation-of-Duties exceptions. Every rule is enforced by the
// backend (app/governance/sod.py); `actions` on each exception says what the
// signed-in user may do and, if not, why. The policy is PROVISIONAL --
// pending governance approval.
import { request, type Allowed } from "./governanceRecords";

export type SodStatus = "DRAFT" | "PENDING_APPROVAL" | "APPROVED" | "REJECTED" | "EXPIRED" | "REVOKED";
export type SodType = "COMMITTEE_SEPARATION" | "ADMIN_COMMITTEE_DUAL_ROLE";
export type RiskLevel = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";

export const SOD_TYPES: { value: SodType; label: string; help: string }[] = [
  {
    value: "COMMITTEE_SEPARATION",
    label: "Committee separation",
    help: "Lets a Committee Member who submitted, or managed, the assessment vote or decide on it.",
  },
  {
    value: "ADMIN_COMMITTEE_DUAL_ROLE",
    label: "Admin acting as committee member",
    help: "Lets an Admin vote or decide as a committee member; same-case administration is then refused.",
  },
];

export interface SodHistoryEntry {
  action: string;
  from_status: string | null;
  to_status: string | null;
  actor: string;
  detail: string | null;
  at: string;
}

export interface SodException {
  id: number;
  reference: string;
  exception_type: SodType;
  status: SodStatus;
  requestor_id: number;
  requestor: string | null;
  affected_user_id: number;
  affected_user: string | null;
  conflicting_roles: string[];
  assessment_id: number | null;
  business_justification: string;
  standard_workflow_reason: string;
  risk_level: RiskLevel;
  compensating_controls: string;
  start_at: string;
  end_at: string;
  tier: "STANDARD" | "COMMITTEE" | null;
  tier_reasons: string[];
  repeated: boolean;
  assigned_approver: string | null;
  assigned_reviewer: string | null;
  decision: string | null;
  decision_rationale: string | null;
  decided_by: string | null;
  decided_at: string | null;
  declaration: string | null;
  declared_at: string | null;
  revoked_by: string | null;
  revoked_at: string | null;
  revocation_reason: string | null;
  expired_at: string | null;
  last_reviewed_at: string | null;
  last_reviewed_by: string | null;
  last_review_note: string | null;
  submitted_at: string | null;
  created_at: string;
  flags: ("REPEATED" | "EXPIRING_SOON" | "DECLARATION_MISSING")[];
  actions: { submit: Allowed; decide: Allowed; declare: Allowed; revoke: Allowed; review: Allowed };
  history: SodHistoryEntry[];
  policy_status: string;
}

export interface SodExceptionInput {
  exception_type: SodType;
  affected_user_id: number;
  assessment_id: number | null;
  business_justification: string;
  standard_workflow_reason: string;
  risk_level: RiskLevel;
  compensating_controls: string;
  start_at: string;
  end_at: string;
}

export type SodScope = "mine" | "awaiting_me" | "all";

export const listSodExceptions = (scope: SodScope) => request<SodException[]>(`/api/sod-exceptions?scope=${scope}`);
export const getSodException = (id: number) => request<SodException>(`/api/sod-exceptions/${id}`);
export const createSodException = (input: SodExceptionInput) =>
  request<SodException>("/api/sod-exceptions", { method: "POST", body: JSON.stringify(input) });
export const submitSodException = (id: number) =>
  request<SodException>(`/api/sod-exceptions/${id}/submit`, { method: "POST" });
export const decideSodException = (id: number, decision: "APPROVE" | "REJECT", rationale: string) =>
  request<SodException>(`/api/sod-exceptions/${id}/decision`, { method: "POST", body: JSON.stringify({ decision, rationale }) });
export const declareSodException = (id: number, statement: string) =>
  request<SodException>(`/api/sod-exceptions/${id}/declaration`, { method: "POST", body: JSON.stringify({ rationale: statement }) });
export const revokeSodException = (id: number, reason: string) =>
  request<SodException>(`/api/sod-exceptions/${id}/revoke`, { method: "POST", body: JSON.stringify({ rationale: reason }) });
export const reviewSodException = (id: number, note: string) =>
  request<SodException>(`/api/sod-exceptions/${id}/review`, { method: "POST", body: JSON.stringify({ rationale: note }) });


export interface Candidate {
  id: number;
  name: string;
  role: string;
}

/** Who an exception of this type could be for (id, name, role only). */
export const listSodCandidates = (type: SodType) =>
  request<Candidate[]>(`/api/sod-exceptions/candidates?exception_type=${type}`);
