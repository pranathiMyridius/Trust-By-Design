import { useEffect, useState } from "react";
import { getAmendmentStatus, type Assessment, type AmendmentStatus } from "../api/assessments";
import type { CurrentUser } from "../api/auth";

interface ReturnedByManagerPanelProps {
  assessment: Assessment;
  user: CurrentUser;
  onResubmit: () => Promise<void>;
  // Opens the request form to correct the request details (owner only).
  onEditRequest?: () => void;
  submitting: boolean;
  error: string | null;
}

// Shown on the approval stage (the "Amendment" node) while the manager has
// returned the assessment for correction (workflow label "Amendment
// Required"). It stays with the business owner, who reads the manager's
// comment, corrects what was asked and resubmits. Nothing is re-run while
// they work. On resubmit the server decides: if documents or request details
// changed since the return, the assessment goes back through analysis;
// otherwise straight back to the same manager. Only the owner can resubmit.
export default function ReturnedByManagerPanel({
  assessment,
  user,
  onResubmit,
  onEditRequest,
  submitting,
  error,
}: ReturnedByManagerPanelProps) {
  const [amendment, setAmendment] = useState<AmendmentStatus | null>(null);
  const returned = assessment.status === "RETURNED_BY_MANAGER";

  useEffect(() => {
    if (!returned) return;
    getAmendmentStatus(assessment.id).then(setAmendment).catch(() => setAmendment(null));
  }, [assessment.id, returned]);

  if (!returned) return null;

  const isOwner = assessment.owner_id === user.id;
  const reanalysis = amendment?.requires_reanalysis === true;
  const returnedAt = assessment.manager_decided_at
    ? new Date(assessment.manager_decided_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })
    : null;

  return (
    <section className="workflow-card">
      <div className="workflow-card-header">
        <div>
          <h2>Amendment — returned by your manager</h2>
          <p>
            {isOwner
              ? "Your manager has sent this assessment back. Read the feedback, make the corrections, then resubmit. Nothing is re-run while you work."
              : "The manager has sent this assessment back to its owner for correction."}
          </p>
        </div>
        <span className="challenge-badge">AMENDMENT REQUIRED</span>
      </div>

      <div style={{ borderLeft: "3px solid #f79009", paddingLeft: 12, marginBottom: 12 }}>
        <strong>Manager's feedback{returnedAt ? ` (${returnedAt})` : ""}</strong>
        <p style={{ margin: "4px 0 0", whiteSpace: "pre-wrap" }}>
          {assessment.manager_comment || "No comment was recorded."}
        </p>
      </div>

      <p style={{ margin: "0 0 12px", fontSize: 13, color: "#667085" }}>
        The earlier challenge-review sign-off no longer applies; it will need to be done again. You can look back at
        earlier stages from the stage tracker above. The owner can correct the request details, upload or replace
        documents (Evidence stage) and re-confirm the business profile (Intake); risk, control and review changes are
        made by the FCRM analyst.
      </p>

      {amendment && amendment.changes.length > 0 && (
        <div style={{ marginBottom: 12 }}>
          <strong style={{ fontSize: 13 }}>Changed since the return</strong>
          <ul style={{ margin: "4px 0 0", paddingLeft: 18, fontSize: 13 }}>
            {amendment.changes.map((change, index) => (
              <li key={index}>{change.description}</li>
            ))}
          </ul>
        </div>
      )}

      {reanalysis && (
        <p role="note" style={{ margin: "0 0 12px", padding: "8px 10px", borderRadius: 6, background: "#fffaeb", color: "#93370d", fontSize: 13 }}>
          Documents or request details have changed since the return, so resubmitting will send this assessment back
          through analysis (Evidence → Risk → Controls → Review) before it returns to your manager. The earlier results
          are kept as history.
        </p>
      )}

      {isOwner ? (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {onEditRequest && (
            <button className="secondary-button" onClick={onEditRequest} disabled={submitting}>
              Edit request details
            </button>
          )}
          <button className="primary-button" onClick={() => void onResubmit()} disabled={submitting}>
            {submitting ? "Resubmitting…" : reanalysis ? "Resubmit — send back through analysis" : "Resubmit to Manager"}
          </button>
        </div>
      ) : (
        <p style={{ margin: 0, color: "#667085" }}>Only the assessment owner can resubmit it.</p>
      )}

      {error && (
        <p role="alert" style={{ marginTop: 8, color: "#b91c1c" }}>
          {error}
        </p>
      )}
    </section>
  );
}

// The manager's feedback, kept in view while the assessment is walked through
// analysis again after a return (Evidence Collection up to Human Review).
const REANALYSIS_STATUSES = new Set([
  "EVIDENCE_COLLECTION",
  "RISK_IDENTIFICATION",
  "INHERENT_RISK_ASSESSMENT",
  "CONTROL_ASSESSMENT",
  "RESIDUAL_RISK",
  "HUMAN_REVIEW",
]);

export function ManagerFeedbackBanner({ assessment }: { assessment: Assessment }) {
  if (assessment.manager_decision !== "RETURN" || !REANALYSIS_STATUSES.has(assessment.status)) return null;
  return (
    <section className="workflow-card" role="note">
      <strong>Returned by manager — being re-analysed</strong>
      <p style={{ margin: "4px 0 0", whiteSpace: "pre-wrap" }}>
        {assessment.manager_comment || "No comment was recorded."}
      </p>
    </section>
  );
}
