import { useEffect, useMemo, useState } from "react";
import type { Assessment } from "../api/assessments";
import type { CurrentUser } from "../api/auth";
import {
  AUTHORITY_LABELS,
  createDelegation,
  formatWindow,
  getDelegationOptions,
  listDelegations,
  revokeDelegation,
  type Delegation,
  type DelegationAuthority,
  type DelegationOptions,
  type DelegationState,
} from "../api/delegations";
import { friendlyError } from "../utils/errorMessages";
import { FieldError, RequiredMarker } from "./FormFeedback";

// AW.7: temporary delegation of a Manager's or Committee Member's
// approval authority. The rules are enforced by the backend
// (app/services/delegation.py); this page records and revokes them.

const COMMITTEE_STATUSES = ["READY_FOR_COMMITTEE", "COMMITTEE_REVIEW", "DEFERRED"];

function stateClass(state: DelegationState): string {
  return `delegation-pill delegation-pill-${state.toLowerCase()}`;
}

function toLocalInput(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function authorityFor(role: string | undefined): DelegationAuthority {
  return role === "COMMITTEE_MEMBER" ? "COMMITTEE_SIGN_OFF" : "MANAGER_APPROVAL";
}

interface DelegationsPageProps {
  user: CurrentUser;
  assessments: Assessment[];
}

export default function DelegationsPage({ user, assessments }: DelegationsPageProps) {
  const [delegations, setDelegations] = useState<Delegation[]>([]);
  const [options, setOptions] = useState<DelegationOptions | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Bumped to reload after a create or revoke.
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    Promise.all([listDelegations(), getDelegationOptions()])
      .then(([list, opts]) => {
        if (cancelled) return;
        setDelegations(list);
        setOptions(opts);
        setError(null);
      })
      .catch((err) => {
        if (!cancelled) setError(friendlyError(err, "Delegations couldn't be loaded. Please try again."));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  function load() {
    setLoading(true);
    setReloadKey((key) => key + 1);
  }

  const coveringNow = delegations.filter((d) => d.delegate_id === user.id && d.state === "ACTIVE");

  const canCreate = !!options && options.delegators.length > 0;

  return (
    <div className="delegation-page">
      <div className="page-header">
        <div>
          <h2>Delegations</h2>
          <p>
            Hand your approval authority to another manager while you are unavailable. A delegate
            can only act inside the dates and scope you set, and every approval they make records
            both of you.
          </p>
        </div>
      </div>

      {coveringNow.length > 0 && (
        <section className="delegation-callout" aria-label="Authority you currently hold as a delegate">
          <h3>You are covering</h3>
          <ul>
            {coveringNow.map((d) => (
              <li key={d.id}>
                {AUTHORITY_LABELS[d.authority]} for <strong>{d.delegator_name}</strong>
                {d.scope_type === "ASSESSMENT" ? ` on assessment #${d.scope_assessment_id}` : ""}, until{" "}
                {new Date(d.end_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}.
                Find these items under Approvals.
              </li>
            ))}
          </ul>
        </section>
      )}

      {error && (
        <p role="alert" className="delegation-error-banner">
          {error}
        </p>
      )}

      {canCreate && options && (
        <div className="delegation-layout">
          <section className="content-card delegation-card">
            <header className="delegation-card-header">
              <h3>New delegation</h3>
              <p>Choose who covers for you, for how long, and what they can approve.</p>
            </header>
            <div className="delegation-card-body">
              <DelegationForm user={user} options={options} assessments={assessments} onCreated={load} />
            </div>
          </section>

          <aside className="content-card delegation-card delegation-help" aria-label="How delegation works">
            <header className="delegation-card-header">
              <h3>How delegation works</h3>
            </header>
            <ul className="delegation-help-list">
              <li>The delegate can act only within the dates and scope you set.</li>
              <li>Access stops automatically at the end time, never more than {options.max_days} days.</li>
              <li>Every approval they make records both of you.</li>
              <li>You can revoke a delegation at any time from the list below.</li>
            </ul>
          </aside>
        </div>
      )}

      <section className="content-card delegation-card">
        <header className="delegation-card-header">
          <h3>Delegations</h3>
          {!loading && delegations.length > 0 && (
            <span className="delegation-count">{delegations.length}</span>
          )}
        </header>
        {loading ? (
          <p role="status" aria-live="polite" className="delegation-card-body">Loading…</p>
        ) : delegations.length === 0 ? (
          <div className="delegation-empty">
            <h4>No delegations yet</h4>
            <p>
              {canCreate
                ? "When you are away, create a delegation above so approvals keep moving."
                : "Delegations you create or are given will appear here."}
            </p>
          </div>
        ) : (
          <div className="delegation-table-wrap">
            <table className="delegation-table">
              <thead>
                <tr>
                  <th>From → to</th>
                  <th>Authority</th>
                  <th>Scope</th>
                  <th>Period</th>
                  <th>State</th>
                  <th>Reason</th>
                  <th><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {delegations.map((d) => (
                  <DelegationRow key={d.id} delegation={d} user={user} onChanged={load} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function DelegationRow({
  delegation: d,
  user,
  onChanged,
}: {
  delegation: Delegation;
  user: CurrentUser;
  onChanged: () => void;
}) {
  const [revoking, setRevoking] = useState(false);
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canRevoke =
    (d.state === "ACTIVE" || d.state === "SCHEDULED") &&
    (user.role === "ADMIN" || user.id === d.delegator_id || user.id === d.created_by_id);

  async function submitRevoke() {
    if (!reason.trim()) {
      setError("Give a reason for revoking.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await revokeDelegation(d.id, reason.trim());
      onChanged();
    } catch (err) {
      setError(friendlyError(err, "The delegation couldn't be revoked. Please try again."));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <tr>
      <td>
        {d.delegator_name} → <strong>{d.delegate_name}</strong>
      </td>
      <td className="delegation-nowrap">{AUTHORITY_LABELS[d.authority]}</td>
      <td>{d.scope_type === "ASSESSMENT" ? `Assessment #${d.scope_assessment_id}` : "All approvals"}</td>
      <td>{formatWindow(d)}</td>
      <td>
        <span className={stateClass(d.state)}>
          {d.state.charAt(0) + d.state.slice(1).toLowerCase()}
        </span>
      </td>
      <td>
        {d.reason}
        {d.revoke_reason && (
          <div className="delegation-revoked">Revoked: {d.revoke_reason}</div>
        )}
      </td>
      <td>
        {canRevoke && !revoking && (
          <button type="button" className="link-button" onClick={() => setRevoking(true)}>
            Revoke
          </button>
        )}
        {revoking && (
          <div className="delegation-revoke-box">
            <label htmlFor={`revoke-reason-${d.id}`} className="sr-only">
              Reason for revoking
            </label>
            <input
              id={`revoke-reason-${d.id}`}
              placeholder="Reason for revoking"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              className="delegation-input"
            />
            {error && <FieldError id={`revoke-error-${d.id}`} message={error} />}
            <div className="delegation-revoke-actions">
              <button type="button" className="secondary-button" disabled={submitting} onClick={submitRevoke}>
                {submitting ? "Revoking…" : "Confirm"}
              </button>
              <button type="button" className="link-button" onClick={() => setRevoking(false)}>
                Cancel
              </button>
            </div>
          </div>
        )}
      </td>
    </tr>
  );
}

function DelegationForm({
  user,
  options,
  assessments,
  onCreated,
}: {
  user: CurrentUser;
  options: DelegationOptions;
  assessments: Assessment[];
  onCreated: () => void;
}) {
  const now = new Date();
  const [delegatorId, setDelegatorId] = useState<number>(options.delegators[0]?.id ?? user.id);
  const [delegateId, setDelegateId] = useState<string>("");
  const [startAt, setStartAt] = useState(toLocalInput(now));
  const [endAt, setEndAt] = useState(toLocalInput(new Date(now.getTime() + 7 * 86_400_000)));
  const [scopeType, setScopeType] = useState<"ALL" | "ASSESSMENT">("ALL");
  const [scopeAssessmentId, setScopeAssessmentId] = useState<string>("");
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const delegator = options.delegators.find((u) => u.id === delegatorId);
  const authority = authorityFor(delegator?.role);
  const delegates = options.delegates.filter((u) => u.id !== delegatorId);

  // Assessments whose approval the chosen delegator could hand over.
  const scopeChoices = useMemo(
    () =>
      assessments.filter((a) =>
        authority === "MANAGER_APPROVAL"
          ? a.manager_id === delegatorId && a.status === "SUBMITTED_TO_MANAGER"
          : COMMITTEE_STATUSES.includes(a.status)
      ),
    [assessments, authority, delegatorId]
  );

  async function submit() {
    setError(null);
    setSuccess(null);
    if (!delegateId) {
      setError("Choose who to delegate to.");
      return;
    }
    if (!reason.trim()) {
      setError("Give a reason for the delegation.");
      return;
    }
    if (scopeType === "ASSESSMENT" && !scopeAssessmentId) {
      setError("Choose the assessment this delegation covers.");
      return;
    }
    setSubmitting(true);
    try {
      const created = await createDelegation({
        delegate_id: Number(delegateId),
        authority,
        scope_type: scopeType,
        scope_assessment_id: scopeType === "ASSESSMENT" ? Number(scopeAssessmentId) : null,
        start_at: new Date(startAt).toISOString(),
        end_at: new Date(endAt).toISOString(),
        reason: reason.trim(),
        ...(delegatorId !== user.id ? { delegator_id: delegatorId } : {}),
      });
      setSuccess(`Delegated to ${created.delegate_name}. It is ${created.state.toLowerCase()}.`);
      setReason("");
      setDelegateId("");
      onCreated();
    } catch (err) {
      setError(friendlyError(err, "The delegation couldn't be created. Please check the details."));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form
      noValidate
      aria-label="New delegation"
      className="delegation-form"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      {options.delegators.length > 1 && (
        <div className="delegation-field">
          <label htmlFor="delegation-delegator">
            On behalf of
            <RequiredMarker />
          </label>
          <select
            id="delegation-delegator"
            value={delegatorId}
            onChange={(event) => setDelegatorId(Number(event.target.value))}
            className="delegation-input"
          >
            {options.delegators.map((u) => (
              <option key={u.id} value={u.id}>
                {u.full_name || u.email} ({u.role === "COMMITTEE_MEMBER" ? "Committee" : "Manager"})
              </option>
            ))}
          </select>
        </div>
      )}

      <div className="delegation-authority">
        <span>Authority</span>
        <strong>{AUTHORITY_LABELS[authority]}</strong>
      </div>

      <div className="delegation-field">
        <label htmlFor="delegation-delegate">
          Delegate
          <RequiredMarker />
        </label>
        <select
          id="delegation-delegate"
          value={delegateId}
          onChange={(event) => setDelegateId(event.target.value)}
          className="delegation-input"
        >
          <option value="">Choose a manager…</option>
          {delegates.map((u) => (
            <option key={u.id} value={u.id}>
              {u.full_name || u.email}
            </option>
          ))}
        </select>
      </div>

      <div className="delegation-dates">
        <div className="delegation-field">
          <label htmlFor="delegation-start">
            Starts
            <RequiredMarker />
          </label>
          <input
            id="delegation-start"
            type="datetime-local"
            value={startAt}
            onChange={(event) => setStartAt(event.target.value)}
            className="delegation-input"
          />
        </div>
        <div className="delegation-field">
          <label htmlFor="delegation-end">
            Ends
            <RequiredMarker />
          </label>
          <input
            id="delegation-end"
            type="datetime-local"
            value={endAt}
            onChange={(event) => setEndAt(event.target.value)}
            className="delegation-input"
          />
        </div>
      </div>
      <p className="delegation-hint">
        At most {options.max_days} days. Access stops automatically at the end time.
      </p>

      <fieldset className="delegation-scope">
        <legend>Scope</legend>
        <label className={`delegation-option${scopeType === "ALL" ? " is-selected" : ""}`}>
          <input
            type="radio"
            name="delegation-scope"
            value="ALL"
            checked={scopeType === "ALL"}
            onChange={() => setScopeType("ALL")}
          />
          <span>
            All {authority === "MANAGER_APPROVAL" ? "assessments submitted to them" : "committee sign-offs"}
          </span>
        </label>
        <label className={`delegation-option${scopeType === "ASSESSMENT" ? " is-selected" : ""}`}>
          <input
            type="radio"
            name="delegation-scope"
            value="ASSESSMENT"
            checked={scopeType === "ASSESSMENT"}
            onChange={() => setScopeType("ASSESSMENT")}
          />
          <span>One assessment only</span>
        </label>
        {scopeType === "ASSESSMENT" && (
          <select
            aria-label="Assessment covered by this delegation"
            value={scopeAssessmentId}
            onChange={(event) => setScopeAssessmentId(event.target.value)}
            className="delegation-input"
          >
            <option value="">Choose an assessment…</option>
            {scopeChoices.map((a) => (
              <option key={a.id} value={a.id}>
                #{a.id} — {a.title}
              </option>
            ))}
          </select>
        )}
      </fieldset>

      <div className="delegation-field">
        <label htmlFor="delegation-reason">
          Reason
          <RequiredMarker />
        </label>
        <textarea
          id="delegation-reason"
          rows={3}
          placeholder="e.g. Annual leave 12–19 October"
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          className="delegation-input"
        />
      </div>

      {error && (
        <p role="alert" className="delegation-form-error">
          {error}
        </p>
      )}
      {success && (
        <p role="status" className="delegation-success">
          {success}
        </p>
      )}

      <div className="delegation-actions">
        <button type="submit" className="primary-button" disabled={submitting}>
          {submitting ? "Saving…" : "Delegate"}
        </button>
      </div>
    </form>
  );
}
