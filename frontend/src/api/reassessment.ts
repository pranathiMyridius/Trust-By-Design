// P6 (Stage 18): reassessment status, triggers, proposals and the
// structured comparison. Every rule (who may propose, flag or resolve; what
// happens to the parent) is enforced by the backend
// (app/services/reassessment_lifecycle.py); `actions` says what the
// signed-in user may do and, if not, why.
import { request, type Allowed } from "./governanceRecords";

export type ReassessmentState = "IN_FORCE" | "UNDER_REASSESSMENT" | "SUPERSEDED";
export type TriggerStatus = "OPEN" | "ACKNOWLEDGED" | "REASSESSMENT_CREATED" | "DISMISSED";
export type ChangeKind = "ADDED" | "REMOVED" | "CHANGED" | "UNCHANGED";

export const TRIGGER_TYPES: { value: string; label: string }[] = [
  { value: "MAJOR_PRODUCT_CHANGE", label: "Major product change" },
  { value: "NEW_GEOGRAPHY", label: "New geography" },
  { value: "NEW_CUSTOMER_SEGMENT", label: "New customer segment" },
  { value: "NEW_VENDOR", label: "New vendor" },
  { value: "MATERIAL_TRANSACTION_VOLUME_CHANGE", label: "Material transaction volume change" },
  { value: "NEW_DELIVERY_CHANNEL", label: "New delivery channel" },
  { value: "NEW_TECHNOLOGY", label: "New technology" },
  { value: "REGULATORY_POLICY_CHANGE", label: "Regulatory or policy change" },
  { value: "SIGNIFICANT_CONTROL_FAILURE", label: "Significant control failure" },
  { value: "EXPIRY", label: "Approval expired" },
  { value: "PERIODIC_REVIEW", label: "Periodic review due" },
];

export function triggerLabel(type: string): string {
  return TRIGGER_TYPES.find((t) => t.value === type)?.label ?? type.replace(/_/g, " ").toLowerCase();
}

export interface ReassessmentStatus {
  assessment_id: number;
  status: string;
  reassessment_state: ReassessmentState;
  superseded_by_id: number | null;
  superseded_at: string | null;
  parent_assessment_id: number | null;
  in_progress_reassessment_id: number | null;
  reassessments: { id: number; status: string; reference_id: string | null }[];
  next_review_date: string | null;
  actions: { propose: Allowed; flag: Allowed; resolve: Allowed };
}

export interface Trigger {
  id: number;
  assessment_id: number;
  trigger_type: string;
  description: string;
  detected_at: string;
  detected_by: string | null;
  status: TriggerStatus;
  dismissed_reason: string | null;
  resolved_by: string | null;
  resolved_at: string | null;
  reassessment_id: number | null;
  resolution_note: string | null;
}

export interface DiffRow<T> {
  key: string;
  change: ChangeKind;
  before: T | null;
  after: T | null;
  changed_fields: string[];
}

export interface FactorState {
  applicable: boolean;
  likelihood: number | null;
  impact: number | null;
  score: number | null;
  severity: string | null;
}

export interface ControlState {
  control_type: string;
  operating_status: string | null;
  design_adequacy: string | null;
  operating_effectiveness: string | null;
}

export interface ConditionState {
  description: string;
  status: string | null;
  due_date: string | null;
}

export interface ReusedField {
  field: string;
  label: string;
  value: string | null;
  source_assessment_id: number | null;
  source_date: string | null;
  age_days: number | null;
}

export interface Comparison {
  parent_assessment_id: number;
  reassessment_id: number;
  fields_changed: { field: string; old_value: string | null; new_value: string | null }[];
  reused_fields: ReusedField[];
  structured: {
    scores: { measure: string; before: number | null; after: number | null; before_level: string | null; after_level: string | null; delta: number | null }[];
    factors: DiffRow<FactorState>[];
    controls: DiffRow<ControlState>[];
    conditions: DiffRow<ConditionState>[];
    summary: Record<"factors" | "controls" | "conditions", Record<ChangeKind, number>>;
  };
}

export function getReassessmentStatus(assessmentId: number): Promise<ReassessmentStatus> {
  return request(`/api/assessments/${assessmentId}/reassessment/status`);
}

export function listTriggers(assessmentId: number): Promise<Trigger[]> {
  return request(`/api/assessments/${assessmentId}/reassessment/triggers`);
}

export function checkTriggers(assessmentId: number): Promise<Trigger[]> {
  return request(`/api/assessments/${assessmentId}/reassessment/check-triggers`);
}

export function flagTrigger(assessmentId: number, trigger_type: string, description: string): Promise<Trigger> {
  return request(`/api/assessments/${assessmentId}/reassessment/flag-trigger`, {
    method: "POST",
    body: JSON.stringify({ trigger_type, description }),
  });
}

export function resolveTrigger(
  assessmentId: number,
  triggerId: number,
  status: Exclude<TriggerStatus, "OPEN">,
  text?: string
): Promise<Trigger> {
  const body =
    status === "DISMISSED" ? { status, dismissed_reason: text } : { status, resolution_note: text || null };
  return request(`/api/assessments/${assessmentId}/reassessment/triggers/${triggerId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function proposeChange(
  assessmentId: number,
  changes: Record<string, string>,
  reason: string
): Promise<{ reassessment_id: number; triggers: Trigger[] }> {
  return request(`/api/assessments/${assessmentId}/reassessment/propose-change`, {
    method: "POST",
    body: JSON.stringify({ changes, reason }),
  });
}

export function getComparison(assessmentId: number): Promise<Comparison> {
  return request(`/api/assessments/${assessmentId}/reassessment/compare`);
}
