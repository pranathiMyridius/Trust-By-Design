import { useEffect, useState } from "react";
import type { Assessment } from "../api/assessments";
import {
  submitCommitteeDecision,
  getDecisionPackage,
  castCommitteeVote,
  type CommitteeDecisionOption,
  type CommitteeConditionInput,
  type DecisionPackage,
} from "../api/assessments";
import type { CurrentUser } from "../api/auth";
import type { Delegation } from "../api/delegations";
import { friendlyError } from "../utils/errorMessages";
import DecisionPackageView from "./DecisionPackageView";
import { VoteHistoryList } from "./GovernanceRecordPanels";
import { getReadiness, getVoteHistory, type Readiness, type VoteRecord } from "../api/governanceRecords";
import "./ManagerDecisionPanel.css";
import "./CommitteeDecisionPanel.css";

const VOTE_LABEL: Record<VoteRecord["vote"], string> = {
  APPROVE: "Approve",
  DISSENT: "Dissent",
  ABSTAIN: "Abstain",
};
const VOTE_CHIP: Record<VoteRecord["vote"], string> = {
  APPROVE: "mdp-chip-ok",
  DISSENT: "mdp-chip-bad",
  ABSTAIN: "cdp-chip-neutral",
};

const EMPTY_CONDITION: CommitteeConditionInput = { description: "", owner: "", due_date: "", priority: "MEDIUM" };

