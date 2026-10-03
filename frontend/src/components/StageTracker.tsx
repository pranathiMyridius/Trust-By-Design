/**
 * StageTracker — the horizontal pipeline tracker across the assessment
 * lifecycle:
 *
 *   Business Request -> Intake -> Evidence Collection -> Risk
 *   Identification -> Control Assessment -> Residual Risk -> Human Review ->
 *   Submitted to Manager -> Ready for Committee -> Decision
 *
 * "Business Request" is the CreateAssessment form itself (before a record
 * exists) and is intentionally not one of the tracked stages here — this
 * component only renders once an assessment record exists, so it always
 * starts at "Intake".
 *
 * INHERENT_RISK_ASSESSMENT is a real backend status but isn't its own
 * tracker node — it shows the same content as "Risk Identification" (see
 * AssessmentWorkflow's inferStepFromStatus), so it maps onto that node via
 * STATUS_TO_STAGE_KEY below.
 *
 * The last three stages are the AW hierarchical approval workflow
 * (Business User -> Manager -> Committee), which replaced the old single
 * "Committee Decision" -> "Audit" pair. Several real backend statuses
 * map onto "Submitted to Manager" (also RETURNED_BY_MANAGER) and "Ready
 * for Committee" (also DEFERRED) since those are still in-progress at
 * that step; see STATUS_TO_STAGE_KEY below.
 *
 * The tracker is driven entirely by the assessment's real backend
 * `status`, not by any client-side/localStorage guess. A few statuses
 * fall outside the tracked stages entirely:
 *  - REMEDIATION: shown as a banner above the tracker with the pipeline
 *    reset to before Intake (nothing marked current/completed), since a
 *    remediation loop-back means the business owner must return to
 *    Intake and re-walk the pipeline.
 *  - MANAGER_REJECTED / REJECTED: shown as a terminal "rejected" banner;
 *    the tracker renders with every stage dimmed since the assessment
 *    never reached a final Approved disposition.
 */

export const TRACKED_STAGES: { key: string; label: string }[] = [
  { key: "INTAKE", label: "Intake" },
  { key: "EVIDENCE_COLLECTION", label: "Evidence Collection" },
  { key: "RISK_IDENTIFICATION", label: "Risk Identification" },
  { key: "CONTROL_ASSESSMENT", label: "Control Assessment" },
  { key: "RESIDUAL_RISK", label: "Residual Risk" },
  { key: "HUMAN_REVIEW", label: "Human Review" },
  { key: "SUBMITTED_TO_MANAGER", label: "Submitted to Manager" },
  { key: "READY_FOR_COMMITTEE", label: "Ready for Committee" },
  { key: "DECISION", label: "Decision" },
];

// Real backend statuses that aren't a tracked stage's own key, but should
// still highlight that stage as current while the assessment is there.
const STATUS_TO_STAGE_KEY: Record<string, string> = {
  // INHERENT_RISK_ASSESSMENT shows the same risk_results/overall_score
  // content as RISK_IDENTIFICATION (see AssessmentWorkflow's
  // inferStepFromStatus), so they share one tracker node.
  INHERENT_RISK_ASSESSMENT: "RISK_IDENTIFICATION",
  RETURNED_BY_MANAGER: "SUBMITTED_TO_MANAGER",
  DEFERRED: "READY_FOR_COMMITTEE",
  COMMITTEE_REVIEW: "READY_FOR_COMMITTEE",
  APPROVED: "DECISION",
  CLOSED: "DECISION",
  APPROVED_WITH_CONDITIONS: "DECISION",
};

export function stageIndexForStatus(status: string): number {
  const key = STATUS_TO_STAGE_KEY[status] ?? status;
  return TRACKED_STAGES.findIndex((stage) => stage.key === key);
}

interface StageTrackerProps {
  status: string;
  onStageClick?: (stageIndex: number) => void;
  disableNavigation?: boolean;
}

export default function StageTracker({ status, onStageClick, disableNavigation = false }: StageTrackerProps) {
  const currentIndex = stageIndexForStatus(status);
  const isRemediation = status === "REMEDIATION";
  const isRejected = status === "REJECTED" || status === "MANAGER_REJECTED";

  return (
    <div className="stage-tracker-wrapper">
      {isRemediation && (
        <div className="stage-tracker-banner remediation" role="status">
          <span aria-hidden="true">↺ </span>
          Remediation required — return to Intake to address committee
          feedback and restart the pipeline.
        </div>
      )}

      {isRejected && (
        <div className="stage-tracker-banner rejected" role="status">
          <span aria-hidden="true">✕ </span>
          {status === "MANAGER_REJECTED"
            ? "This assessment was rejected by its manager. The pipeline is closed."
            : "This assessment was rejected by the committee. The pipeline is closed."}
        </div>
      )}

      <div className="stage-tracker" role="list" aria-label="Assessment pipeline stages">
        {TRACKED_STAGES.map((stage, index) => {
          const isCompleted = !isRemediation && !isRejected && index < currentIndex;
          const isCurrent = !isRemediation && !isRejected && index === currentIndex;
          const isFuture = !isCompleted && !isCurrent;
          const isClickable = !disableNavigation && (isCompleted || isCurrent);

          return (
            <div
              className={`stage-tracker-step ${isCurrent ? "current" : ""} ${
                isCompleted ? "completed" : ""
              } ${isFuture ? "future" : ""} ${isClickable ? "clickable" : ""}`}
              role="listitem"
              aria-current={isCurrent ? "step" : undefined}
              key={stage.key}
              title={stage.label}
              onClick={() => isClickable && onStageClick?.(index)}
              style={{ cursor: isClickable ? "pointer" : "default" }}
            >
              <div className="stage-tracker-marker" aria-hidden="true">
                {isCompleted ? "✓" : index + 1}
              </div>
              <span className="stage-tracker-label">
                {stage.label}
                <span className="sr-only">
                  {isCompleted ? " (completed)" : isCurrent ? " (current stage)" : " (not started)"}
                </span>
              </span>
              {index < TRACKED_STAGES.length - 1 && (
                <div className="stage-tracker-connector" />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
