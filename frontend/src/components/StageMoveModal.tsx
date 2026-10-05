import { useEffect, useRef } from "react";
import "./StageMoveModal.css";

import type { Assessment, RiskResult } from "../api/assessments";
import type { CurrentUser } from "../api/auth";
import type { ProcessingJob, ProcessingStage } from "../api/processing";
import NavIcon from "./NavIcons";

/*
 * Live view of a stage move (a STAGE_ADVANCE background job), set in the
 * full assessment lifecycle. The stage being entered expands into the
 * move's own steps, straight from the job's per-step progress (backend
 * app/langgraph/progress.py); stages before it are done and stages after
 * it are still to come.
 */

type RowStatus = "completed" | "current" | "running" | "warning" | "queued" | "failed" | "blocked" | "skipped";

const STATUS_LABEL: Record<RowStatus, string> = {
  completed: "Completed",
  current: "Current stage",
  running: "Running",
  warning: "Needs attention",
  queued: "Queued",
  failed: "Failed",
  blocked: "Blocked",
  skipped: "Skipped",
};

const LIFECYCLE: { stage: string; title: string; owner: string; evidence: string }[] = [
  { stage: "INTAKE", title: "Intake & Entity Verification", owner: "BUSINESS_USER", evidence: "intake request, mandatory fields" },
  { stage: "EVIDENCE_COLLECTION", title: "Evidence Collection & Document Extraction", owner: "BUSINESS_USER", evidence: "documents, confirmed business profile" },
  { stage: "RISK_IDENTIFICATION", title: "Risk Identification", owner: "FCRM_ANALYST", evidence: "screening output, risk factors" },
  { stage: "INHERENT_RISK_ASSESSMENT", title: "Inherent Risk Assessment", owner: "FCRM_ANALYST", evidence: "factor ratings, inherent score" },
  { stage: "CONTROL_ASSESSMENT", title: "Control Assessment", owner: "FCRM_ANALYST", evidence: "controls, effectiveness ratings, challenge outcome" },
  { stage: "RESIDUAL_RISK", title: "Residual Risk", owner: "FCRM_ANALYST", evidence: "residual grid, floors applied" },
  { stage: "HUMAN_REVIEW", title: "Human Review", owner: "FCRM_ANALYST", evidence: "decision-ready draft" },
  { stage: "COMMITTEE", title: "Committee Review & Decision", owner: "MANAGER / COMMITTEE_MEMBER", evidence: "final committee pack" },
];

const ROLE_SCOPE: Record<string, string> = {
  BUSINESS_USER: "Least-privilege visibility: intake, extraction, retry and observation only.",
  FCRM_ANALYST: "Runs and reviews risk identification, factor ratings and controls.",
  MANAGER: "Oversees the pipeline; may run or retry stage moves for the team.",
  COMMITTEE_MEMBER: "Observes progress; acts at committee review.",
  ADMIN: "Full visibility; may run or retry any pipeline step.",
};

function formatTime(value: string | null | undefined): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", timeZoneName: "short" });
}

function targetIndexFor(fromStatus: string): number {
  if (fromStatus === "REMEDIATION") return 0;
  const index = LIFECYCLE.findIndex((row) => row.stage === fromStatus);
  return index < 0 ? 0 : index + 1;
}

/* The stage being entered, from the job's state. */
function targetStatus(job: ProcessingJob | null): RowStatus {
  if (!job || !job.is_finished) return "running";
  if (job.status === "FAILED") return "failed";
  return (job.stages ?? []).some((step) => step.status === "warning") ? "warning" : "completed";
}

function StatusIcon({ status }: { status: RowStatus | ProcessingStage["status"] }) {
  if (status === "completed") return <NavIcon name="check-circle" size={16} />;
  if (status === "failed") return <NavIcon name="x-circle" size={16} />;
  if (status === "running") return <span className="rim-spinner" />;
  if (status === "warning") return <span className="rim-warn">!</span>;
  return <span className="rim-dot" />;
}

const ENGINES = [
  { mode: "ai_assisted", label: "AI-Assisted Engine", className: "ai" },
  { mode: "rules_only", label: "Rules-Only Engine", className: "rules" },
  { mode: "unavailable", label: "Unavailable", className: "unavailable" },
] as const;

