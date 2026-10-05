import { useEffect, useState } from "react";
import type { Assessment } from "../api/assessments";
import type { CurrentUser } from "../api/auth";
import { activeDelegationsFor, delegationCovers as covers, listDelegations, type Delegation } from "../api/delegations";
import RiskLevelBadge from "./RiskLevelBadge";
import CommitteeDecisionPanel from "./CommitteeDecisionPanel";

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
    <div className="approvals-page">
      <div className="page-header">
        <div>
          <h2>Approvals</h2>
          <p>
            {isManager
              ? "Pending tasks: assessments your direct reports have submitted for your decision. Open one to review the findings and approve, return or reject it."
              : "Assessments approved by a manager and ready for committee sign-off."}
            {delegations.length > 0 && " Items you are covering as a delegate are marked."}
          </p>
        </div>
      </div>

      <section className="content-card approvals-card">
        <header className="approvals-card-header">
          <h3>Pending decisions</h3>
          {queue.length > 0 && <span className="approvals-count">{queue.length}</span>}
        </header>
        {queue.length === 0 ? (
          <div className="approvals-empty">
            <h4>Nothing waiting on you right now.</h4>
            <p>New submissions that need your decision will appear here.</p>
          </div>
        ) : (
          <div className="approvals-list">
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
  return (
    <div className="approval-item">
      <div className="approval-item-main">
        <div className="approval-item-info">
          <h4>
            <button type="button" className="row-link-button" onClick={onOpen}>
              {assessment.title}
              <span className="sr-only"> — open assessment</span>
            </button>
          </h4>
          <div className="approval-meta">
            <span className="approval-ref">{assessment.reference_id ?? `ASSESSMENT-${assessment.id}`}</span>
            <span className="approval-status">{statusLabel(assessment.status)}</span>
            {delegation && <span className="approval-delegate-tag">Delegated</span>}
          </div>
        </div>
        <div className="approval-item-side">
          {assessment.risk_level && <RiskLevelBadge level={assessment.risk_level} />}
          {isManager && (
            <button type="button" className="primary-button" onClick={onOpen}>
              Review &amp; Decide
            </button>
          )}
        </div>
      </div>

      {delegation && isManager && (
        <p className="approval-delegate-note">
          You are acting as delegate for <strong>{delegation.delegator_name}</strong> (delegation #
          {delegation.id}, until{" "}
          {new Date(delegation.end_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}
          ). Your decision is recorded under both names.
        </p>
      )}

      {!isManager && (
        <CommitteeDecisionPanel
          assessment={assessment}
          user={user}
          delegation={delegation}
          onDecisionRecorded={onDecisionRecorded}
        />
      )}
    </div>
  );
}

function statusLabel(status: string): string {
  const text = status.replace(/_/g, " ").toLowerCase();
  return text.charAt(0).toUpperCase() + text.slice(1);
}
