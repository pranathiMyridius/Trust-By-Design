import { useEffect, useState } from "react";

import type { Assessment } from "../api/assessments";
import type { CurrentUser } from "../api/auth";
import {
  assignCurrentTask,
  closeAssessment,
  getAssessmentWorkflow,
  getAssignableUsers,
  openCommitteeReview,
  setTargetDate,
  submitDraft,
  withdrawAssessment,
  workflowLabel,
  formatDate,
  formatDateTime,
  SLA_LABELS,
  type AssignableUser,
  type WorkflowSummary,
} from "../api/workflow";
import "./Workflow.css";
import { friendlyError } from "../utils/errorMessages";

/**
 * Stage 14: the assessment's lifecycle at a glance -- status, current
 * owner / team / next action (R14.3), due dates and SLA (R14.4), any
 * escalation, what's blocking committee review, the permitted next
 * statuses (R14.1/R14.2), the lifecycle-only actions the current user
 * can take, and the full workflow history (R14.5).
 *
 * Stage-specific moves (advance stage, manager/committee decisions,
 * information requests) stay on their own panels further down; this
 * panel reloads whenever the assessment's status changes so it always
 * reflects them.
 */

interface WorkflowPanelProps {
  assessment: Assessment;
  user: CurrentUser;
  onAssessmentChanged: (updated: Assessment) => void;
  /** R1.3: reopen a saved draft in the intake form to finish it. */
  onEditDraft?: () => void;
}

type PendingAction = "withdraw" | "close" | "open_committee_review" | "target" | "assign" | null;

// Stage 19: SLA state is conveyed by an icon + text, not colour alone.
const SLA_ICONS: Record<string, string> = {
  ON_TRACK: "✓",
  AT_RISK: "◔",
  OVERDUE: "⚠",
};

export function SlaBadge({ state }: { state: string | null | undefined }) {
  if (!state || state === "NONE") {
    return null;
  }
  const label = SLA_LABELS[state as keyof typeof SLA_LABELS] ?? state;
  return (
    <span className={`wf-sla wf-sla-${state.toLowerCase()}`} title={`SLA: ${label}`}>
      {SLA_ICONS[state] && <span aria-hidden="true">{SLA_ICONS[state]} </span>}
      <span className="sr-only">SLA: </span>
      {label}
    </span>
  );
}

