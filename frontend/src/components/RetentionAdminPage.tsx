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

const VERSION_STYLE: Record<VersionStatus, [string, string, string]> = {
  PROPOSED: ["#fef9c3", "#854d0e", "Pending approval"],
  ACTIVE: ["#dcfce7", "#166534", "Active"],
  SUPERSEDED: ["#e5e7eb", "#4b5563", "Superseded"],
  REJECTED: ["#fee2e2", "#991b1b", "Rejected"],
};

const ELIGIBILITY_STYLE: Record<EligibilityStatus, [string, string]> = {
  ELIGIBLE: ["#dbeafe", "#1e40af"],
  RETAINED: ["#f3f4f6", "#374151"],
  LEGAL_HOLD: ["#ffe4e6", "#9f1239"],
  NOT_STARTED: ["#f3f4f6", "#374151"],
  INVALID_DATE: ["#fef3c7", "#92400e"],
  NO_POLICY: ["#fef3c7", "#92400e"],
  INVALID_POLICY: ["#fef3c7", "#92400e"],
  SOFT_DELETED: ["#e5e7eb", "#4b5563"],
};

function Badge({ background, color, children }: { background: string; color: string; children: React.ReactNode }) {
  return <span style={{ background, color, borderRadius: 4, padding: "1px 6px", fontSize: 12, whiteSpace: "nowrap" }}>{children}</span>;
}

function when(value: string | null) {
  return value ? new Date(value).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "—";
}

function day(value: string | null) {
  return value ? new Date(value).toLocaleDateString(undefined, { dateStyle: "medium" }) : "—";
}

export function RetentionPolicyBanner() {
  return (
    <p role="note" style={{ margin: "0 0 12px", padding: "6px 10px", borderRadius: 6, background: "#fff7ed", color: "#9a3412", fontSize: 13 }}>
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
      <p style={{ fontSize: 13, color: "#6b7280", margin: "8px 0 0" }}>
        You can't decide this proposal: {version.actions?.decide.reason ?? "not permitted."}
      </p>
    );
  }
  return (
    <div style={{ display: "grid", gap: 6, marginTop: 8 }}>
      <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 13 }}>
        Decision reason (required)
        <textarea aria-label="Decision reason" rows={2} value={reason} onChange={(e) => setReason(e.target.value)} />
      </label>
      <div style={{ display: "flex", gap: 8 }}>
        <button className="primary-button" disabled={busy} onClick={() => run("APPROVE")}>Approve proposal</button>
        <button className="doc-action-button" disabled={busy} onClick={() => run("REJECT")}>Reject proposal</button>
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
    <div style={{ display: "grid", gap: 8, gridTemplateColumns: "minmax(140px, 200px) 1fr", alignItems: "start" }}>
      <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 13 }}>
        Proposed period (days)
        <input aria-label="Proposed retention days" type="number" min={permissions.min_retention_days} max={permissions.max_retention_days} value={days} onChange={(e) => setDays(e.target.value)} />
      </label>
      <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 13 }}>
        Business justification (required, at least {permissions.min_reason_length} characters)
        <textarea aria-label="Change justification" rows={2} value={reason} onChange={(e) => setReason(e.target.value)} />
      </label>
      <div style={{ gridColumn: "1 / -1" }}>
        <button className="primary-button" disabled={busy} onClick={submit}>
          {permissions.require_independent_approval ? "Submit for independent approval" : "Apply change"}
        </button>
        <span style={{ marginLeft: 8, fontSize: 12, color: "#6b7280" }}>
          {permissions.require_independent_approval
            ? `Takes effect only when approved by another user who is: ${permissions.approvers}.`
            : "Independent approval is switched off in configuration; the change applies at once (still versioned and audited)."}
        </span>
      </div>
      {errors.length > 0 && (
        <ul role="alert" className="field-error" style={{ gridColumn: "1 / -1", margin: 0 }}>
          {errors.map((e) => <li key={e}>{e}</li>)}
        </ul>
      )}
    </div>
  );
}