interface StageMoveModalProps {
  assessment: Assessment;
  user: CurrentUser;
  /** The stage being left (known before the job exists). */
  fromStatus: string;
  /** Null while the start request is still in flight. */
  job: ProcessingJob | null;
  documentCount: number;
  /** The AI's per-category prediction, shown once Risk Identification finishes. */
  riskResults?: RiskResult[];
  retrying: boolean;
  onRetry: () => void;
  /** Close the dialog; a running move carries on in the background. */
  onClose: () => void;
}

export default function StageMoveModal({
  assessment,
  user,
  fromStatus,
  job,
  documentCount,
  riskResults = [],
  retrying,
  onRetry,
  onClose,
}: StageMoveModalProps) {
  const dialogRef = useRef<HTMLDivElement>(null);

  const targetIndex = targetIndexFor(fromStatus);
  const target = LIFECYCLE[targetIndex];
  const current = targetStatus(job);
  const succeeded = current === "completed" || current === "warning";
  const steps = job?.stages ?? [];
  const running = !job || !job.is_finished;
  const failed = job?.status === "FAILED";

  const rows = LIFECYCLE.map((row, index) => {
    let status: RowStatus;
    if (index === targetIndex) status = current;
    else if (index === targetIndex - 1) status = succeeded ? "completed" : "current";
    else if (index < targetIndex && fromStatus !== "REMEDIATION") status = "completed";
    else if (row.stage === "COMMITTEE") status = succeeded && target.stage === "HUMAN_REVIEW" ? "queued" : "blocked";
    else status = "queued";

    const evidence =
      row.stage === "EVIDENCE_COLLECTION"
        ? `${documentCount} document${documentCount === 1 ? "" : "s"}, confirmed business profile`
        : row.evidence;
    return { ...row, status, evidence };
  });

  // Lifecycle progress, counting the stage being entered by its steps.
  const doneSteps = steps.filter((step) => step.status !== "queued" && step.status !== "running").length;
  const targetFraction = succeeded ? 1 : steps.length ? doneSteps / steps.length : 0;
  const completedRows = rows.filter((row, index) => index !== targetIndex && row.status === "completed").length;
  const percent = Math.round(((completedRows + targetFraction) / rows.length) * 100);

  useEffect(() => {
    dialogRef.current?.focus({ preventScroll: true });
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const caseLine = [
    assessment.reference_id ? `Case #${assessment.reference_id}` : `Assessment #${assessment.id}`,
    assessment.countries_jurisdictions,
    assessment.change_type?.replace(/_/g, " ").toLowerCase(),
  ]
    .filter(Boolean)
    .join(" • ");

  const fromLabel =
    fromStatus === "REMEDIATION" ? "Remediation" : LIFECYCLE[targetIndex - 1]?.title ?? fromStatus;
  const showEngines = fromStatus === "EVIDENCE_COLLECTION";
  const engineKnown = showEngines && succeeded;

  return (
    <div className="rim-backdrop">
      <div
        ref={dialogRef}
        className="rim"
        role="dialog"
        aria-modal="true"
        aria-labelledby="rim-title"
        aria-describedby="rim-move"
        tabIndex={-1}
      >
        <header className="rim-head">
          <h2 id="rim-title">{assessment.title}</h2>
          <span className="rim-role">{user.role}</span>
        </header>

        <div className="rim-body">
          <div className="rim-case-row">
            <span>{caseLine}</span>
            <strong>{percent}% Complete</strong>
          </div>
          <div
            className="rim-progress"
            role="progressbar"
            aria-label="Assessment lifecycle progress"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={percent}
          >
            <span style={{ width: `${percent}%` }} />
          </div>

          <p className="rim-move" id="rim-move">
            {succeeded ? "Moved" : "Moving"} from <strong>{fromLabel}</strong> to <strong>{target.title}</strong>
          </p>

          <p className="rim-scope">
            <span className="rim-role">{user.role}</span>
            {ROLE_SCOPE[user.role] ?? ""}
          </p>

          {failed && (
            <div className="rim-error" role="alert">
              {job?.error_message ?? "The stage move failed. Your assessment has been kept."}
            </div>
          )}

          <h3 className="rim-section">Pipeline Stages</h3>
          <ol className="rim-stages" aria-label="Pipeline stages">
            {rows.map((row, index) => {
              const isTarget = index === targetIndex;
              const time = isTarget ? formatTime(steps.find((step) => step.started_at)?.started_at) : null;
              return (
                <li key={row.stage} className={`rim-stage rim-stage-${row.status}${isTarget ? " rim-stage-target" : ""}`}>
                  <div className="rim-stage-main">
                    <span className="rim-stage-icon" aria-hidden="true">
                      <StatusIcon status={row.status} />
                    </span>
                    <div className="rim-stage-body">
                      <strong>{row.title}</strong>
                      <span>
                        Owner: {row.owner} • {STATUS_LABEL[row.status]}
                        {time ? ` ${time}` : ""} • Evidence: {row.evidence}
                      </span>
                    </div>
                    <span className={`rim-badge rim-badge-${row.status}`}>{STATUS_LABEL[row.status]}</span>
                  </div>

                  {isTarget && (
                    <ol className="rim-steps" aria-label={`${row.title} steps`} aria-live="polite">
                      {steps.length === 0 ? (
                        <li className="rim-step rim-step-running">
                          <span className="rim-stage-icon" aria-hidden="true">
                            <span className="rim-spinner" />
                          </span>
                          <span className="rim-step-label">Starting…</span>
                        </li>
                      ) : (
                        steps.map((step) => {
                          const stepTime = formatTime(step.finished_at ?? step.started_at);
                          return (
                            <li key={step.key} className={`rim-step rim-step-${step.status}`}>
                              <span className="rim-stage-icon" aria-hidden="true">
                                <StatusIcon status={step.status} />
                              </span>
                              <span className="rim-step-label">{step.label}</span>
                              <span className="rim-step-meta">
                                {STATUS_LABEL[step.status as RowStatus] ?? step.status}
                                {stepTime && step.status !== "queued" && step.status !== "skipped" ? ` ${stepTime}` : ""}
                              </span>
                            </li>
                          );
                        })
                      )}
                    </ol>
                  )}
                </li>
              );
            })}
          </ol>

          {engineKnown && (
            <>
              <h3 className="rim-section">AI Prediction</h3>
              {riskResults.length === 0 ? (
                <p className="rim-hint">
                  No AI prediction was produced for this run; add the risk factors manually on the next step.
                </p>
              ) : (
                <ul className="rim-prediction" aria-label="AI risk prediction">
                  {[...riskResults]
                    .sort((a, b) => b.score - a.score)
                    .map((result) => (
                      <li key={result.id} className="rim-pred-row">
                        <div className="rim-pred-head">
                          <strong>{result.dimension.replace(/_/g, " ")}</strong>
                          <span className={`rim-sev rim-sev-${result.severity.toLowerCase()}`}>
                            {result.severity} · {result.score}
                          </span>
                        </div>
                        <span className="rim-pred-reason">{result.reason}</span>
                      </li>
                    ))}
                </ul>
              )}
            </>
          )}

          {showEngines && (
            <>
              <h3 className="rim-section">Decision Engine</h3>
              <ul className="rim-engines" aria-label="Decision engine">
                {ENGINES.map((engine) => {
                  const active = engineKnown && assessment.assessment_mode === engine.mode;
                  return (
                    <li
                      key={engine.mode}
                      className={`rim-engine rim-engine-${engine.className}${active ? " active" : ""}${engineKnown && !active ? " dim" : ""}`}
                      aria-current={active ? "true" : undefined}
                    >
                      {engine.mode === "ai_assisted" && <NavIcon name="sparkle" size={12} />}
                      {engine.label}
                    </li>
                  );
                })}
              </ul>
              <p className="rim-hint">
                {engineKnown
                  ? "The engine that produced this run's risk factors."
                  : "Determined when the Risk Identification Engine finishes."}
              </p>
            </>
          )}
        </div>

        <footer className="rim-foot">
          <div className="rim-authorized">
            <strong>Authorized actions</strong>
            <span>
              {user.role} can {failed && job?.is_retryable ? "retry this stage move, " : ""}
              {running ? "continue in the background, " : ""}and observe progress.
            </span>
          </div>
          <div className="rim-actions">
            {failed && job?.is_retryable && (
              <button type="button" className="rim-btn" onClick={onRetry} disabled={retrying}>
                {retrying ? "Retrying…" : "Retry Stage Move"}
              </button>
            )}
            <button type="button" className="rim-btn" onClick={onClose}>
              {running ? "Continue in Background" : engineKnown ? "Continue" : "Close"}
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}
