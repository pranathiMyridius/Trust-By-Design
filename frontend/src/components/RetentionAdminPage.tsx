// P5: Retention & legal holds -- versioned retention policy (propose,
// independent approval), current and historical legal holds, and the
// read-only eligibility report. Every rule is enforced by the backend;
// action availability and the reasons an action is unavailable come from
// the API. Retention periods are provisional: pending governance approval,
// not compliance-approved.
import { useCallback, useEffect, useState } from "react";
import { POLICY_PENDING_LABEL } from "../api/governanceRecords";
import {
  ELIGIBILITY_LABEL,
  decideRetentionPolicy,
  getEligibilityReport,
  listLegalHolds,
  listRetentionPolicies,
  proposeRetentionPolicy,
  type EligibilityReport,
  type EligibilityStatus,
  type LegalHoldSummary,
  type PolicyListing,
  type PolicyVersion,
  type RecordTypePolicies,
  type ReportFilters,
  type RetentionPermissions,
  type VersionStatus,
} from "../api/retention";
import { friendlyError } from "../utils/errorMessages";

const VERSION_STYLE: Record<VersionStatus, [string, string]> = {
  PROPOSED: ["warn", "Pending approval"],
  ACTIVE: ["ok", "Active"],
  SUPERSEDED: ["grey", "Superseded"],
  REJECTED: ["danger", "Rejected"],
};

const ELIGIBILITY_TONE: Record<EligibilityStatus, string> = {
  ELIGIBLE: "info",
  RETAINED: "grey",
  LEGAL_HOLD: "rose",
  NOT_STARTED: "grey",
  INVALID_DATE: "amber",
  NO_POLICY: "amber",
  INVALID_POLICY: "amber",
  SOFT_DELETED: "grey",
};

function Badge({ tone, children }: { tone: string; children: React.ReactNode }) {
  return <span className={`ret-badge ret-badge-${tone}`}>{children}</span>;
}

function when(value: string | null) {
  return value ? new Date(value).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "—";
}

function day(value: string | null) {
  return value ? new Date(value).toLocaleDateString(undefined, { dateStyle: "medium" }) : "—";
}

export function RetentionPolicyBanner() {
  return (
    <p role="note" className="ret-banner">
      <strong>Provisional: {POLICY_PENDING_LABEL}.</strong> Retention periods, bounds and approval rules are provisional defaults.
      Periods are not compliance-approved.
    </p>
  );
}

// -- policies -------------------------------------------------------------------

function DecisionPanel({ version, onDone }: { version: PolicyVersion; onDone: () => void }) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const allowed = version.actions?.decide.allowed ?? false;

  async function run(decision: "APPROVE" | "REJECT") {
    if (!reason.trim()) {
      setError("Enter the reason for your decision.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await decideRetentionPolicy(version.id, decision, reason.trim());
      setReason("");
      onDone();
    } catch (err) {
      setError(friendlyError(err, "The decision couldn't be recorded."));
    } finally {
      setBusy(false);
    }
  }

  if (!allowed) {
    return (
      <p className="ret-muted ret-note">
        You can't decide this proposal: {version.actions?.decide.reason ?? "not permitted."}
      </p>
    );
  }
  return (
    <div className="ret-decision">
      <label className="ret-field">
        Decision reason (required)
        <textarea aria-label="Decision reason" rows={2} value={reason} onChange={(e) => setReason(e.target.value)} />
      </label>
      <div className="ret-actions">
        <button className="primary-button" disabled={busy} onClick={() => run("APPROVE")}>Approve proposal</button>
        <button className="secondary-button" disabled={busy} onClick={() => run("REJECT")}>Reject proposal</button>
      </div>
      {error && <p role="alert" className="field-error">{error}</p>}
    </div>
  );
}