function RecordTypeCard({ policies, permissions, onChange }: { policies: RecordTypePolicies; permissions: RetentionPermissions; onChange: () => void }) {
  const { active, pending } = policies;
  return (
    <section className="content-card" aria-label={`${policies.record_type} retention policy`}>
      <h3 style={{ marginTop: 0 }}>
        {policies.record_type} records <span style={{ fontSize: 13, fontWeight: 400, color: "#6b7280" }}>· period runs from {policies.basis.replace(/_/g, " ").toLowerCase()}</span>
      </h3>
      {active ? (
        <p style={{ margin: "0 0 8px" }}>
          In force: <strong data-testid="active-retention-days">{active.retention_days} days</strong> (version {active.version}, since {day(active.effective_from)}){" "}
          <Badge background="#fff7ed" color="#9a3412">{POLICY_PENDING_LABEL}</Badge>
        </p>
      ) : (
        <p role="alert" className="field-error">No version is in force: no record of this type is ever eligible.</p>
      )}

      {pending && (
        <div style={{ border: "1px solid #fde68a", borderRadius: 6, padding: 10, margin: "8px 0" }} aria-label="Pending proposal">
          <strong>Pending proposal (version {pending.version})</strong>
          <table style={{ width: "100%", fontSize: 13, marginTop: 6, borderCollapse: "collapse" }}>
            <thead>
              <tr><th style={{ textAlign: "left" }}></th><th style={{ textAlign: "left" }}>Current</th><th style={{ textAlign: "left" }}>Proposed</th></tr>
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
          <h4 style={{ margin: "12px 0 6px", fontSize: 14 }}>Propose a new period</h4>
          <ProposeForm recordType={policies.record_type} permissions={permissions} onDone={onChange} />
        </>
      )}
      {!permissions.can_propose && (
        <p style={{ fontSize: 12, color: "#6b7280" }}>Proposing a change needs: {permissions.proposers}.</p>
      )}

      <h4 style={{ margin: "12px 0 6px", fontSize: 14 }}>Version history (read-only)</h4>
      <div style={{ overflowX: "auto" }}>
        <table aria-label={`${policies.record_type} version history`} style={{ width: "100%", fontSize: 13, borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ textAlign: "left" }}>
              <th>Version</th><th>Status</th><th>Period</th><th>Previous</th><th>Effective from</th><th>Proposed by</th><th>Decided by</th><th>Reasons</th>
            </tr>
          </thead>
          <tbody>
            {policies.versions.map((v) => {
              const [bg, fg, label] = VERSION_STYLE[v.status];
              return (
                <tr key={v.id} style={{ borderTop: "1px solid #e5e7eb", verticalAlign: "top" }}>
                  <td>v{v.version}</td>
                  <td><Badge background={bg} color={fg}>{label}</Badge></td>
                  <td>{v.retention_days} days</td>
                  <td>{v.previous_retention_days != null ? `${v.previous_retention_days} days` : "—"}</td>
                  <td>{day(v.effective_from)}</td>
                  <td>{v.proposed_by ?? "—"}<br /><span style={{ color: "#6b7280" }}>{when(v.proposed_at)}</span></td>
                  <td>{v.decided_by ?? "—"}<br /><span style={{ color: "#6b7280" }}>{when(v.decided_at)}</span></td>
                  <td>
                    <div>{v.change_reason}</div>
                    {v.decision_reason && v.decision_reason !== v.change_reason && <div style={{ color: "#6b7280" }}>Decision: {v.decision_reason}</div>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
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
    <section className="content-card" aria-label="Legal holds">
      <h3 style={{ marginTop: 0 }}>Legal holds</h3>
      <p style={{ fontSize: 13, color: "#6b7280", marginTop: 0 }}>
        Holds are placed and released (each with a reason) from the assessment's Retention panel. A hold overrides retention
        eligibility until a different authorized user releases it. Released holds stay in the history.
      </p>
      <label style={{ fontSize: 13 }}>
        <input type="checkbox" checked={currentOnly} onChange={(e) => setCurrentOnly(e.target.checked)} /> Current holds only
      </label>
      {error && <p role="alert" className="field-error">{error}</p>}
      {rows === null ? (
        <p>Loading…</p>
      ) : rows.length === 0 ? (
        <p style={{ fontSize: 13 }}>No legal holds {currentOnly ? "in place" : "recorded"} on assessments you can see.</p>
      ) : (
        <ul style={{ listStyle: "none", padding: 0, margin: "8px 0 0", display: "grid", gap: 8 }}>
          {rows.map((row) => (
            <li key={row.assessment_id} style={{ border: "1px solid #e5e7eb", borderRadius: 6, padding: 8, fontSize: 13 }}>
              <strong>{row.reference ?? `Assessment #${row.assessment_id}`}</strong>{" "}
              {row.legal_hold ? <Badge background="#ffe4e6" color="#9f1239">On hold</Badge> : <Badge background="#e5e7eb" color="#4b5563">Released</Badge>}
              <ol style={{ margin: "4px 0 0", paddingLeft: 18 }}>
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
  const control: React.CSSProperties = { display: "flex", flexDirection: "column", gap: 4, fontSize: 13 };

  return (
    <section className="content-card" aria-label="Retention eligibility report">
      <h3 style={{ marginTop: 0 }}>Retention eligibility report (read-only)</h3>
      <p style={{ fontSize: 13, color: "#6b7280", marginTop: 0 }}>
        Calculated by the server from the policy version in force. Eligibility means a record may be put forward for a
        controlled lifecycle review. It does not authorize deletion, and nothing is purged. Assessment content is not shown.
      </p>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "end", marginBottom: 10 }}>
        <label style={control}>
          Status
          <select aria-label="Eligibility status filter" value={filters.status} onChange={(e) => update({ status: e.target.value })}>
            {STATUS_FILTERS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>
        {filters.status === "upcoming" && (
          <label style={control}>
            Within (days)
            <input aria-label="Within days" type="number" min={0} style={{ width: 90 }} value={filters.within_days} onChange={(e) => update({ within_days: e.target.value })} />
          </label>
        )}
        <label style={control}>
          Legal hold
          <select aria-label="Legal hold filter" value={filters.legal_hold} onChange={(e) => update({ legal_hold: e.target.value as ReportFilters["legal_hold"] })}>
            <option value="">Any</option>
            <option value="true">On hold</option>
            <option value="false">Not on hold</option>
          </select>
        </label>
        <label style={control}>
          Policy version id
          <input aria-label="Policy version filter" style={{ width: 90 }} value={filters.policy_version_id ?? ""} onChange={(e) => update({ policy_version_id: e.target.value.replace(/\D/g, "") })} />
        </label>
        <label style={control}>
          Decided from
          <input aria-label="Decided from" type="date" value={filters.basis_from ?? ""} onChange={(e) => update({ basis_from: e.target.value })} />
        </label>
        <label style={control}>
          Decided to
          <input aria-label="Decided to" type="date" value={filters.basis_to ?? ""} onChange={(e) => update({ basis_to: e.target.value })} />
        </label>
        <label style={control}>
          Sort by
          <select aria-label="Sort by" value={`${filters.sort}:${filters.order}`} onChange={(e) => { const [sort, order] = e.target.value.split(":"); update({ sort: sort as ReportFilters["sort"], order: order as ReportFilters["order"] }); }}>
            <option value="eligible_at:asc">Eligibility date (earliest)</option>
            <option value="eligible_at:desc">Eligibility date (latest)</option>
            <option value="basis_date:asc">Decision date (oldest)</option>
            <option value="record_id:asc">Record id</option>
          </select>
        </label>
        {canIncludeDeleted && (
          <label style={{ fontSize: 13 }}>
            <input type="checkbox" checked={!!filters.include_deleted} onChange={(e) => update({ include_deleted: e.target.checked })} /> Include soft-deleted
          </label>
        )}
      </div>

      {error && <p role="alert" className="field-error">{error}</p>}
      {report && (
        <p style={{ fontSize: 13, margin: "0 0 8px" }} aria-label="Report summary">
          {Object.entries(report.summary).filter(([, n]) => n > 0).map(([code, n]) => `${ELIGIBILITY_LABEL[code as EligibilityStatus]}: ${n}`).join(" · ") || "No records in scope."}
          {report.active_policy && ` · Policy in force: v${report.active_policy.version}, ${report.active_policy.retention_days} days (${POLICY_PENDING_LABEL})`}
        </p>
      )}
      {!report && !error ? (
        <p>Loading…</p>
      ) : report && report.items.length === 0 ? (
        <p style={{ fontSize: 13 }}>No records match these filters.</p>
      ) : report ? (
        <div style={{ overflowX: "auto" }}>
          <table aria-label="Eligibility results" style={{ width: "100%", fontSize: 13, borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ textAlign: "left" }}>
                <th>Record</th><th>Type</th><th>Policy</th><th>Retention start</th><th>Eligibility date</th><th>Status</th><th>Legal hold</th><th>Explanation</th>
              </tr>
            </thead>
            <tbody>
              {report.items.map((row) => {
                const [bg, fg] = ELIGIBILITY_STYLE[row.eligibility_status];
                return (
                  <tr key={row.record_id} data-testid={`eligibility-row-${row.record_id}`} style={{ borderTop: "1px solid #e5e7eb", verticalAlign: "top" }}>
                    <td>{row.reference ?? `#${row.record_id}`}<br /><span style={{ color: "#6b7280" }}>{row.assessment_status}</span></td>
                    <td>{row.record_type}</td>
                    <td>{row.policy_version != null ? `v${row.policy_version} (${row.retention_days} d)` : "—"}</td>
                    <td>{day(row.basis_date)}</td>
                    <td>{day(row.eligible_at)}</td>
                    <td><Badge background={bg} color={fg}>{ELIGIBILITY_LABEL[row.eligibility_status]}</Badge></td>
                    <td>{row.legal_hold ? "Yes" : "No"}</td>
                    <td>{row.reason}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
      {report && report.total > report.page_size && (
        <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 8, fontSize: 13 }}>
          <button className="doc-action-button" disabled={(filters.page ?? 1) <= 1} onClick={() => setFilters((f) => ({ ...f, page: (f.page ?? 1) - 1 }))}>Previous</button>
          Page {report.page} of {pages} ({report.total} records)
          <button className="doc-action-button" disabled={(filters.page ?? 1) >= pages} onClick={() => setFilters((f) => ({ ...f, page: (f.page ?? 1) + 1 }))}>Next</button>
        </div>
      )}
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
    <div>
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
            <section className="content-card">
              <p style={{ fontSize: 13, margin: 0 }}>The eligibility report is available to authorized report readers only.</p>
            </section>
          )}
        </>
      )}
    </div>
  );
}