// The committee's side of the decision: individual member votes (R12.6),
// then the final decision with its rationale and conditions. The backend
// re-checks every rule (quorum, recusal, readiness) when either is submitted.
export default function CommitteeDecisionPanel({
  assessment,
  user,
  delegation,
  onDecisionRecorded,
}: {
  assessment: Assessment;
  user: CurrentUser;
  delegation?: Delegation;
  onDecisionRecorded: () => void;
}) {
  const [rationale, setRationale] = useState("");
  // Conditions the committee has added so far, and the one being typed.
  const [conditions, setConditions] = useState<CommitteeConditionInput[]>([]);
  const [draft, setDraft] = useState<CommitteeConditionInput>({ ...EMPTY_CONDITION });
  const [draftError, setDraftError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // P3: bumped after a failed decision so the readiness checklist re-checks.
  const [readinessKey, setReadinessKey] = useState(0);
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [readinessError, setReadinessError] = useState(false);
  // Stage 19: field-level flag for the required committee rationale.
  const [rationaleMissing, setRationaleMissing] = useState(false);

  // R12.2: the decision package, shown to the committee before they act.
  const [showPackage, setShowPackage] = useState(false);
  const [decisionPackage, setDecisionPackage] = useState<DecisionPackage | null>(null);
  const [packageLoading, setPackageLoading] = useState(false);

  // R12.6: append-only votes, superseded ones included.
  const [votes, setVotes] = useState<VoteRecord[]>([]);
  const [voteComment, setVoteComment] = useState("");
  const [recastReason, setRecastReason] = useState("");
  const [recastReasonMissing, setRecastReasonMissing] = useState(false);
  const [votingLoading, setVotingLoading] = useState(false);

  useEffect(() => {
    getVoteHistory(assessment.id)
      .then(setVotes)
      .catch(() => setVotes([]));
  }, [assessment.id]);

  useEffect(() => {
    let cancelled = false;
    getReadiness(assessment.id, "FINAL_DECISION")
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

  // The seat this user votes in: their own, or the delegator's (AW.7).
  const seatId = delegation ? delegation.delegator_id : user.id;
  const currentVotes = votes.filter((v) => v.is_current);
  const seatVote = currentVotes.find((v) => v.member_id === seatId);
  const tally = {
    APPROVE: currentVotes.filter((v) => v.vote === "APPROVE").length,
    DISSENT: currentVotes.filter((v) => v.vote === "DISSENT").length,
    ABSTAIN: currentVotes.filter((v) => v.vote === "ABSTAIN").length,
  };
  const blocked = readiness !== null && !readiness.ready;
  const filledConditions = conditions;

  async function toggleDecisionPackage() {
    if (showPackage) {
      setShowPackage(false);
      return;
    }
    setShowPackage(true);
    if (!decisionPackage) {
      setPackageLoading(true);
      try {
        setDecisionPackage(await getDecisionPackage(assessment.id));
      } catch (err) {
        setError(friendlyError(err, "The decision package couldn't be loaded. Please try again."));
      } finally {
        setPackageLoading(false);
      }
    }
  }

  async function runCastVote(vote: VoteRecord["vote"]) {
    if (seatVote && !recastReason.trim()) {
      setRecastReasonMissing(true);
      setError("Give a reason for changing the vote — the earlier vote stays on record.");
      return;
    }
    setRecastReasonMissing(false);
    setVotingLoading(true);
    setError(null);
    try {
      await castCommitteeVote(assessment.id, vote, voteComment || undefined, seatVote ? recastReason.trim() : undefined);
      setVotes(await getVoteHistory(assessment.id));
      setRecastReason("");
      setVoteComment("");
      setReadinessKey((k) => k + 1);
    } catch (err) {
      setError(friendlyError(err, "Your vote couldn't be recorded. Please try again."));
    } finally {
      setVotingLoading(false);
    }
  }

  function addCondition() {
    if (!draft.description.trim() || !draft.owner.trim() || !draft.due_date) {
      setDraftError("A condition needs a description, an owner and a due date.");
      return;
    }
    setDraftError(null);
    setConditions((current) => [...current, { ...draft, description: draft.description.trim(), owner: draft.owner.trim() }]);
    setDraft({ ...EMPTY_CONDITION });
  }

  async function runCommitteeDecision(decision: CommitteeDecisionOption) {
    if (!rationale.trim()) {
      setRationaleMissing(true);
      setError("A decision rationale is required.");
      return;
    }
    setRationaleMissing(false);

    if (decision === "approve_with_conditions" && filledConditions.length === 0) {
      setError(
        "At least one condition, with a description, owner, and due date, is required when approving with conditions."
      );
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      await submitCommitteeDecision(
        assessment.id,
        decision,
        rationale,
        decision === "approve_with_conditions" ? filledConditions : undefined
      );
      onDecisionRecorded();
    } catch (err) {
      setError(friendlyError(err, "The decision couldn't be recorded. Your comments are still here — please try again."));
      setReadinessKey((k) => k + 1);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="mdp cdp">
      {delegation && (
        <p className="mdp-delegate">
          You are acting as delegate for <strong>{delegation.delegator_name}</strong> (delegation #{delegation.id}, until{" "}
          {new Date(delegation.end_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}). Your
          decision is recorded under both names.
        </p>
      )}

      <div className="mdp-tiles">
        <div className="mdp-tile">
          <span className="mdp-tile-label">Residual score</span>
          <strong className="mdp-tile-value">{assessment.residual_score ?? "—"}</strong>
          <span className="mdp-tile-sub">{assessment.residual_risk_level ?? "Not calculated"}</span>
        </div>
        <div className="mdp-tile">
          <span className="mdp-tile-label">Current votes</span>
          <strong className="mdp-tile-value">{currentVotes.length}</strong>
          <span className="mdp-tile-sub">
            {tally.APPROVE} approve · {tally.DISSENT} dissent · {tally.ABSTAIN} abstain
          </span>
        </div>
        <div className="mdp-tile">
          <span className="mdp-tile-label">Final decision readiness</span>
          <strong className="mdp-tile-value">{readiness ? (readiness.ready ? "Ready" : "Blocked") : "—"}</strong>
          <span className="mdp-tile-sub">
            {readiness ? (readiness.ready ? "All checks passed" : `${readiness.blockers.length} to resolve`) : "Checking…"}
          </span>
        </div>
      </div>

      <div>
        <button type="button" className="cdp-link" onClick={toggleDecisionPackage}>
          {showPackage ? "Hide decision package" : "View decision package"}
        </button>
        {showPackage && (
          <div className="cdp-package">
            {packageLoading ? (
              <p role="status">Loading decision package...</p>
            ) : decisionPackage ? (
              <DecisionPackageView pkg={decisionPackage} />
            ) : (
              <p role="alert">The decision package couldn't be loaded. Close and reopen it to try again.</p>
            )}
          </div>
        )}
      </div>

      {/* P3: what still blocks the final decision, from the server. */}
      <div className="mdp-checklist" role="region" aria-label="Final decision checklist">
        <div className="mdp-checklist-head">
          <h3>Final decision checklist</h3>
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
              <span>Nothing blocks the final decision.</span>
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

      <section className="cdp-section" aria-label="Committee voting">
        <div className="cdp-section-head">
          <h3>Committee votes</h3>
          <span className="mdp-progress-text">
            Current tally: {tally.APPROVE} Approve, {tally.ABSTAIN} Abstain, {tally.DISSENT} Dissent
          </span>
        </div>
        {currentVotes.length === 0 ? (
          <p className="workflow-empty">No votes recorded yet.</p>
        ) : (
          <div className="cdp-members">
            {currentVotes.map((v) => (
              <div className="cdp-member" key={v.id}>
                <span className="cdp-avatar" aria-hidden="true">
                  {(v.member_name ?? "?").trim().charAt(0).toUpperCase()}
                </span>
                <div>
                  <strong>{v.member_name ?? `Member ${v.member_id}`}</strong>
                  <span>
                    {v.delegate_name ? `Via delegate ${v.delegate_name}` : "Committee member"}
                    {v.comment ? ` · ${v.comment}` : ""}
                  </span>
                </div>
                <span className={`mdp-chip ${VOTE_CHIP[v.vote]}`}>{VOTE_LABEL[v.vote]}</span>
              </div>
            ))}
          </div>
        )}

        {seatVote && (
          <>
            <p className="cdp-note">
              This seat has voted <strong>{VOTE_LABEL[seatVote.vote]}</strong>. A new vote replaces it as current; the
              earlier vote stays on record.
            </p>
            <textarea
              aria-label="Reason for changing the vote (required)"
              aria-required="true"
              aria-invalid={recastReasonMissing || undefined}
              placeholder="Reason for changing the vote (required)"
              value={recastReason}
              onChange={(event) => {
                setRecastReason(event.target.value);
                if (event.target.value.trim()) setRecastReasonMissing(false);
              }}
              rows={1}
            />
          </>
        )}
        <textarea
          aria-label="Optional comment for your vote"
          placeholder="Optional comment for your vote"
          value={voteComment}
          onChange={(event) => setVoteComment(event.target.value)}
          rows={1}
        />
        <div className="mdp-actions">
          <button className="mdp-btn mdp-approve" disabled={votingLoading} onClick={() => runCastVote("APPROVE")}>
            Approve
          </button>
          <button className="mdp-btn mdp-reject" disabled={votingLoading} onClick={() => runCastVote("DISSENT")}>
            Dissent
          </button>
          <button className="mdp-btn cdp-abstain" disabled={votingLoading} onClick={() => runCastVote("ABSTAIN")}>
            Abstain
          </button>
        </div>
        {votes.length > 0 && (
          <details className="cdp-history">
            <summary>Vote history ({votes.length})</summary>
            <VoteHistoryList votes={votes} />
          </details>
        )}
      </section>

      <section className="cdp-section" aria-label="Final decision">
        <div className="cdp-section-head">
          <h3>Final verdict &amp; condition builder</h3>
        </div>
        <label className="mdp-label" htmlFor={`cdp-rationale-${assessment.id}`}>
          Decision rationale <em>(required)</em>
        </label>
        <textarea
          id={`cdp-rationale-${assessment.id}`}
          aria-required="true"
          aria-invalid={rationaleMissing || undefined}
          aria-describedby={rationaleMissing ? `rationale-error-${assessment.id}` : undefined}
          placeholder="Enter the committee's decision rationale…"
          value={rationale}
          onChange={(event) => {
            setRationale(event.target.value);
            if (event.target.value.trim()) setRationaleMissing(false);
          }}
          rows={3}
        />
        {rationaleMissing && (
          <span id={`rationale-error-${assessment.id}`} className="field-error">
            <span aria-hidden="true">⚠</span> Enter a decision rationale before recording the decision.
          </span>
        )}

        <div className="cdp-conditions">
          <div className="cdp-section-head">
            <h4>Condition builder</h4>
            <span className="mdp-progress-text">Needed only for Approve with Conditions</span>
          </div>
          <div className="cdp-condition">
            <input
              aria-label="New condition description"
              placeholder="Type a new committee stipulation/condition…"
              value={draft.description}
              onChange={(event) => setDraft({ ...draft, description: event.target.value })}
              className="cdp-grow"
            />
            <input
              aria-label="New condition owner"
              placeholder="Owner"
              value={draft.owner}
              onChange={(event) => setDraft({ ...draft, owner: event.target.value })}
            />
            <input
              type="date"
              aria-label="New condition due date"
              value={draft.due_date}
              onChange={(event) => setDraft({ ...draft, due_date: event.target.value })}
            />
            <select
              aria-label="New condition priority"
              value={draft.priority}
              onChange={(event) => setDraft({ ...draft, priority: event.target.value as CommitteeConditionInput["priority"] })}
            >
              <option value="LOW">Low</option>
              <option value="MEDIUM">Medium</option>
              <option value="HIGH">High</option>
              <option value="CRITICAL">Critical</option>
            </select>
            <button type="button" className="cdp-add" onClick={addCondition}>
              Add
            </button>
          </div>
          {draftError && (
            <p role="alert" className="mdp-error">
              {draftError}
            </p>
          )}
          {conditions.length > 0 && (
            <>
              <span className="cdp-caption">Committed attached conditions</span>
              {conditions.map((condition, index) => (
                <div className="cdp-committed" key={index}>
                  <span>
                    {condition.description}
                    <em>
                      {" "}
                      — {condition.owner}, due {condition.due_date}, {condition.priority.toLowerCase()} priority
                    </em>
                  </span>
                  <button
                    type="button"
                    className="cdp-remove"
                    aria-label={`Remove condition ${index + 1}`}
                    onClick={() => setConditions((current) => current.filter((_, i) => i !== index))}
                  >
                    ✕
                  </button>
                </div>
              ))}
            </>
          )}
        </div>

        <div className="mdp-actions">
          <button className="mdp-btn mdp-approve" disabled={submitting || blocked} onClick={() => runCommitteeDecision("approve")}>
            Approve
          </button>
          <button
            className="mdp-btn cdp-conditional"
            disabled={submitting || blocked}
            onClick={() => runCommitteeDecision("approve_with_conditions")}
          >
            Approve with Conditions
          </button>
          <button className="mdp-btn mdp-return" disabled={submitting} onClick={() => runCommitteeDecision("defer")}>
            Defer
          </button>
          <button className="mdp-btn mdp-reject" disabled={submitting} onClick={() => runCommitteeDecision("reject")}>
            Reject (reason required)
          </button>
          {blocked && <span className="mdp-hint">Approval is blocked until the checklist is complete.</span>}
        </div>
      </section>

      {error && (
        <p role="alert" className="mdp-error">
          {error}
        </p>
      )}

      <div className="mdp-rbac">
        <h4>Committee controls</h4>
        <ul>
          <li>Members vote Approve, Dissent or Abstain; a changed vote needs a reason and the earlier vote stays on record.</li>
          <li>Quorum, recusal and conflict rules are checked by the server and shown in the checklist above.</li>
          <li>The final decision needs a rationale; approving with conditions needs at least one condition.</li>
        </ul>
      </div>
    </div>
  );
}