export default function WorkflowPanel({ assessment, user, onAssessmentChanged, onEditDraft }: WorkflowPanelProps) {
  const [summary, setSummary] = useState<WorkflowSummary | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<PendingAction>(null);
  const [reason, setReason] = useState("");
  const [targetDraft, setTargetDraft] = useState("");
  const [assignees, setAssignees] = useState<AssignableUser[]>([]);
  const [assigneeId, setAssigneeId] = useState<string>("");

  function reload() {
    return getAssessmentWorkflow(assessment.id).then(
      (data) => {
        setSummary(data);
        setLoadError(null);
      },
      (error) => setLoadError(friendlyError(error, "The workflow details couldn't be loaded. Please refresh the page."))
    );
  }

  // Re-fetch whenever anything elsewhere on the page moves the status.
  useEffect(() => {
    let cancelled = false;
    getAssessmentWorkflow(assessment.id).then(
      (data) => {
        if (!cancelled) {
          setSummary(data);
          setLoadError(null);
        }
      },
      (error) => {
        if (!cancelled) {
          setLoadError(friendlyError(error, "The workflow details couldn't be loaded. Please refresh the page."));
        }
      }
    );
    return () => {
      cancelled = true;
    };
  }, [assessment.id, assessment.status, assessment.workflow_status, assessment.updated_at]);

  function startAction(action: PendingAction) {
    setActionError(null);
    setReason("");
    setPending(action);
    if (action === "target") {
      setTargetDraft(summary?.target_date ?? "");
    }
    if (action === "assign") {
      setAssigneeId(summary?.current_assignee_id ? String(summary.current_assignee_id) : "");
      getAssignableUsers(assessment.id)
        .then(setAssignees)
        .catch((error) => setActionError(friendlyError(error, "The list of people to assign couldn't be loaded. Please try again.")));
    }
  }

  async function run(work: () => Promise<Assessment | WorkflowSummary | void>) {
    setBusy(true);
    setActionError(null);
    try {
      const result = await work();
      if (result && "workflow_status" in result && "change_type" in result) {
        onAssessmentChanged(result as Assessment);
      }
      setPending(null);
      setReason("");
      await reload();
    } catch (error) {
      setActionError(friendlyError(error, "That workflow action couldn't be completed. Please try again."));
    } finally {
      setBusy(false);
    }
  }

  if (loadError && !summary) {
    return <div className="wf-panel wf-error" role="alert">Workflow unavailable: {loadError}</div>;
  }

  if (!summary) {
    return <div className="wf-panel wf-muted" role="status" aria-live="polite">Loading workflow…</div>;
  }

  const actions = new Set(summary.available_actions);
  const canClaim =
    actions.has("assign") &&
    summary.current_assignee_id !== user.id &&
    (summary.owner.party === "FCRM_ANALYST"
      ? ["FCRM_ANALYST", "MANAGER", "ADMIN"].includes(user.role)
      : summary.owner.party === "COMMITTEE"
        ? ["COMMITTEE_MEMBER", "ADMIN"].includes(user.role)
        : false);
  const isClosed = summary.workflow_status === "CLOSED";

  return (
    <section className="wf-panel" aria-label="Workflow status">
      <div className="wf-head">
        <div className="wf-status-block">
          <span className="wf-eyebrow">Workflow status</span>
          <div className="wf-status-line">
            <span className={`wf-status wf-status-${summary.workflow_status.toLowerCase()}`}>
              {summary.workflow_status_label}
            </span>
            <SlaBadge state={summary.sla_state} />
            {summary.escalation_level > 0 && (
              <span className="wf-sla wf-sla-escalated">
                <span aria-hidden="true">⇧ </span>Escalated · L{summary.escalation_level}
              </span>
            )}
          </div>
          <span className="wf-muted wf-small">Pipeline stage: {summary.status}</span>
        </div>

        <div className="wf-actions">
          {actions.has("submit") && onEditDraft && (
            <button className="secondary-button" disabled={busy} onClick={onEditDraft}>
              Edit draft
            </button>
          )}
          {actions.has("submit") && (
            <button className="primary-button" disabled={busy} onClick={() => run(() => submitDraft(assessment.id))}>
              Submit request
            </button>
          )}
          {actions.has("open_committee_review") && (
            <button className="primary-button" disabled={busy} onClick={() => startAction("open_committee_review")}>
              Open committee review
            </button>
          )}
          {actions.has("close") && (
            <button className="primary-button" disabled={busy} onClick={() => startAction("close")}>
              Close assessment
            </button>
          )}
          {canClaim && (
            <button
              className="secondary-button"
              disabled={busy}
              onClick={() => run(() => assignCurrentTask(assessment.id, user.id, "Claimed from queue"))}
            >
              Claim task
            </button>
          )}
          {actions.has("assign") && (
            <button className="secondary-button" disabled={busy} onClick={() => startAction("assign")}>
              Assign…
            </button>
          )}
          {actions.has("set_target_date") && (
            <button className="secondary-button" disabled={busy} onClick={() => startAction("target")}>
              Target date…
            </button>
          )}
          {actions.has("withdraw") && (
            <button className="secondary-button wf-danger" disabled={busy} onClick={() => startAction("withdraw")}>
              Withdraw
            </button>
          )}
        </div>
      </div>

      <dl className="wf-grid wf-grid-compact">
        <div>
          <dt>Current owner</dt>
          <dd>{summary.owner.owner_name}</dd>
        </div>
        <div className="wf-grid-wide">
          <dt>Next action</dt>
          <dd>{summary.owner.next_action}</dd>
        </div>
        <div>
          <dt>Status due{summary.sla_days ? ` (${summary.sla_days}-day SLA)` : ""}</dt>
          <dd>{summary.status_due_at ? formatDateTime(summary.status_due_at) : "No SLA"}</dd>
        </div>
      </dl>

      {summary.escalation_level > 0 && summary.escalation_note && (
        <div className="wf-banner wf-banner-danger" role="alert">
          <strong>Escalated{summary.escalated_to_name ? ` to ${summary.escalated_to_name}` : ""}.</strong>{" "}
          {summary.escalation_note}
        </div>
      )}

      {summary.mandatory_issues.length > 0 && (
        <div className="wf-banner wf-banner-warning" role="status">
          <strong><span aria-hidden="true">⚠ </span>Blocking committee review:</strong>
          <ul>
            {summary.mandatory_issues.map((issue) => (
              <li key={issue}>{issue}</li>
            ))}
          </ul>
        </div>
      )}

      {pending && (
        <div className="wf-form">
          {pending === "assign" ? (
            <>
              <label htmlFor="wf-assignee">
                Assign the current task ({workflowLabel(summary.workflow_status)})
              </label>
              <select id="wf-assignee" value={assigneeId} onChange={(event) => setAssigneeId(event.target.value)}>
                <option value="">Unassigned (shared queue)</option>
                {assignees.map((candidate) => (
                  <option key={candidate.id} value={candidate.id}>
                    {candidate.full_name ?? candidate.email} · {candidate.role}
                  </option>
                ))}
              </select>
            </>
          ) : pending === "target" ? (
            <>
              <label htmlFor="wf-target">Target completion date</label>
              <input id="wf-target" type="date" value={targetDraft} onChange={(event) => setTargetDraft(event.target.value)} />
            </>
          ) : (
            <p className="wf-form-title">
              {pending === "withdraw"
                ? "Withdraw this request? It will be closed and cannot be reopened."
                : pending === "close"
                  ? "Close this assessment? Closed assessments are read-only."
                  : "Open committee review for this assessment."}
            </p>
          )}

          <label htmlFor="wf-reason">
            Reason{pending === "assign" || pending === "open_committee_review" ? " (optional)" : ""}
          </label>
          <textarea id="wf-reason" rows={2} value={reason} onChange={(event) => setReason(event.target.value)} />

          <div className="wf-form-buttons">
            <button className="secondary-button" disabled={busy} onClick={() => setPending(null)}>
              Cancel
            </button>
            <button
              className="primary-button"
              disabled={
                busy ||
                ((pending === "withdraw" || pending === "close" || pending === "target") && !reason.trim()) ||
                (pending === "target" && !targetDraft)
              }
              onClick={() =>
                run(() => {
                  switch (pending) {
                    case "withdraw":
                      return withdrawAssessment(assessment.id, reason);
                    case "close":
                      return closeAssessment(assessment.id, reason);
                    case "open_committee_review":
                      return openCommitteeReview(assessment.id, reason);
                    case "target":
                      return setTargetDate(assessment.id, targetDraft, reason);
                    case "assign":
                      return assignCurrentTask(assessment.id, assigneeId ? Number(assigneeId) : null, reason);
                    default:
                      return Promise.resolve();
                  }
                })
              }
            >
              {busy ? "Saving…" : "Confirm"}
            </button>
          </div>
        </div>
      )}

      {actionError && <div className="wf-banner wf-banner-danger" role="alert">{actionError}</div>}

      <details className="wf-more">
        <summary>More details</summary>
        <dl className="wf-grid">
          <div>
            <dt>Team</dt>
            <dd>{summary.owner.team}</dd>
          </div>
          <div>
            <dt>In this status since</dt>
            <dd>{formatDateTime(summary.status_entered_at)}</dd>
          </div>
          <div>
            <dt>Target completion</dt>
            <dd>{formatDate(summary.target_date)}</dd>
          </div>
          <div>
            <dt>Priority</dt>
            <dd>{summary.priority ?? "—"}</dd>
          </div>
        </dl>
        {!isClosed && summary.available_transitions.length > 0 && (
          <div className="wf-next">
            <span className="wf-eyebrow">Permitted next statuses</span>
            <div className="wf-chips">
              {summary.available_transitions.map((next) => (
                <span
                  key={next.to_status}
                  className={`wf-chip ${next.allowed_for_user ? "" : "wf-chip-locked"}`}
                  title={
                    next.allowed_for_user
                      ? `You can make this move (${next.to_status})`
                      : `Requires: ${next.roles.join(", ")}`
                  }
                >
                  {next.to_workflow_label}
                  {!next.allowed_for_user && (
                    <>
                      <span aria-hidden="true"> 🔒</span>
                      <span className="sr-only"> (locked — requires {next.roles.join(", ")})</span>
                    </>
                  )}
                </span>
              ))}
            </div>
          </div>
        )}
      </details>

      {/* R14.5: every status change -- previous and new status, user, time, reason. */}
      <details className="wf-history">
        <summary>Workflow history ({summary.history.length})</summary>
        {summary.history.length === 0 ? (
          <p className="wf-history-empty">No status changes recorded yet.</p>
        ) : (
          <div className="wf-history-scroll">
            <table className="risk-table">
              <thead>
                <tr>
                  <th scope="col">When</th>
                  <th scope="col">From</th>
                  <th scope="col">To</th>
                  <th scope="col">By</th>
                  <th scope="col">Reason</th>
                </tr>
              </thead>
              <tbody>
                {[...summary.history]
                  .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
                  .map((entry) => (
                    <tr key={entry.id}>
                      <td>{formatDateTime(entry.created_at)}</td>
                      <td>{entry.from_workflow_status ? workflowLabel(entry.from_workflow_status) : "—"}</td>
                      <td>{workflowLabel(entry.to_workflow_status)}</td>
                      <td>{entry.actor}</td>
                      <td>{entry.reason}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        )}
      </details>
    </section>
  );
}
