/**
 * StageTracker — the horizontal pipeline tracker across the assessment
 * lifecycle:
 *
 *   Intake & Evidence -> Risk Identification -> Controls & Residual Risk
 *   -> Human Review -> Approval
 *
 * Two nodes each cover two real pipeline statuses (INTAKE + EVIDENCE_COLLECTION,
 * CONTROL_ASSESSMENT + RESIDUAL_RISK): both are on one screen, the later one
 * locked until its status is reached. See stageTrackerStages.ts.
 *
 * "Business Request" is the CreateAssessment form itself (before a record
 * exists) and is intentionally not tracked here.
 *
 * INHERENT_RISK_ASSESSMENT is a real backend status but isn't its own node;
 * it shares "Risk Identification". The whole approval chain (submitted to
 * manager, returned, ready for committee, deferred, final decisions) is the
 * one "Approval" node, with a short line saying where the chain stands; the
 * manager and committee stages live together on that screen. The mapping is
 * in stageTrackerStages.ts.
 *
 * The tracker is driven entirely by the assessment's real backend status.
 * A few statuses fall outside the tracked stages entirely:
 *  - REMEDIATION: shown as a banner above the tracker with the pipeline
 *    reset to before Intake.
 *  - MANAGER_REJECTED / REJECTED: shown as a terminal "rejected" banner;
 *    every stage is dimmed since the assessment never reached approval.
 */

import { approvalSubstatus, stageIndexForStatus, stageKeysForRole, TRACKED_STAGES } from "./stageTrackerStages";

interface StageTrackerProps {
  status: string;
  /** The viewer's role: the steps they work on are marked "Your step". */
  role?: string;
  onStageClick?: (stageIndex: number, stageKey: string) => void;
  disableNavigation?: boolean;
}

export default function StageTracker({
  status,
  role,
  onStageClick,
  disableNavigation = false,
}: StageTrackerProps) {
  const stages = TRACKED_STAGES;
  const yourStages = stageKeysForRole(role);
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
        {stages.map((stage, index) => {
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
              onClick={() => isClickable && onStageClick?.(index, stage.key)}
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
              {yourStages.includes(stage.key) && <span className="stage-tracker-yours">Your step</span>}
              {isCurrent && stage.key === "APPROVAL" && approvalSubstatus(status) && (
                <span className="stage-tracker-substatus">{approvalSubstatus(status)}</span>
              )}
              {index < stages.length - 1 && (
                <div className="stage-tracker-connector" />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