function ProposeForm({ recordType, permissions, onDone }: { recordType: string; permissions: RetentionPermissions; onDone: () => void }) {
  const [days, setDays] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);

  async function submit() {
    const value = Number(days);
    const problems = [
      (!Number.isInteger(value) || value < permissions.min_retention_days || value > permissions.max_retention_days) &&
        `Enter a whole number of days between ${permissions.min_retention_days} and ${permissions.max_retention_days}.`,
      reason.trim().length < permissions.min_reason_length &&
        `Enter a business justification of at least ${permissions.min_reason_length} characters.`,
    ].filter(Boolean) as string[];
    if (problems.length) {
      setErrors(problems);
      return;
    }
    setBusy(true);
    setErrors([]);
    try {
      await proposeRetentionPolicy({ record_type: recordType, retention_days: value, change_reason: reason.trim() });
      setDays("");
      setReason("");
      onDone();
    } catch (err) {
      setErrors([friendlyError(err, "The proposal couldn't be saved.")]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="ret-propose">
      <label className="ret-field">
        Proposed period (days)
        <input aria-label="Proposed retention days" type="number" min={permissions.min_retention_days} max={permissions.max_retention_days} value={days} onChange={(e) => setDays(e.target.value)} />
      </label>
      <label className="ret-field">
        Business justification (required, at least {permissions.min_reason_length} characters)
        <textarea aria-label="Change justification" rows={2} value={reason} onChange={(e) => setReason(e.target.value)} />
      </label>
      <div className="ret-propose-actions">
        <button className="primary-button" disabled={busy} onClick={submit}>
          {permissions.require_independent_approval ? "Submit for independent approval" : "Apply change"}
        </button>
        <span className="ret-muted ret-small">
          {permissions.require_independent_approval
            ? `Takes effect only when approved by another user who is: ${permissions.approvers}.`
            : "Independent approval is switched off in configuration; the change applies at once (still versioned and audited)."}
        </span>
      </div>
      {errors.length > 0 && (
        <ul role="alert" className="field-error ret-errors">
          {errors.map((e) => <li key={e}>{e}</li>)}
        </ul>
      )}
    </div>
  );
}

function RecordTypeCard({ policies, permissions, onChange }: { policies: RecordTypePolicies; permissions: RetentionPermissions; onChange: () => void }) {
  const { active, pending } = policies;
  return (
    <section className="content-card ret-card" aria-label={`${policies.record_type} retention policy`}>
      <header className="ret-card-header">
        <h3>{policies.record_type} records</h3>
        <span className="ret-muted">Period runs from {policies.basis.replace(/_/g, " ").toLowerCase()}</span>
      </header>
      <div className="ret-card-body">
      {active ? (
        <p className="ret-inforce">
          In force: <strong data-testid="active-retention-days">{active.retention_days} days</strong> (version {active.version}, since {day(active.effective_from)}){" "}
          <Badge tone="amber">{POLICY_PENDING_LABEL}</Badge>
        </p>
      ) : (
        <p role="alert" className="field-error">No version is in force: no record of this type is ever eligible.</p>
      )}

      {pending && (
        <div className="ret-pending" aria-label="Pending proposal">
          <strong>Pending proposal (version {pending.version})</strong>
          <table className="ret-mini-table">
            <thead>
              <tr><th></th><th>Current</th><th>Proposed</th></tr>
            </thead>
            <tbody>
              <tr><td>Retention period</td><td>{pending.previous_retention_days ?? "—"} days</td><td><strong>{pending.retention_days} days</strong></td></tr>
              <tr><td>Proposed by</td><td colSpan={2}>{pending.proposed_by} on {when(pending.proposed_at)}</td></tr>
              <tr><td>Justification</td><td colSpan={2}>{pending.change_reason}</td></tr>
            </tbody>
          </table>
          <DecisionPanel version={pending} onDone={onChange} />
        </div>
      )}

      {permissions.can_propose && !pending && (
        <>
          <h4 className="ret-subtitle">Propose a new period</h4>
          <ProposeForm recordType={policies.record_type} permissions={permissions} onDone={onChange} />
        </>
      )}
      {!permissions.can_propose && (
        <p className="ret-muted ret-small">Proposing a change needs: {permissions.proposers}.</p>
      )}

      <h4 className="ret-subtitle">Version history (read-only)</h4>
      <div className="ret-table-wrap">
        <table aria-label={`${policies.record_type} version history`} className="ret-table">
          <thead>
            <tr>
              <th>Version</th><th>Status</th><th>Period</th><th>Previous</th><th>Effective from</th><th>Proposed by</th><th>Decided by</th><th>Reasons</th>
            </tr>
          </thead>
          <tbody>
            {policies.versions.map((v) => {
              const [tone, label] = VERSION_STYLE[v.status];
              return (
                <tr key={v.id}>
                  <td>v{v.version}</td>
                  <td><Badge tone={tone}>{label}</Badge></td>
                  <td>{v.retention_days} days</td>
                  <td>{v.previous_retention_days != null ? `${v.previous_retention_days} days` : "—"}</td>
                  <td>{day(v.effective_from)}</td>
                  <td>{v.proposed_by ?? "—"}<br /><span className="ret-muted">{when(v.proposed_at)}</span></td>
                  <td>{v.decided_by ?? "—"}<br /><span className="ret-muted">{when(v.decided_at)}</span></td>
                  <td>
                    <div>{v.change_reason}</div>
                    {v.decision_reason && v.decision_reason !== v.change_reason && <div className="ret-muted">Decision: {v.decision_reason}</div>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      </div>
    </section>
  );
}

// -- legal holds ----------------------------------------------------------------

function LegalHoldsSection() {
  const [rows, setRows] = useState<LegalHoldSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [currentOnly, setCurrentOnly] = useState(false);

  useEffect(() => {
    let cancelled = false;
    listLegalHolds(currentOnly)
      .then((data) => !cancelled && (setRows(data.items), setError(null)))
      .catch((err) => !cancelled && (setRows([]), setError(friendlyError(err, "Legal holds couldn't be loaded."))));
    return () => {
      cancelled = true;
    };
  }, [currentOnly]);

  return (
    <section className="content-card ret-card" aria-label="Legal holds">
      <header className="ret-card-header">
        <h3>Legal holds</h3>
        <label className="ret-check">
          <input type="checkbox" checked={currentOnly} onChange={(e) => setCurrentOnly(e.target.checked)} /> Current holds only
        </label>
      </header>
      <div className="ret-card-body">
      <p className="ret-muted ret-intro">
        Holds are placed and released (each with a reason) from the assessment's Retention panel. A hold overrides retention
        eligibility until a different authorized user releases it. Released holds stay in the history.
      </p>
      {error && <p role="alert" className="field-error">{error}</p>}
      {rows === null ? (
        <p>Loading…</p>
      ) : rows.length === 0 ? (
        <p className="ret-empty">No legal holds {currentOnly ? "in place" : "recorded"} on assessments you can see.</p>
      ) : (
        <ul className="ret-holds">
          {rows.map((row) => (
            <li key={row.assessment_id} className="ret-hold">
              <strong>{row.reference ?? `Assessment #${row.assessment_id}`}</strong>{" "}
              {row.legal_hold ? <Badge tone="rose">On hold</Badge> : <Badge tone="grey">Released</Badge>}
              <ol className="ret-history">
                {row.history.map((h) => (
                  <li key={h.id}>
                    {when(h.at)} — <strong>{h.action === "SET" ? "Placed" : "Released"}</strong> by {h.actor}: {h.reason}
                    {h.matter_reference ? ` (matter ${h.matter_reference})` : ""}
                  </li>
                ))}
              </ol>
            </li>
          ))}
        </ul>
      )}
      </div>
    </section>
  );
}

// -- eligibility report -----------------------------------------------------------

const STATUS_FILTERS: [string, string][] = [
  ["", "All"],
  ["eligible", "Eligible"],
  ["upcoming", "Eligible soon"],
  ["held", "On legal hold"],
  ["retained", "Within period"],
  ["not_started", "Not started"],
  ["invalid", "Invalid data"],
  ["no_policy", "No policy"],
];

function EligibilityReportSection({ canIncludeDeleted }: { canIncludeDeleted: boolean }) {
  const [filters, setFilters] = useState<ReportFilters>({ status: "", legal_hold: "", within_days: "90", sort: "eligible_at", order: "asc", page: 1, page_size: 25 });
  const [report, setReport] = useState<EligibilityReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getEligibilityReport(filters)
      .then((data) => !cancelled && (setReport(data), setError(null)))
      .catch((err) => !cancelled && setError(friendlyError(err, "The eligibility report couldn't be loaded.")));
    return () => {
      cancelled = true;
    };
  }, [filters]);

  function update(patch: Partial<ReportFilters>) {
    setFilters((current) => ({ ...current, page: 1, ...patch }));
  }

  const pages = report ? Math.max(1, Math.ceil(report.total / report.page_size)) : 1;

  return (
    <section className="content-card ret-card" aria-label="Retention eligibility report">
      <header className="ret-card-header">
        <h3>Retention eligibility report (read-only)</h3>
      </header>
      <div className="ret-card-body">
      <p className="ret-muted ret-intro">
        Calculated by the server from the policy version in force. Eligibility means a record may be put forward for a
        controlled lifecycle review. It does not authorize deletion, and nothing is purged. Assessment content is not shown.
      </p>
      <div className="ret-filters">
        <label className="ret-field">
          Status
          <select aria-label="Eligibility status filter" value={filters.status} onChange={(e) => update({ status: e.target.value })}>
            {STATUS_FILTERS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>
        {filters.status === "upcoming" && (
          <label className="ret-field">
            Within (days)
            <input aria-label="Within days" type="number" min={0} className="ret-narrow" value={filters.within_days} onChange={(e) => update({ within_days: e.target.value })} />
          </label>
        )}
        <label className="ret-field">
          Legal hold
          <select aria-label="Legal hold filter" value={filters.legal_hold} onChange={(e) => update({ legal_hold: e.target.value as ReportFilters["legal_hold"] })}>
            <option value="">Any</option>
            <option value="true">On hold</option>
            <option value="false">Not on hold</option>
          </select>
        </label>
        <label className="ret-field">
          Policy version id
          <input aria-label="Policy version filter" className="ret-narrow" value={filters.policy_version_id ?? ""} onChange={(e) => update({ policy_version_id: e.target.value.replace(/\D/g, "") })} />
        </label>
        <label className="ret-field">
          Decided from
          <input aria-label="Decided from" type="date" value={filters.basis_from ?? ""} onChange={(e) => update({ basis_from: e.target.value })} />
        </label>
        <label className="ret-field">
          Decided to
          <input aria-label="Decided to" type="date" value={filters.basis_to ?? ""} onChange={(e) => update({ basis_to: e.target.value })} />
        </label>
        <label className="ret-field">
          Sort by
          <select aria-label="Sort by" value={`${filters.sort}:${filters.order}`} onChange={(e) => { const [sort, order] = e.target.value.split(":"); update({ sort: sort as ReportFilters["sort"], order: order as ReportFilters["order"] }); }}>
            <option value="eligible_at:asc">Eligibility date (earliest)</option>
            <option value="eligible_at:desc">Eligibility date (latest)</option>
            <option value="basis_date:asc">Decision date (oldest)</option>
            <option value="record_id:asc">Record id</option>
          </select>
        </label>
        {canIncludeDeleted && (
          <label className="ret-check">
            <input type="checkbox" checked={!!filters.include_deleted} onChange={(e) => update({ include_deleted: e.target.checked })} /> Include soft-deleted
          </label>
        )}
      </div>

      {error && <p role="alert" className="field-error">{error}</p>}
      {report && (
        <p className="ret-summary" aria-label="Report summary">
          {Object.entries(report.summary).filter(([, n]) => n > 0).map(([code, n]) => `${ELIGIBILITY_LABEL[code as EligibilityStatus]}: ${n}`).join(" · ") || "No records in scope."}
          {report.active_policy && ` · Policy in force: v${report.active_policy.version}, ${report.active_policy.retention_days} days (${POLICY_PENDING_LABEL})`}
        </p>
      )}
      {!report && !error ? (
        <p>Loading…</p>
      ) : report && report.items.length === 0 ? (
        <p className="ret-empty">No records match these filters.</p>
      ) : report ? (
        <div className="ret-table-wrap">
          <table aria-label="Eligibility results" className="ret-table">
            <thead>
              <tr>
                <th>Record</th><th>Type</th><th>Policy</th><th>Retention start</th><th>Eligibility date</th><th>Status</th><th>Legal hold</th><th>Explanation</th>
              </tr>
            </thead>
            <tbody>
              {report.items.map((row) => {
                const tone = ELIGIBILITY_TONE[row.eligibility_status];
                return (
                  <tr key={row.record_id} data-testid={`eligibility-row-${row.record_id}`}>
                    <td>{row.reference ?? `#${row.record_id}`}<br /><span className="ret-muted">{row.assessment_status}</span></td>
                    <td>{row.record_type}</td>
                    <td>{row.policy_version != null ? `v${row.policy_version} (${row.retention_days} d)` : "—"}</td>
                    <td>{day(row.basis_date)}</td>
                    <td>{day(row.eligible_at)}</td>
                    <td><Badge tone={tone}>{ELIGIBILITY_LABEL[row.eligibility_status]}</Badge></td>
                    <td>{row.legal_hold ? <Badge tone="rose">Yes</Badge> : "No"}</td>
                    <td>{row.reason}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
      {report && report.total > report.page_size && (
        <div className="ret-pager">
          <button className="secondary-button" disabled={(filters.page ?? 1) <= 1} onClick={() => setFilters((f) => ({ ...f, page: (f.page ?? 1) - 1 }))}>Previous</button>
          Page {report.page} of {pages} ({report.total} records)
          <button className="secondary-button" disabled={(filters.page ?? 1) >= pages} onClick={() => setFilters((f) => ({ ...f, page: (f.page ?? 1) + 1 }))}>Next</button>
        </div>
      )}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------

export default function RetentionAdminPage({ userRole }: { userRole: string }) {
  const [listing, setListing] = useState<PolicyListing | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    listRetentionPolicies()
      .then((data) => {
        setListing(data);
        setError(null);
      })
      .catch((err) => setError(friendlyError(err, "Retention policies couldn't be loaded.")));
  }, []);

  useEffect(load, [load]);

  const permissions = listing?.permissions;
  return (
    <div className="ret-page">
      <div className="page-header">
        <div>
          <h2>Retention &amp; Legal Holds</h2>
          <p>
            Versioned retention periods by record type, legal holds, and which records are eligible for a controlled lifecycle
            review. A change never edits a previous version, and nothing is ever physically deleted here.
          </p>
        </div>
      </div>
      <RetentionPolicyBanner />
      {error && <p role="alert" className="field-error">{error}</p>}
      {!listing && !error && <p>Loading…</p>}
      {listing && permissions && (
        <>
          {listing.record_types.map((policies) => (
            <RecordTypeCard key={policies.record_type} policies={policies} permissions={permissions} onChange={load} />
          ))}
          <LegalHoldsSection />
          {permissions.can_read_report ? (
            <EligibilityReportSection canIncludeDeleted={userRole === "ADMIN" || userRole === "AUDITOR"} />
          ) : (
            <section className="content-card ret-card">
              <p className="ret-empty">The eligibility report is available to authorized report readers only.</p>
            </section>
          )}
        </>
      )}
    </div>
  );
}
