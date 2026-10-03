// Stage 14: Workflow and Status Management. See
// backend/app/services/workflow.py (rules) and backend/app/api/workflow.py
// (endpoints).
import { authFetch } from "./http";
import type { Assessment } from "./assessments";
import { API_BASE_URL } from "./config";


export type SlaState = "NONE" | "ON_TRACK" | "AT_RISK" | "OVERDUE";

export interface WorkflowOwner {
  party: string | null;
  owner_name: string;
  owner_user_id: number | null;
  team: string;
  next_action: string;
}

export interface WorkflowTransitionRecord {
  id: number;
  assessment_id: number;
  from_status: string | null;
  to_status: string;
  from_workflow_status: string | null;
  to_workflow_status: string;
  action: string;
  reason: string;
  user_id: number | null;
  actor: string;
  created_at: string;
}

export interface AvailableTransition {
  to_status: string;
  to_workflow_status: string;
  to_workflow_label: string;
  allowed_for_user: boolean;
  roles: string[];
}

export type WorkflowAction =
  | "submit"
  | "withdraw"
  | "open_committee_review"
  | "close"
  | "assign"
  | "set_target_date";

export interface WorkflowSummary {
  assessment_id: number;
  reference_id: string | null;
  title: string;
  status: string;
  workflow_status: string;
  workflow_status_label: string;
  owner: WorkflowOwner;
  current_assignee_id: number | null;
  status_entered_at: string | null;
  status_due_at: string | null;
  target_date: string | null;
  sla_state: SlaState;
  sla_days: number | null;
  escalation_level: number;
  escalated_at: string | null;
  escalated_to_id: number | null;
  escalated_to_name: string | null;
  escalation_note: string | null;
  priority: string | null;
  mandatory_issues: string[];
  available_transitions: AvailableTransition[];
  available_actions: WorkflowAction[];
  history: WorkflowTransitionRecord[];
}

export interface WorkQueueItem {
  assessment_id: number;
  reference_id: string | null;
  title: string;
  change_type: string;
  status: string;
  workflow_status: string;
  workflow_status_label: string;
  priority: string | null;
  risk_level: string | null;
  owner: WorkflowOwner;
  status_entered_at: string | null;
  status_due_at: string | null;
  target_date: string | null;
  sla_state: SlaState;
  escalation_level: number;
  escalation_note: string | null;
  reason: "TASK" | "ESCALATION";
}

// R13.3: an overdue action item escalated to the user.
export interface ActionEscalationItem {
  action_item_id: number;
  assessment_id: number;
  reference_id: string | null;
  assessment_title: string;
  title: string;
  owner: string | null;
  priority: string;
  due_date: string | null;
  escalated_at: string | null;
  escalation_note: string | null;
}

// Stage 18: an approval that has expired or is due for periodic review.
export interface ReassessmentAlertItem {
  trigger_id: number;
  assessment_id: number;
  reference_id: string | null;
  assessment_title: string;
  trigger_type: "EXPIRY" | "PERIODIC_REVIEW" | string;
  description: string;
  next_review_date: string | null;
  detected_at: string | null;
  // P6: every open trigger (not only dates), where the approval stands,
  // and what this user may do (decided by the server).
  trigger_status?: "OPEN" | "ACKNOWLEDGED" | string;
  reassessment_state?: "IN_FORCE" | "UNDER_REASSESSMENT" | "SUPERSEDED" | string;
  in_progress_reassessment_id?: number | null;
  can_start_reassessment?: boolean;
  can_resolve?: boolean;
}

export interface WorkQueue {
  tasks: WorkQueueItem[];
  escalations: WorkQueueItem[];
  overdue_count: number;
  at_risk_count: number;
  action_escalations?: ActionEscalationItem[];
  reassessment_alerts?: ReassessmentAlertItem[];
}

export interface AssignableUser {
  id: number;
  email: string;
  full_name: string | null;
  role: string;
}

export const WORKFLOW_STATUS_LABELS: Record<string, string> = {
  DRAFT: "Draft",
  SUBMITTED: "Submitted",
  INTAKE_VALIDATION: "Intake Validation",
  INFORMATION_REQUESTED: "Information Requested",
  EVIDENCE_REVIEW: "Evidence Review",
  RISK_ASSESSMENT_IN_PROGRESS: "Risk Assessment in Progress",
  ANALYST_REVIEW: "Analyst Review",
  CHALLENGE_REVIEW: "Challenge Review",
  READY_FOR_COMMITTEE: "Ready for Committee",
  COMMITTEE_REVIEW: "Committee Review",
  APPROVED: "Approved",
  APPROVED_WITH_CONDITIONS: "Approved with Conditions",
  DEFERRED: "Deferred",
  REJECTED: "Rejected",
  CLOSED: "Closed",
  AMENDMENT_REQUIRED: "Amendment Required",
};

