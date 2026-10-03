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

const STATE_STYLES: Record<DelegationState, { background: string; color: string }> = {
  ACTIVE: { background: "#dcfce7", color: "#166534" },
  SCHEDULED: { background: "#e0e7ff", color: "#3730a3" },
  EXPIRED: { background: "#f3f4f6", color: "#4b5563" },
  REVOKED: { background: "#fee2e2", color: "#991b1b" },
};

const inputStyle = { padding: "8px 10px", borderRadius: 8, border: "1px solid #d0d5dd", width: "100%" };
const cell = { padding: "8px 4px", verticalAlign: "top" as const };

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

  return (
    <div>
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
        <section className="content-card" aria-label="Authority you currently hold as a delegate">
          <h3 style={{ marginTop: 0 }}>You are covering</h3>
          <ul style={{ margin: 0, paddingLeft: 18 }}>
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
        <p role="alert" style={{ color: "#b91c1c" }}>
          {error}
        </p>
      )}

      {options && options.delegators.length > 0 && (
        <section className="content-card">
          <h3 style={{ marginTop: 0 }}>New delegation</h3>
          <DelegationForm user={user} options={options} assessments={assessments} onCreated={load} />
        </section>
      )}

      <section className="content-card">
        <h3 style={{ marginTop: 0 }}>Delegations</h3>
        {loading ? (
          <p role="status" aria-live="polite">Loading…</p>
        ) : delegations.length === 0 ? (
          <div className="empty-state">
            <h3>No delegations yet</h3>
          </div>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
              <thead>
                <tr style={{ textAlign: "left", borderBottom: "1px solid #e5e7eb" }}>
                  <th style={cell}>From → to</th>
                  <th style={cell}>Authority</th>
                  <th style={cell}>Scope</th>
                  <th style={cell}>Period</th>
                  <th style={cell}>State</th>
                  <th style={cell}>Reason</th>
                  <th style={cell}><span className="sr-only">Actions</span></th>
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
    <tr style={{ borderBottom: "1px solid #f1f5f9" }}>
      <td style={cell}>
        {d.delegator_name} → <strong>{d.delegate_name}</strong>
      </td>
      <td style={cell}>{AUTHORITY_LABELS[d.authority]}</td>
      <td style={cell}>{d.scope_type === "ASSESSMENT" ? `Assessment #${d.scope_assessment_id}` : "All approvals"}</td>
      <td style={cell}>{formatWindow(d)}</td>
      <td style={cell}>
        <span
          style={{
            ...STATE_STYLES[d.state],
            padding: "2px 8px",
            borderRadius: 999,
            fontSize: 12,
            fontWeight: 600,
          }}
        >
          {d.state.charAt(0) + d.state.slice(1).toLowerCase()}
        </span>
      </td>
      <td style={cell}>
        {d.reason}
        {d.revoke_reason && (
          <div style={{ color: "#667085", fontSize: 12, marginTop: 4 }}>Revoked: {d.revoke_reason}</div>
        )}
      </td>
      <td style={cell}>
        {canRevoke && !revoking && (
          <button type="button" className="link-button" onClick={() => setRevoking(true)}>
            Revoke
          </button>
        )}
        {revoking && (
          <div style={{ display: "flex", flexDirection: "column", gap: 6, minWidth: 180 }}>
            <label htmlFor={`revoke-reason-${d.id}`} className="sr-only">
              Reason for revoking
            </label>
            <input
              id={`revoke-reason-${d.id}`}
              placeholder="Reason for revoking"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              style={inputStyle}
            />
            {error && <FieldError id={`revoke-error-${d.id}`} message={error} />}
            <div style={{ display: "flex", gap: 8 }}>
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
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
      style={{ display: "grid", gap: 12, maxWidth: 560 }}
    >
      {options.delegators.length > 1 && (
        <div>
          <label htmlFor="delegation-delegator">
            On behalf of
            <RequiredMarker />
          </label>
          <select
            id="delegation-delegator"
            value={delegatorId}
            onChange={(event) => setDelegatorId(Number(event.target.value))}
            style={inputStyle}
          >
            {options.delegators.map((u) => (
              <option key={u.id} value={u.id}>
                {u.full_name || u.email} ({u.role === "COMMITTEE_MEMBER" ? "Committee" : "Manager"})
              </option>
            ))}
          </select>
        </div>
      )}

      <p style={{ margin: 0, color: "#475467", fontSize: 14 }}>
        Authority: <strong>{AUTHORITY_LABELS[authority]}</strong>
      </p>

      <div>
        <label htmlFor="delegation-delegate">
          Delegate
          <RequiredMarker />
        </label>
        <select
          id="delegation-delegate"
          value={delegateId}
          onChange={(event) => setDelegateId(event.target.value)}
          style={inputStyle}
        >
          <option value="">Choose a manager…</option>
          {delegates.map((u) => (
            <option key={u.id} value={u.id}>
              {u.full_name || u.email}
            </option>
          ))}
        </select>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 12 }}>
        <div>
          <label htmlFor="delegation-start">
            Starts
            <RequiredMarker />
          </label>
          <input
            id="delegation-start"
            type="datetime-local"
            value={startAt}
            onChange={(event) => setStartAt(event.target.value)}
            style={inputStyle}
          />
        </div>
        <div>
          <label htmlFor="delegation-end">
            Ends
            <RequiredMarker />
          </label>
          <input
            id="delegation-end"
            type="datetime-local"
            value={endAt}
            onChange={(event) => setEndAt(event.target.value)}
            style={inputStyle}
          />
        </div>
      </div>
      <p style={{ margin: 0, color: "#667085", fontSize: 12 }}>
        At most {options.max_days} days. Access stops automatically at the end time.
      </p>

      <fieldset style={{ border: "none", padding: 0, margin: 0 }}>
        <legend>Scope</legend>
        <label style={{ display: "block" }}>
          <input
            type="radio"
            name="delegation-scope"
            value="ALL"
            checked={scopeType === "ALL"}
            onChange={() => setScopeType("ALL")}
          />{" "}
          All {authority === "MANAGER_APPROVAL" ? "assessments submitted to them" : "committee sign-offs"}
        </label>
        <label style={{ display: "block" }}>
          <input
            type="radio"
            name="delegation-scope"
            value="ASSESSMENT"
            checked={scopeType === "ASSESSMENT"}
            onChange={() => setScopeType("ASSESSMENT")}
          />{" "}
          One assessment only
        </label>
        {scopeType === "ASSESSMENT" && (
          <select
            aria-label="Assessment covered by this delegation"
            value={scopeAssessmentId}
            onChange={(event) => setScopeAssessmentId(event.target.value)}
            style={{ ...inputStyle, marginTop: 6 }}
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

      <div>
        <label htmlFor="delegation-reason">
          Reason
          <RequiredMarker />
        </label>
        <textarea
          id="delegation-reason"
          rows={2}
          placeholder="e.g. Annual leave 12–19 October"
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          style={inputStyle}
        />
      </div>

      {error && (
        <p role="alert" style={{ color: "#b91c1c", margin: 0 }}>
          {error}
        </p>
      )}
      {success && (
        <p role="status" style={{ color: "#0f766e", margin: 0 }}>
          {success}
        </p>
      )}

      <div>
        <button type="submit" className="primary-button" disabled={submitting}>
          {submitting ? "Saving…" : "Delegate"}
        </button>
      </div>
    </form>
  );
}
