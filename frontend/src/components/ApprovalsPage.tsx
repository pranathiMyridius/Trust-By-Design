import { useEffect, useState } from "react";
import type { Assessment } from "../api/assessments";
import {
  submitManagerDecision,
  submitCommitteeDecision,
  getDecisionPackage,
  castCommitteeVote,
  type ManagerDecision,
  type CommitteeDecisionOption,
  type CommitteeConditionInput,
  type DecisionPackage,
} from "../api/assessments";
import type { CurrentUser } from "../api/auth";
import { activeDelegationsFor, listDelegations, type Delegation } from "../api/delegations";
import RiskLevelBadge from "./RiskLevelBadge";
import { friendlyError } from "../utils/errorMessages";
import DecisionPackageView from "./DecisionPackageView";
import { ChallengeSignoffPanel, CommitteeReadinessPanel, VoteHistoryList } from "./GovernanceRecordPanels";
import { getVoteHistory, type VoteRecord } from "../api/governanceRecords";

interface ApprovalsPageProps {
  user: CurrentUser;
  assessments: Assessment[];
  onSelectAssessment: (assessment: Assessment) => void;
  onDecisionRecorded: () => void;
}

export default function ApprovalsPage({
  user,
  assessments,
  onSelectAssessment,
  onDecisionRecorded,
}: ApprovalsPageProps) {
  const isManager = user.role === "MANAGER";

  // AW.7: approvals this user is covering as a delegate.
  const [delegations, setDelegations] = useState<Delegation[]>([]);
  useEffect(() => {
    listDelegations()
      .then((all) => setDelegations(activeDelegationsFor(all, user.id)))
      .catch(() => setDelegations([]));
  }, [user.id]);

  const queue = buildQueue(user, assessments, delegations);

  return (
    <div>
      <div className="page-header">
        <div>
          <h2>Approvals</h2>
          <p>
            {isManager
              ? "Assessments your direct reports have submitted for your review."
              : "Assessments approved by a manager and ready for committee sign-off."}
            {delegations.length > 0 && " Items you are covering as a delegate are marked."}
          </p>
        </div>
      </div>

      <section className="content-card">
        {queue.length === 0 ? (
          <div className="empty-state">
            <h3>Nothing waiting on you right now.</h3>
          </div>
        ) : (
          <div className="assessment-list">
            {queue.map((item) => (
              <ApprovalRow
                key={`${item.mode}-${item.assessment.id}`}
                assessment={item.assessment}
                isManager={item.mode === "manager"}
                delegation={item.delegation}
                user={user}
                onOpen={() => onSelectAssessment(item.assessment)}
                onDecisionRecorded={onDecisionRecorded}
              />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

const COMMITTEE_STATUSES = ["READY_FOR_COMMITTEE", "COMMITTEE_REVIEW", "DEFERRED"];

interface QueueItem {
  assessment: Assessment;
  mode: "manager" | "committee";
  // Set when the user acts on this item as a delegate (AW.7).
  delegation?: Delegation;
}

function covers(delegation: Delegation, assessment: Assessment): boolean {
  if (delegation.scope_type === "ASSESSMENT") {
    return delegation.scope_assessment_id === assessment.id;
  }
  return delegation.authority === "MANAGER_APPROVAL"
    ? assessment.manager_id === delegation.delegator_id
    : true;
}

// The user's own queue, plus whatever their active delegations cover.
// The backend re-checks every rule when a decision is submitted.
function buildQueue(user: CurrentUser, assessments: Assessment[], delegations: Delegation[]): QueueItem[] {
  const items: QueueItem[] = [];
  // Q-6: administering the system is not committee membership. (If the
  // backend's COMMITTEE_ADMIN_AUTHORITY policy is switched on, an Admin can
  // still act from the assessment itself.)
  const isCommittee = user.role === "COMMITTEE_MEMBER";

  for (const assessment of assessments) {
    if (assessment.status === "SUBMITTED_TO_MANAGER") {
      if (user.role === "MANAGER" && assessment.manager_id === user.id) {
        items.push({ assessment, mode: "manager" });
        continue;
      }
      const delegation = delegations.find((d) => d.authority === "MANAGER_APPROVAL" && covers(d, assessment));
      if (delegation) items.push({ assessment, mode: "manager", delegation });
    } else if (COMMITTEE_STATUSES.includes(assessment.status)) {
      if (isCommittee) {
        items.push({ assessment, mode: "committee" });
        continue;
      }
      const delegation = delegations.find((d) => d.authority === "COMMITTEE_SIGN_OFF" && covers(d, assessment));
      if (delegation) items.push({ assessment, mode: "committee", delegation });
    }
  }
  return items;
}

function ApprovalRow({
  assessment,
  isManager,
  delegation,
  user,
  onOpen,
  onDecisionRecorded,
}: {
  assessment: Assessment;
  isManager: boolean;
  delegation?: Delegation;
  user: CurrentUser;
  onOpen: () => void;
  onDecisionRecorded: () => void;
}) {
  const [comment, setComment] = useState("");
  const [rationale, setRationale] = useState("");
  const [structuredConditions, setStructuredConditions] = useState<
    CommitteeConditionInput[]
  >([{ description: "", owner: "", due_date: "", priority: "MEDIUM" }]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // P3: bumped after governance actions so the readiness panel re-checks.
  const [readinessKey, setReadinessKey] = useState(0);
  // Stage 19: field-level flag for the required committee rationale.
  const [rationaleMissing, setRationaleMissing] = useState(false);

  // R12.2: the decision package, shown to the committee before they act.
  const [showPackage, setShowPackage] = useState(false);
  const [decisionPackage, setDecisionPackage] = useState<DecisionPackage | null>(null);
  const [packageLoading, setPackageLoading] = useState(false);

  // R12.6: individual member vote, separate from the final decision.
  // Append-only (R12.6): every vote, superseded ones included.
  const [votes, setVotes] = useState<VoteRecord[]>([]);
  const [voteComment, setVoteComment] = useState("");
  const [recastReason, setRecastReason] = useState("");
  const [recastReasonMissing, setRecastReasonMissing] = useState(false);
  const [votingLoading, setVotingLoading] = useState(false);

  useEffect(() => {
    if (!isManager) {
      getVoteHistory(assessment.id).then(setVotes).catch(() => setVotes([]));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assessment.id]);

  // The seat this user votes in: their own, or the delegator's (AW.7).
  const seatId = delegation ? delegation.delegator_id : user.id;
  const seatVote = votes.find((v) => v.member_id === seatId && v.is_current);

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

  async function runCastVote(vote: "APPROVE" | "DISSENT" | "ABSTAIN") {
    if (seatVote && !recastReason.trim()) {
      setRecastReasonMissing(true);
      setError("Give a reason for changing the vote — the earlier vote stays on record.");
      return;
    }
    setRecastReasonMissing(false);
    setVotingLoading(true);
    setError(null);
    try {
      await castCommitteeVote(
        assessment.id,
        vote,
        voteComment || undefined,
        seatVote ? recastReason.trim() : undefined
      );
      setVotes(await getVoteHistory(assessment.id));
      setRecastReason("");
      setVoteComment("");
    } catch (err) {
      setError(friendlyError(err, "Your vote couldn't be recorded. Please try again."));
    } finally {
      setVotingLoading(false);
    }
  }

  function updateCondition(index: number, field: keyof CommitteeConditionInput, value: string) {
    setStructuredConditions((current) =>
      current.map((condition, i) =>
        i === index ? { ...condition, [field]: value } : condition
      )
    );
  }

  async function runManagerDecision(decision: ManagerDecision) {
    setSubmitting(true);
    setError(null);
    try {
      await submitManagerDecision(assessment.id, decision, comment || undefined);
      onDecisionRecorded();
    } catch (err) {
      setError(friendlyError(err, "The decision couldn't be recorded. Your comments are still here — please try again."));
      setReadinessKey((k) => k + 1);
    } finally {
      setSubmitting(false);
    }
  }

  async function runCommitteeDecision(decision: CommitteeDecisionOption) {
    if (!rationale.trim()) {
      setRationaleMissing(true);
      setError("A decision rationale is required.");
      return;
    }
    setRationaleMissing(false);

    const validConditions = structuredConditions.filter(
      (c) => c.description.trim() && c.owner.trim() && c.due_date
    );

    if (decision === "approve_with_conditions" && validConditions.length === 0) {
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
        decision === "approve_with_conditions" ? validConditions : undefined
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
    <div className="assessment-row" style={{ flexDirection: "column", alignItems: "stretch", gap: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <h4>
            <button type="button" className="row-link-button" onClick={onOpen}>
              {assessment.title}
              <span className="sr-only"> — open assessment</span>
            </button>
          </h4>
          <span className="change-type">
            {assessment.reference_id ?? `ASSESSMENT-${assessment.id}`} &middot; {assessment.status}
          </span>
        </div>
        <div>
          {assessment.risk_level && <RiskLevelBadge level={assessment.risk_level} />}
        </div>
      </div>

      {delegation && (
        <p
          style={{
            margin: 0,
            padding: "6px 10px",
            borderRadius: 6,
            background: "#eef2ff",
            color: "#3730a3",
            fontSize: 13,
          }}
        >
          You are acting as delegate for <strong>{delegation.delegator_name}</strong> (delegation #
          {delegation.id}, until{" "}
          {new Date(delegation.end_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}
          ). Your decision is recorded under both names.
        </p>
      )}

      {isManager ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {/* R11: approval to committee needs the mandatory challenge sign-off. */}
          <ChallengeSignoffPanel
            assessmentId={assessment.id}
            onChanged={() => {
              setError(null);
              setReadinessKey((k) => k + 1);
            }}
          />
          {/* P3: the server's readiness result for committee submission. */}
          <CommitteeReadinessPanel assessmentId={assessment.id} refreshKey={readinessKey} />
          <textarea
            aria-label="Manager comment (required to return or reject)"
            placeholder="Comment (required to return or reject)"
            value={comment}
            onChange={(event) => setComment(event.target.value)}
            rows={2}
          />
          <div style={{ display: "flex", gap: 8 }}>
            <button className="primary-button" disabled={submitting} onClick={() => runManagerDecision("approve")}>
              Approve
            </button>
            <button disabled={submitting} onClick={() => runManagerDecision("return")}>
              Return for Correction
            </button>
            <button disabled={submitting} onClick={() => runManagerDecision("reject")}>
              Reject
            </button>
          </div>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <button
            type="button"
            className="doc-action-button"
            style={{ alignSelf: "flex-start" }}
            onClick={toggleDecisionPackage}
          >
            {showPackage ? "Hide Decision Package" : "View Decision Package (R12.2)"}
          </button>

          {showPackage && (
            <div style={{ border: "1px solid #e5e7eb", borderRadius: 6, padding: 12 }}>
              {packageLoading ? (
                <p role="status">Loading decision package...</p>
              ) : decisionPackage ? (
                <DecisionPackageView pkg={decisionPackage} />
              ) : (
                <p role="alert">The decision package couldn't be loaded. Close and reopen it to try again.</p>
              )}
            </div>
          )}

          {/* P3: what still blocks the final decision, from the server. */}
          <CommitteeReadinessPanel assessmentId={assessment.id} purpose="FINAL_DECISION" refreshKey={readinessKey} />

          <div style={{ border: "1px solid #e5e7eb", borderRadius: 6, padding: 12 }}>
            <h4 style={{ margin: "0 0 8px" }}>Cast Your Vote (R12.6)</h4>
            <VoteHistoryList votes={votes} />
            {seatVote && (
              <>
                <p style={{ margin: "0 0 6px", fontSize: 13 }}>
                  This seat has voted <strong>{seatVote.vote}</strong>. A new vote replaces it as current; the earlier
                  vote stays on record.
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
            <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
              <button disabled={votingLoading} onClick={() => runCastVote("APPROVE")}>
                Approve
              </button>
              <button disabled={votingLoading} onClick={() => runCastVote("DISSENT")}>
                Dissent
              </button>
              <button disabled={votingLoading} onClick={() => runCastVote("ABSTAIN")}>
                Abstain
              </button>
            </div>
          </div>

          <textarea
            aria-label="Decision rationale (required)"
            aria-required="true"
            aria-invalid={rationaleMissing || undefined}
            aria-describedby={rationaleMissing ? `rationale-error-${assessment.id}` : undefined}
            placeholder="Decision rationale (required)"
            value={rationale}
            onChange={(event) => {
              setRationale(event.target.value);
              if (event.target.value.trim()) setRationaleMissing(false);
            }}
            rows={2}
          />
          {rationaleMissing && (
            <span id={`rationale-error-${assessment.id}`} className="field-error">
              <span aria-hidden="true">⚠</span> Enter a decision rationale before recording the decision.
            </span>
          )}

          <div style={{ border: "1px solid #e5e7eb", borderRadius: 6, padding: 12 }}>
            <h4 style={{ margin: "0 0 8px" }}>
              Conditions (required only for Approve with Conditions)
            </h4>
            {structuredConditions.map((condition, index) => (
              <div
                key={index}
                style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 6 }}
              >
                <input
                  aria-label={`Condition ${index + 1} description`}
                  placeholder="Condition description"
                  value={condition.description}
                  onChange={(event) => updateCondition(index, "description", event.target.value)}
                  style={{ flex: 2, minWidth: 160 }}
                />
                <input
                  aria-label={`Condition ${index + 1} owner`}
                  placeholder="Owner"
                  value={condition.owner}
                  onChange={(event) => updateCondition(index, "owner", event.target.value)}
                  style={{ flex: 1, minWidth: 100 }}
                />
                <input
                  type="date"
                  aria-label={`Condition ${index + 1} due date`}
                  value={condition.due_date}
                  onChange={(event) => updateCondition(index, "due_date", event.target.value)}
                />
                <select
                  aria-label={`Condition ${index + 1} priority`}
                  value={condition.priority}
                  onChange={(event) => updateCondition(index, "priority", event.target.value)}
                >
                  <option value="LOW">Low</option>
                  <option value="MEDIUM">Medium</option>
                  <option value="HIGH">High</option>
                  <option value="CRITICAL">Critical</option>
                </select>
              </div>
            ))}
            <button
              type="button"
              onClick={() =>
                setStructuredConditions((current) => [
                  ...current,
                  { description: "", owner: "", due_date: "", priority: "MEDIUM" },
                ])
              }
            >
              + Add Condition
            </button>
          </div>

          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button className="primary-button" disabled={submitting} onClick={() => runCommitteeDecision("approve")}>
              Approve
            </button>
            <button disabled={submitting} onClick={() => runCommitteeDecision("approve_with_conditions")}>
              Approve with Conditions
            </button>
            <button disabled={submitting} onClick={() => runCommitteeDecision("defer")}>
              Defer
            </button>
            <button disabled={submitting} onClick={() => runCommitteeDecision("reject")}>
              Reject (reason required)
            </button>
          </div>
        </div>
      )}

      {error && <p role="alert" style={{ color: "#b91c1c", margin: 0 }}>{error}</p>}
    </div>
  );
}
