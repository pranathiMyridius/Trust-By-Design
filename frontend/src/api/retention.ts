// P5: retention policy versions, legal holds and the read-only eligibility
// report. Every rule (who may propose, approve, hold, release; what is
// eligible) is decided by the backend (app/governance/retention.py) -- the
// UI shows what the server returns and never computes eligibility itself.
// The policy is PROVISIONAL: pending governance approval.
import { request, type Allowed } from "./governanceRecords";

export type VersionStatus = "PROPOSED" | "ACTIVE" | "SUPERSEDED" | "REJECTED";
export type EligibilityStatus =
  | "LEGAL_HOLD"
  | "SOFT_DELETED"
  | "NO_POLICY"
  | "INVALID_POLICY"
  | "NOT_STARTED"
  | "INVALID_DATE"
  | "RETAINED"
  | "ELIGIBLE";

export interface RetentionPermissions {
  can_view: boolean;
  can_propose: boolean;
  can_approve: boolean;
  can_set_hold: boolean;
  can_release_hold: boolean;
  can_soft_delete: boolean;
  can_read_report: boolean;
  require_independent_approval: boolean;
  release_requires_different_user: boolean;
  proposers: string;
  approvers: string;
  min_retention_days: number;
  max_retention_days: number;
  min_reason_length: number;
  record_types: string[];
  policy_status: string;
}

export interface PolicyVersion {
  id: number;
  record_type: string;
  version: number;
  retention_days: number;
  previous_retention_days: number | null;
  basis: string;
  status: VersionStatus;
  effective_from: string | null;
  change_reason: string;
  proposed_by: string | null;
  proposed_at: string | null;
  decided_by: string | null;
  decided_at: string | null;
  decision_reason: string | null;
  supersedes_id: number | null;
  superseded_at: string | null;
  superseded_by_id: number | null;
  system_seeded: boolean;
  governance_approval_reference: string | null;
  governance_approved_at: string | null;
  policy_status: string;
  actions?: { decide: Allowed };
}

export interface RecordTypePolicies {
  record_type: string;
  basis: string;
  active: PolicyVersion | null;
  pending: PolicyVersion | null;
  versions: PolicyVersion[];
}

export interface PolicyListing {
  record_types: RecordTypePolicies[];
  permissions: RetentionPermissions;
  policy_status: string;
}

export interface Eligibility {
  record_id: number;
  record_type: string;
  reference: string | null;
  assessment_status: string;
  policy_version_id: number | null;
  policy_version: number | null;
  retention_days: number | null;
  basis: string | null;
  basis_date: string | null;
  eligible_at: string | null;
  eligible: boolean;
  eligibility_status: EligibilityStatus;
  legal_hold: boolean;
  is_deleted: boolean;
  reason: string;
  evaluated_at: string;
}

export interface EligibilityReport {
  items: Eligibility[];
  total: number;
  page: number;
  page_size: number;
  summary: Record<EligibilityStatus, number>;
  active_policy: PolicyVersion | null;
  evaluated_at: string;
  read_only: boolean;
  note: string;
  policy_status: string;
}

export interface HoldEvent {
  id: number;
  action: "SET" | "RELEASED";
  reason: string;
  matter_reference: string | null;
  actor: string;
  at: string | null;
  backfilled: boolean;
}

export interface LegalHoldSummary {
  assessment_id: number;
  reference: string | null;
  legal_hold: boolean;
  set_by: string | null;
  set_at: string | null;
  history: HoldEvent[];
}

export interface ReportFilters {
  status?: string;
  legal_hold?: "" | "true" | "false";
  policy_version_id?: string;
  within_days?: string;
  basis_from?: string;
  basis_to?: string;
  include_deleted?: boolean;
  sort?: "eligible_at" | "basis_date" | "record_id";
  order?: "asc" | "desc";
  page?: number;
  page_size?: number;
}

export const ELIGIBILITY_LABEL: Record<EligibilityStatus, string> = {
  ELIGIBLE: "Eligible for review",
  RETAINED: "Within retention period",
  LEGAL_HOLD: "On legal hold",
  NOT_STARTED: "Not started (no final decision)",
  INVALID_DATE: "Invalid or missing date",
  NO_POLICY: "No policy in force",
  INVALID_POLICY: "Invalid policy",
  SOFT_DELETED: "Soft-deleted",
};

export function getRetentionPermissions(): Promise<RetentionPermissions> {
  return request("/api/retention/permissions");
}

export function listRetentionPolicies(): Promise<PolicyListing> {
  return request("/api/retention/policies");
}

export function proposeRetentionPolicy(payload: {
  record_type: string;
  retention_days: number;
  change_reason: string;
}): Promise<PolicyVersion> {
  return request("/api/retention/policies", { method: "POST", body: JSON.stringify(payload) });
}

export function decideRetentionPolicy(
  versionId: number,
  decision: "APPROVE" | "REJECT",
  reason: string
): Promise<PolicyVersion> {
  return request(`/api/retention/policies/${versionId}/decision`, {
    method: "POST",
    body: JSON.stringify({ decision, reason }),
  });
}

export function getEligibilityReport(filters: ReportFilters): Promise<EligibilityReport> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== null && value !== "" && value !== false) {
      params.set(key, String(value));
    }
  }
  const query = params.toString();
  return request(`/api/retention/eligibility${query ? `?${query}` : ""}`);
}

export function listLegalHolds(currentOnly = false): Promise<{ items: LegalHoldSummary[]; policy_status: string }> {
  return request(`/api/retention/legal-holds${currentOnly ? "?current_only=true" : ""}`);
}
