import { useEffect, useState, type ReactNode } from "react";
import type { Assessment } from "../api/assessments";
import {
  listAIChallengeFindings,
  submitManagerDecision,
  type AIChallengeFinding,
  type ManagerDecision,
} from "../api/assessments";
import { getLedger, getReadiness, type LedgerEntry, type Readiness } from "../api/governanceRecords";
import type { CurrentUser } from "../api/auth";
import { activeDelegationsFor, delegationCovers, listDelegations, type Delegation } from "../api/delegations";
import { friendlyError } from "../utils/errorMessages";
import { ChallengeSignoffPanel } from "./GovernanceRecordPanels";
import "./ManagerDecisionPanel.css";

// The manager's approve / return / reject controls. Shown on the
// "Submitted to Manager" stage so the manager decides next to the findings.
// The backend re-checks every rule when a decision is submitted.
export default function ManagerDecisionPanel({
  assessment,
  user,
  onDecisionRecorded,
  afterChecklist,
}: {
  assessment: Assessment;
  user: CurrentUser;
  onDecisionRecorded: () => void;
  /** Shown right after the pre-approval checklist (the challenge findings). It
   *  stays visible to everyone else when the decision controls are hidden. */
  afterChecklist?: ReactNode;
}) {
  const [delegations, setDelegations] = useState<Delegation[]>([]);
  const [comment, setComment] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Bumped after governance actions so the readiness checklist re-checks.
  const [readinessKey, setReadinessKey] = useState(0);
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [readinessError, setReadinessError] = useState(false);
  const [findings, setFindings] = useState<AIChallengeFinding[] | null>(null);
  const [overrides, setOverrides] = useState<LedgerEntry[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    getReadiness(assessment.id)
      .then((data) => {
        if (cancelled) return;
        setReadiness(data);
        setReadinessError(false);
      })
      .catch(() => {
        if (!cancelled) setReadinessError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [assessment.id, readinessKey]);

  useEffect(() => {
    listAIChallengeFindings(assessment.id)
      .then(setFindings)
      .catch(() => setFindings(null));
  }, [assessment.id]);

  useEffect(() => {
    getLedger(assessment.id)
      .then(setOverrides)
      .catch(() => setOverrides(null));
  }, [assessment.id]);

  useEffect(() => {
    listDelegations()
      .then((all) => setDelegations(activeDelegationsFor(all, user.id)))
      .catch(() => setDelegations([]));
  }, [user.id]);

  const isAssignedManager = user.role === "MANAGER" && assessment.manager_id === user.id;
  const delegation = isAssignedManager
    ? undefined
    : delegations.find((d) => d.authority === "MANAGER_APPROVAL" && delegationCovers(d, assessment));

  if (assessment.status !== "SUBMITTED_TO_MANAGER" || (!isAssignedManager && !delegation)) {
    return afterChecklist ? <>{afterChecklist}</> : null;
  }

  async function runDecision(decision: ManagerDecision) {
    setSubmitting(true);
    setError(null);
    try {
      await submitManagerDecision(assessment.id, decision, comment || undefined);
      setComment("");
      onDecisionRecorded();
    } catch (err) {
      setError(friendlyError(err, "The decision couldn't be recorded. Your comments are still here — please try again."));
      setReadinessKey((k) => k + 1);
    } finally {
      setSubmitting(false);
    }
  }

  const blocked = readiness !== null && !readiness.ready;
  const openFindings = findings?.filter((f) => f.status === "OPEN") ?? [];
  const highOpen = openFindings.filter((f) => f.severity === "HIGH").length;

  return (
    <div className="mdp">
      {delegation && (
        <p className="mdp-delegate">
          You are acting as delegate for <strong>{delegation.delegator_name}</strong> (delegation #{delegation.id}).
          Your decision is recorded under both names.
        </p>
      )}

      <div className="mdp-tiles">
        <div className="mdp-tile">
          <span className="mdp-tile-label">Residual score</span>
          <strong className="mdp-tile-value">{assessment.residual_score ?? "—"}</strong>
          <span className="mdp-tile-sub">{assessment.residual_risk_level ?? "Not calculated"}</span>
        </div>
        <div className="mdp-tile">
          <span className="mdp-tile-label">Open challenge findings</span>
          <strong className="mdp-tile-value">{findings ? openFindings.length : "—"}</strong>
          <span className="mdp-tile-sub">{findings ? `${highOpen} high severity` : "Unavailable"}</span>
        </div>
        <div className="mdp-tile">
          <span className="mdp-tile-label">Overrides</span>
          <strong className="mdp-tile-value">{overrides ? overrides.length : "—"}</strong>
          <span className="mdp-tile-sub">
            {overrides
              ? `${overrides.filter((o) => o.materiality === "MATERIAL" || o.materiality === "CRITICAL").length} material or critical`
              : "Unavailable"}
          </span>
        </div>
        <div className="mdp-tile">
          <span className="mdp-tile-label">Committee readiness</span>
          <strong className="mdp-tile-value">{readiness ? (readiness.ready ? "Ready" : "Blocked") : "—"}</strong>
          <span className="mdp-tile-sub">
            {readiness ? (readiness.ready ? "All checks passed" : `${readiness.blockers.length} to resolve`) : "Checking…"}
          </span>
        </div>
      </div>

      {/* R11: approval to committee needs the mandatory challenge sign-off. */}
      <ChallengeSignoffPanel
        assessmentId={assessment.id}
        onChanged={() => {
          setError(null);
          setReadinessKey((k) => k + 1);
        }}
      />

      {/* P3: the server's readiness result for committee submission, as a checklist. */}
      <div className="mdp-checklist" role="region" aria-label="Committee readiness checklist">
        <div className="mdp-checklist-head">
          <h3>Pre-approval checklist</h3>
          {readiness && (
            <span className="mdp-progress-text">
              {readiness.ready ? "All checks passed" : `${readiness.blockers.length} blocking`}
            </span>
          )}
        </div>
        {readiness?.ready && (
          <div className="mdp-progress" aria-hidden="true">
            <div style={{ width: "100%" }} />
          </div>
        )}
        {readinessError && (
          <p role="alert" className="mdp-error">
            Readiness couldn't be loaded.
          </p>
        )}
        {readiness?.ready && (
          <div className="mdp-row">
            <div>
              <strong>All readiness checks passed</strong>
              <span>Nothing blocks submission to the committee.</span>
            </div>
            <span className="mdp-chip mdp-chip-ok">Verified</span>
          </div>
        )}
        {readiness?.blockers.map((b) => (
          <div className="mdp-row" key={b.code}>
            <div>
              <strong>{b.message}</strong>
              <span>
                Next: {b.next_action} · Responsible: {b.responsible_role}
              </span>
            </div>
            <span className="mdp-chip mdp-chip-bad">Blocked</span>
          </div>
        ))}
      </div>

      {afterChecklist}

      <label className="mdp-label" htmlFor={`mdp-comment-${assessment.id}`}>
        Required manager audit remarks <em>(required to return or reject)</em>
      </label>
      <textarea
        id={`mdp-comment-${assessment.id}`}
        placeholder="Enter your decision rationale…"
        value={comment}
        onChange={(event) => setComment(event.target.value)}
        rows={3}
      />
      <div className="mdp-actions">
        <button
          className="mdp-btn mdp-approve"
          aria-label="Approve"
          disabled={submitting || blocked}
          onClick={() => runDecision("approve")}
        >
          Approve &amp; Advance
        </button>
        <button className="mdp-btn mdp-return" disabled={submitting} onClick={() => runDecision("return")}>
          Return for Revision
        </button>
        <button
          className="mdp-btn mdp-reject"
          aria-label="Reject"
          disabled={submitting}
          onClick={() => runDecision("reject")}
        >
          Reject Entirely
        </button>
        {blocked && <span className="mdp-hint">Approve is blocked until the checklist is complete.</span>}
      </div>
      {error && (
        <p role="alert" className="mdp-error">
          {error}
        </p>
      )}

      <div className="mdp-rbac">
        <h4>Role controls</h4>
        <ul>
          <li>MANAGER: approve, return or reject once the review gates pass, with a rationale to return or reject.</li>
          <li>COMMITTEE_MEMBER: votes Approve, Dissent or Abstain and records the final decision; quorum and recusal are enforced by the server.</li>
          <li>BUSINESS_USER and FCRM_ANALYST: author and resubmit the package; no approval or vote controls.</li>
          <li>ADMIN: inspect permissions and audit activity; no business decision controls.</li>
        </ul>
      </div>
    </div>
  );
}
