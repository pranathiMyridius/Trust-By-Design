import { useEffect, useState } from "react";
import { triggerLabel } from "../api/reassessment";

import {
  formatDate,
  formatDateTime,
  getWorkQueue,
  type WorkQueue,
  type WorkQueueItem,
} from "../api/workflow";
import { SlaBadge } from "./WorkflowPanel";
import "./Workflow.css";
import { friendlyError } from "../utils/errorMessages";

/**
 * Stage 14: "given an assigned task, the responsible user sees it in
 * their work queue" and "given an overdue assessment, the system
 * displays an escalation". Lists every active assessment whose next
 * action belongs to the current user (directly assigned, or held by
 * their role/relationship and unassigned), plus anything escalated to
 * them. Ordered overdue -> at risk -> on track, then by due date.
 */

interface WorkQueuePageProps {
  onOpenAssessment: (assessmentId: number) => void;
}

function QueueTable({
  items,
  onOpen,
  showEscalation,
}: {
  items: WorkQueueItem[];
  onOpen: (id: number) => void;
  showEscalation?: boolean;
}) {
  return (
    <div className="wf-table-wrap">
      <table className="wf-table wf-queue-table">
        <thead>
          <tr>
            <th>Assessment</th>
            <th>Status</th>
            <th>{showEscalation ? "Escalation" : "Next action"}</th>
            <th>Owner / team</th>
            <th>Due</th>
            <th>SLA</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={`${item.reason}-${item.assessment_id}`} className="wf-clickable" onClick={() => onOpen(item.assessment_id)}>
              <td>
                {/* Stage 19: the row is mouse-clickable; this real button is
                    the keyboard / screen-reader way to open it. */}
                <button
                  type="button"
                  className="row-link-button"
                  onClick={(event) => {
                    event.stopPropagation();
                    onOpen(item.assessment_id);
                  }}
                >
                  <strong>{item.title}</strong>
                  <span className="sr-only"> — open assessment</span>
                </button>
                <span className="wf-sub">
                  {item.reference_id ?? `ASSESSMENT-${item.assessment_id}`}
                  {item.priority ? ` · ${item.priority}` : ""}
                </span>
              </td>
              <td>{item.workflow_status_label}</td>
              <td>{showEscalation ? item.escalation_note : item.owner.next_action}</td>
              <td>
                {item.owner.owner_name}
                <span className="wf-sub">{item.owner.team}</span>
              </td>
              <td className="wf-nowrap">
                {item.status_due_at ? formatDateTime(item.status_due_at) : "—"}
                {item.target_date && <span className="wf-sub">Target {formatDate(item.target_date)}</span>}
              </td>
              <td>
                <SlaBadge state={item.sla_state} />
                {item.escalation_level > 0 && (
                  <span className="wf-sla wf-sla-escalated" title={`Escalation level ${item.escalation_level}`}>
                    <span aria-hidden="true">⇧ </span>
                    <span className="sr-only">Escalation level </span>L{item.escalation_level}
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function WorkQueuePage({ onOpenAssessment }: WorkQueuePageProps) {
  const [queue, setQueue] = useState<WorkQueue | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  function fetchQueue() {
    return getWorkQueue().then(
      (data) => {
        setQueue(data);
        setError(null);
      },
      (err) => setError(friendlyError(err, "We couldn't load your work queue. Please try again."))
    );
  }

  function load() {
    setLoading(true);
    fetchQueue().finally(() => setLoading(false));
  }

  useEffect(() => {
    let cancelled = false;
    getWorkQueue().then(
      (data) => {
        if (!cancelled) {
          setQueue(data);
          setError(null);
        }
      },
      (err) => {
        if (!cancelled) {
          setError(friendlyError(err, "We couldn't load your work queue. Please try again."));
        }
      }
    ).finally(() => {
      if (!cancelled) {
        setLoading(false);
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="wq-page">
      <div className="page-header wq-header">
        <div>
          <h2>My Work Queue</h2>
          <p>Assessments waiting on you, and anything escalated to you.</p>
        </div>
        <button type="button" className="secondary-button" onClick={load} disabled={loading} aria-busy={loading}>
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {error && <div className="wf-banner wf-banner-danger" role="alert">{error}</div>}

      {queue && (
        <>
          <section className="stats-grid wq-stats">
            <div className="stat-card">
              <span>My tasks</span>
              <strong>{queue.tasks.length}</strong>
            </div>
            <div className={`stat-card${queue.overdue_count ? " wq-stat-danger" : ""}`}>
              <span>Overdue</span>
              <strong className={queue.overdue_count ? "wf-text-danger" : ""}>
                {queue.overdue_count > 0 && <span aria-hidden="true">⚠ </span>}
                {queue.overdue_count}
              </strong>
            </div>
            <div className={`stat-card${queue.at_risk_count ? " wq-stat-warning" : ""}`}>
              <span>At risk</span>
              <strong className={queue.at_risk_count ? "wf-text-warning" : ""}>
                {queue.at_risk_count > 0 && <span aria-hidden="true">◔ </span>}
                {queue.at_risk_count}
              </strong>
            </div>
            <div className={`stat-card${queue.escalations.length ? " wq-stat-danger" : ""}`}>
              <span>Escalated to me</span>
              <strong className={queue.escalations.length ? "wf-text-danger" : ""}>{queue.escalations.length}</strong>
            </div>
          </section>

          {queue.escalations.length > 0 && (
            <section className="content-card wf-queue-section">
              <div className="card-header wq-section-header">
                <div>
                  <h3>Escalations</h3>
                  <p>Overdue assessments escalated to you for follow-up.</p>
                </div>
                <span className="wq-count">{queue.escalations.length}</span>
              </div>
              <QueueTable items={queue.escalations} onOpen={onOpenAssessment} showEscalation />
            </section>
          )}

          {(queue.reassessment_alerts ?? []).length > 0 && (
            <section className="content-card wf-queue-section">
              <div className="card-header wq-section-header">
                <div>
                  <h3>Reassessments due</h3>
                  <p>Approved assessments with an open reassessment trigger: expired or due approvals and flagged changes.</p>
                </div>
                <span className="wq-count">{(queue.reassessment_alerts ?? []).length}</span>
              </div>
              <div className="wf-table-wrap">
              <table className="risk-table wq-table">
                <thead>
                  <tr>
                    <th scope="col">Assessment</th>
                    <th scope="col">Trigger</th>
                    <th scope="col">Review date</th>
                    <th scope="col">Detail</th>
                    <th scope="col">Next step</th>
                  </tr>
                </thead>
                <tbody>
                  {(queue.reassessment_alerts ?? []).map((alert) => (
                    <tr key={alert.trigger_id}>
                      <td>
                        <button type="button" className="doc-action-button" onClick={() => onOpenAssessment(alert.assessment_id)}>
                          {alert.reference_id ?? `#${alert.assessment_id}`}
                        </button>{" "}
                        {alert.assessment_title}
                      </td>
                      <td>
                        <span aria-hidden="true">{alert.trigger_type === "EXPIRY" ? "⚠ " : "◔ "}</span>
                        {triggerLabel(alert.trigger_type)}
                        {alert.trigger_status === "ACKNOWLEDGED" ? " (acknowledged)" : ""}
                      </td>
                      <td>{alert.next_review_date ?? "—"}</td>
                      <td>{alert.description}</td>
                      <td>
                        {alert.in_progress_reassessment_id
                          ? `Reassessment #${alert.in_progress_reassessment_id} in progress`
                          : [alert.can_start_reassessment && "Open the assessment to propose a change", alert.can_resolve && "acknowledge or dismiss the trigger"]
                              .filter(Boolean)
                              .join("; ") || "For information"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
            </section>
          )}

          {(queue.action_escalations ?? []).length > 0 && (
            <section className="content-card wf-queue-section">
              <div className="card-header wq-section-header">
                <div>
                  <h3>Escalated action items</h3>
                  <p>Overdue remediation actions on assessments you own or review (R13.3).</p>
                </div>
                <span className="wq-count">{(queue.action_escalations ?? []).length}</span>
              </div>
              <div className="wf-table-wrap">
              <table className="risk-table wq-table">
                <thead>
                  <tr>
                    <th scope="col">Assessment</th>
                    <th scope="col">Action</th>
                    <th scope="col">Owner</th>
                    <th scope="col">Priority</th>
                    <th scope="col">Due</th>
                    <th scope="col">Escalation</th>
                  </tr>
                </thead>
                <tbody>
                  {(queue.action_escalations ?? []).map((item) => (
                    <tr key={item.action_item_id}>
                      <td>
                        <button type="button" className="doc-action-button" onClick={() => onOpenAssessment(item.assessment_id)}>
                          {item.reference_id ?? `#${item.assessment_id}`}
                        </button>{" "}
                        {item.assessment_title}
                      </td>
                      <td>{item.title}</td>
                      <td>{item.owner ?? "Unassigned"}</td>
                      <td>{item.priority}</td>
                      <td>{item.due_date ?? "—"}</td>
                      <td>{item.escalation_note}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
            </section>
          )}

          <section className="content-card wf-queue-section">
            <div className="card-header wq-section-header">
              <div>
                <h3>Tasks</h3>
                <p>Assessments where you are responsible for the next action.</p>
              </div>
              <span className="wq-count">{queue.tasks.length}</span>
            </div>
            {queue.tasks.length === 0 ? (
              <div className="empty-state">
                <div className="empty-icon">✓</div>
                <h3>Nothing waiting on you right now.</h3>
                <p>You are all caught up. New tasks and escalations will appear here.</p>
              </div>
            ) : (
              <QueueTable items={queue.tasks} onOpen={onOpenAssessment} />
            )}
          </section>
        </>
      )}
    </div>
  );
}