export function workflowLabel(status: string | null | undefined): string {
  if (!status) {
    return "—";
  }
  return WORKFLOW_STATUS_LABELS[status] ?? status;
}

export const SLA_LABELS: Record<SlaState, string> = {
  NONE: "No SLA",
  ON_TRACK: "On track",
  AT_RISK: "At risk",
  OVERDUE: "Overdue",
};

// The backend returns either a plain string detail or, for blocked
// transitions / missing fields, {message, issues | missing_fields}.
async function errorMessage(response: Response, fallback: string): Promise<string> {
  try {
    const body = await response.json();
    const detail = body?.detail;
    if (typeof detail === "string") {
      return detail;
    }
    if (detail?.message) {
      const list: string[] = detail.issues ?? detail.missing_fields ?? [];
      return list.length ? `${detail.message} ${list.join(" ")}` : detail.message;
    }
    if (Array.isArray(detail)) {
      return detail.map((item) => item?.msg ?? String(item)).join(" ");
    }
  } catch {
    // fall through
  }
  return fallback;
}

async function send<T>(path: string, init: RequestInit, fallback: string): Promise<T> {
  const response = await authFetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", Accept: "application/json", ...init.headers },
  });
  if (!response.ok) {
    throw new Error(await errorMessage(response, fallback));
  }
  return response.json();
}

export function getAssessmentWorkflow(assessmentId: number): Promise<WorkflowSummary> {
  return send(`/api/assessments/${assessmentId}/workflow`, {}, "Failed to load workflow");
}

export function getWorkQueue(): Promise<WorkQueue> {
  return send("/api/workflow/work-queue", {}, "Failed to load work queue");
}

export function getAssignableUsers(assessmentId: number): Promise<AssignableUser[]> {
  return send(
    `/api/assessments/${assessmentId}/workflow/assignable-users`,
    {},
    "Failed to load assignable users"
  );
}

export function submitDraft(assessmentId: number): Promise<Assessment> {
  return send(`/api/assessments/${assessmentId}/workflow/submit`, { method: "POST" }, "Failed to submit draft");
}

export function withdrawAssessment(assessmentId: number, reason: string): Promise<Assessment> {
  return send(
    `/api/assessments/${assessmentId}/workflow/withdraw`,
    { method: "POST", body: JSON.stringify({ reason }) },
    "Failed to withdraw assessment"
  );
}

export function openCommitteeReview(assessmentId: number, reason?: string): Promise<Assessment> {
  return send(
    `/api/assessments/${assessmentId}/workflow/open-committee-review`,
    { method: "POST", body: JSON.stringify({ reason: reason ?? null }) },
    "Failed to open committee review"
  );
}

export function closeAssessment(assessmentId: number, reason: string): Promise<Assessment> {
  return send(
    `/api/assessments/${assessmentId}/workflow/close`,
    { method: "POST", body: JSON.stringify({ reason }) },
    "Failed to close assessment"
  );
}

export function assignCurrentTask(
  assessmentId: number,
  userId: number | null,
  reason?: string
): Promise<WorkflowSummary> {
  return send(
    `/api/assessments/${assessmentId}/workflow/assign`,
    { method: "POST", body: JSON.stringify({ user_id: userId, reason: reason ?? null }) },
    "Failed to assign task"
  );
}

export function setTargetDate(
  assessmentId: number,
  targetDate: string,
  reason: string
): Promise<WorkflowSummary> {
  return send(
    `/api/assessments/${assessmentId}/workflow/target-date`,
    { method: "PATCH", body: JSON.stringify({ target_date: targetDate, reason }) },
    "Failed to set target date"
  );
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) {
    return "—";
  }
  // Backend datetimes are UTC; SQLite ones come back without a zone.
  const normalized = /[zZ]|[+-]\d\d:?\d\d$/.test(value) ? value : `${value}Z`;
  return new Date(normalized).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

export function formatDate(value: string | null | undefined): string {
  if (!value) {
    return "—";
  }
  return new Date(`${value.slice(0, 10)}T00:00:00`).toLocaleDateString(undefined, {
    dateStyle: "medium",
  });
}
