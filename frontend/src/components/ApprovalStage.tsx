import { useEffect, useState, type ReactNode } from "react";
import type { Assessment } from "../api/assessments";
import type { CurrentUser } from "../api/auth";
import { activeDelegationsFor, delegationCovers, listDelegations, type Delegation } from "../api/delegations";
import CommitteeDecisionPanel from "./CommitteeDecisionPanel";
import { COMMITTEE_PHASE_STATUSES, DECIDED_STATUSES } from "./stageTrackerStages";
import "./ManagerDecisionPanel.css";
import "./ApprovalStage.css";

type StageState = { label: string; tone: "active" | "done" | "locked" | "bad" };

function managerState(status: string): StageState {
  if (status === "RETURNED_BY_MANAGER") return { label: "Returned to owner", tone: "bad" };
  if (status === "SUBMITTED_TO_MANAGER") return { label: "In progress", tone: "active" };
  if (status === "MANAGER_REJECTED") return { label: "Rejected", tone: "bad" };
  return { label: "Complete", tone: "done" };
}

function committeeState(status: string): StageState {
  if (status === "DEFERRED") return { label: "Deferred", tone: "active" };
  if (COMMITTEE_PHASE_STATUSES.includes(status)) return { label: "Open for voting", tone: "active" };
  if (status === "MANAGER_REJECTED") return { label: "Not reached", tone: "locked" };
  if (DECIDED_STATUSES.includes(status)) return { label: "Decided", tone: "done" };
  return { label: "Locked", tone: "locked" };
}

// One screen for the whole approval chain: Stage 1 is the manager's review
// and decision, Stage 2 is the committee's vote and final decision. The
// active stage is open; a finished one folds away to its header; a stage
// that hasn't been reached is locked. The backend enforces the order and
// every rule -- this only lays the two stages out together.
export default function ApprovalStage({
  assessment,
  user,
  managerContent,
  statusContent,
  onDecisionRecorded,
}: {
  assessment: Assessment;
  user: CurrentUser;
  /** Stage 1: the challenge review and the manager's decision panel. */
  managerContent: ReactNode;
  /** The approval status, timeline, comments, conditions and amendment view. */
  statusContent: ReactNode;
  onDecisionRecorded: () => void;
}) {
  const status = assessment.status;
  const inCommitteePhase = COMMITTEE_PHASE_STATUSES.includes(status);
  const reachedCommittee = inCommitteePhase || (DECIDED_STATUSES.includes(status) && status !== "MANAGER_REJECTED");

  const [delegations, setDelegations] = useState<Delegation[]>([]);
  useEffect(() => {
    listDelegations()
      .then((all) => setDelegations(activeDelegationsFor(all, user.id)))
      .catch(() => setDelegations([]));
  }, [user.id]);

  // The same rule as the Approvals queue: a committee member, or a delegate
  // for committee sign-off on this assessment (Q-6: an Admin is not a member).
  const isMember = user.role === "COMMITTEE_MEMBER";
  const delegation = isMember
    ? undefined
    : delegations.find((d) => d.authority === "COMMITTEE_SIGN_OFF" && delegationCovers(d, assessment));
  const canVote = inCommitteePhase && (isMember || !!delegation);

  const manager = managerState(status);
  const committee = committeeState(status);

  return (
    <div className="apv">
      <section className="apv-card" aria-label="Section I: Manager review">
        <div className="apv-card-head">
          <h2>Section I: Manager Audit and Decision Package</h2>
          <span className={`apv-state apv-${manager.tone}`}>{manager.label}</span>
        </div>
        <div className="apv-body">{managerContent}</div>
      </section>

      <section className="apv-card" aria-label="Section II: Committee voting">
        <div className="apv-card-head">
          <h2>Section II: Committee Voting Panel</h2>
          <span className={`apv-state apv-${committee.tone}`}>{committee.label}</span>
        </div>
        <div className="apv-body">
          {!reachedCommittee ? (
            <p className="apv-locked">
              {status === "MANAGER_REJECTED"
                ? "The manager rejected this assessment, so it did not go to the committee."
                : "Opens when the manager approves and sends this to the committee."}
            </p>
          ) : (
            <>
              {inCommitteePhase &&
                (canVote ? (
                  <CommitteeDecisionPanel
                    assessment={assessment}
                    user={user}
                    delegation={delegation}
                    onDecisionRecorded={onDecisionRecorded}
                  />
                ) : (
                  <p className="apv-locked">
                    Waiting on the committee. Members vote and record the final decision here; you&apos;ll see the
                    outcome below once it&apos;s recorded.
                  </p>
                ))}
              {statusContent}
            </>
          )}
        </div>
      </section>
    </div>
  );
}
