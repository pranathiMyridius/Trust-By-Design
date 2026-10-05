// The tracked stages behind StageTracker (kept apart from the component so
// the component file exports only a component). See StageTracker.tsx.
//
// The approval chain (Submitted to Manager -> Ready for Committee ->
// Decision) is one "Approval" node: it is a single screen with a manager
// stage and a committee stage. The backend still has every one of those
// statuses; STATUS_TO_STAGE_KEY folds them onto the node.

export const TRACKED_STAGES: { key: string; label: string }[] = [
  { key: "INTAKE_EVIDENCE", label: "Intake & Evidence" },
  { key: "RISK_IDENTIFICATION", label: "Risk Identification" },
  { key: "CONTROLS_RESIDUAL", label: "Controls & Residual Risk" },
  { key: "HUMAN_REVIEW", label: "Human Review" },
  { key: "APPROVAL", label: "Approval" },
];

// The workflow page still walks the real pipeline one status at a time (7
// internal steps, each with its own advance gate). A tracker node can cover
// two of them: both are on one screen, the later one locked until reached.
export const STAGE_STEPS: Record<string, number[]> = {
  INTAKE_EVIDENCE: [1, 2],
  RISK_IDENTIFICATION: [3],
  CONTROLS_RESIDUAL: [4, 5],
  HUMAN_REVIEW: [6],
  APPROVAL: [7],
};

/** Index of the tracker node that covers an internal workflow step (1-7). */
export function stageIndexForStep(step: number): number {
  const index = TRACKED_STAGES.findIndex((stage) => (STAGE_STEPS[stage.key] ?? []).includes(step));
  return index >= 0 ? index : 0;
}

// Real backend statuses that aren't a tracked stage's own key, but should
// still highlight that stage as current while the assessment is there.
const STATUS_TO_STAGE_KEY: Record<string, string> = {
  // Intake and evidence collection are one tracker node, as are the control
  // assessment and the residual risk that follows from it.
  INTAKE: "INTAKE_EVIDENCE",
  EVIDENCE_COLLECTION: "INTAKE_EVIDENCE",
  CONTROL_ASSESSMENT: "CONTROLS_RESIDUAL",
  RESIDUAL_RISK: "CONTROLS_RESIDUAL",
  // INHERENT_RISK_ASSESSMENT shows the same risk_results/overall_score
  // content as RISK_IDENTIFICATION (see AssessmentWorkflow's
  // inferStepFromStatus), so they share one tracker node.
  INHERENT_RISK_ASSESSMENT: "RISK_IDENTIFICATION",
  // The whole approval chain, manager through committee and the final
  // decision, is the one Approval node.
  SUBMITTED_TO_MANAGER: "APPROVAL",
  RETURNED_BY_MANAGER: "APPROVAL",
  READY_FOR_COMMITTEE: "APPROVAL",
  COMMITTEE_REVIEW: "APPROVAL",
  DEFERRED: "APPROVAL",
  APPROVED: "APPROVAL",
  APPROVED_WITH_CONDITIONS: "APPROVAL",
  CLOSED: "APPROVAL",
};

export function stageIndexForStatus(status: string): number {
  const key = STATUS_TO_STAGE_KEY[status] ?? status;
  return TRACKED_STAGES.findIndex((stage) => stage.key === key);
}

// Statuses by phase of the Approval screen (see ApprovalStage).
export const MANAGER_PHASE_STATUSES = ["SUBMITTED_TO_MANAGER", "RETURNED_BY_MANAGER"];
export const COMMITTEE_PHASE_STATUSES = ["READY_FOR_COMMITTEE", "COMMITTEE_REVIEW", "DEFERRED"];
export const DECIDED_STATUSES = ["APPROVED", "APPROVED_WITH_CONDITIONS", "REJECTED", "MANAGER_REJECTED", "CLOSED"];

const APPROVAL_SUBSTATUS: Record<string, string> = {
  SUBMITTED_TO_MANAGER: "Awaiting manager",
  RETURNED_BY_MANAGER: "Returned to owner",
  READY_FOR_COMMITTEE: "Awaiting committee",
  COMMITTEE_REVIEW: "Voting open",
  DEFERRED: "Deferred",
  APPROVED: "Approved",
  APPROVED_WITH_CONDITIONS: "Approved with conditions",
  CLOSED: "Closed",
};

/** A short line under the Approval node saying where the chain stands. */
export function approvalSubstatus(status: string): string | null {
  return APPROVAL_SUBSTATUS[status] ?? null;
}

// The steps each role works on, so the tracker can mark "your step". Roles
// that review or oversee (admin, auditor, executive) have none of their own.
const ROLE_STAGE_KEYS: Record<string, string[]> = {
  BUSINESS_USER: ["INTAKE_EVIDENCE"],
  FCRM_ANALYST: ["RISK_IDENTIFICATION", "CONTROLS_RESIDUAL", "HUMAN_REVIEW"],
  MANAGER: ["APPROVAL"],
  COMMITTEE_MEMBER: ["APPROVAL"],
};

export function stageKeysForRole(role: string | undefined): string[] {
  return (role && ROLE_STAGE_KEYS[role]) || [];
}
