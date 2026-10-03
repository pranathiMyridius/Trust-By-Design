// P3: Segregation-of-Duties exceptions -- request, independent approval,
// declaration, revocation, periodic review and the full history. Every
// rule is enforced by the backend; each action's availability and the
// reason it is unavailable come from the API (`actions`). The policy is
// provisional: pending governance approval.
import { Fragment, useEffect, useState } from "react";
import type { Assessment } from "../api/assessments";
import type { CurrentUser } from "../api/auth";
import { POLICY_PENDING_LABEL } from "../api/governanceRecords";
import {
  SOD_TYPES,
  createSodException,
  declareSodException,
  decideSodException,
  listSodCandidates,
  listSodExceptions,
  reviewSodException,
  revokeSodException,
  submitSodException,
  type Candidate,
  type RiskLevel,
  type SodException,
  type SodScope,
  type SodStatus,
  type SodType,
} from "../api/sodExceptions";
import { friendlyError } from "../utils/errorMessages";

const STATUS_STYLE: Record<SodStatus, [string, string, string]> = {
  DRAFT: ["#f3f4f6", "#374151", "Draft"],
  PENDING_APPROVAL: ["#fef9c3", "#854d0e", "Pending approval"],
  APPROVED: ["#dcfce7", "#166534", "Approved"],
  REJECTED: ["#fee2e2", "#991b1b", "Rejected"],
  EXPIRED: ["#e5e7eb", "#4b5563", "Expired"],
  REVOKED: ["#ffe4e6", "#9f1239", "Revoked"],
};

const FLAG_TEXT: Record<string, string> = {
  REPEATED: "Repeated exception",
  EXPIRING_SOON: "Expiring soon",
  DECLARATION_MISSING: "Declaration missing (not usable yet)",
};

export function SodStatusBadge({ status }: { status: SodStatus }) {
  const [background, color, label] = STATUS_STYLE[status];
  return <span style={{ background, color, borderRadius: 4, padding: "1px 6px", fontSize: 12, whiteSpace: "nowrap" }}>{label}</span>;
}

function when(value: string | null) {
  return value ? new Date(value).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "—";
}

function PolicyBanner() {
  return (
    <p role="note" style={{ margin: "0 0 12px", padding: "6px 10px", borderRadius: 6, background: "#fff7ed", color: "#9a3412", fontSize: 13 }}>
      <strong>{POLICY_PENDING_LABEL}:</strong> the approver tiers, conflict rules and durations applied here are provisional
      defaults, not approved policy.
    </p>
  );
}

// ---------------------------------------------------------------------------

function RequestForm({ assessments, onCreated }: { assessments: Assessment[]; onCreated: (created: SodException) => void }) {
  const [type, setType] = useState<SodType>("COMMITTEE_SEPARATION");
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [affected, setAffected] = useState("");
  const [assessmentId, setAssessmentId] = useState("");
  const [justification, setJustification] = useState("");
  const [workflowReason, setWorkflowReason] = useState("");
  const [controls, setControls] = useState("");
  const [risk, setRisk] = useState<RiskLevel>("LOW");
  const [start, setStart] = useState(() => new Date().toISOString().slice(0, 16));
  const [end, setEnd] = useState(() => new Date(Date.now() + 7 * 86400_000).toISOString().slice(0, 16));
  const [saving, setSaving] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);

  useEffect(() => {
    let cancelled = false;
    listSodCandidates(type)
      .then((rows) => !cancelled && setCandidates(rows))
      .catch(() => !cancelled && setCandidates([]));
    return () => {
      cancelled = true;
    };
  }, [type]);

  async function submit() {
    const missing = [
      !affected && "the person the exception is for",
      type === "COMMITTEE_SEPARATION" && !assessmentId && "the assessment",
      !justification.trim() && "the business justification",
      !workflowReason.trim() && "why the standard workflow can't be followed",
      !controls.trim() && "the compensating controls",
    ].filter(Boolean) as string[];
    if (missing.length) {
      setErrors(missing.map((m) => `Enter ${m}.`));
      return;
    }
    setSaving(true);
    setErrors([]);
    try {
      const created = await createSodException({
        exception_type: type,
        affected_user_id: Number(affected),
        assessment_id: assessmentId ? Number(assessmentId) : null,
        business_justification: justification.trim(),
        standard_workflow_reason: workflowReason.trim(),
        risk_level: risk,
        compensating_controls: controls.trim(),
        start_at: new Date(start).toISOString(),
        end_at: new Date(end).toISOString(),
      });
      onCreated(created);
      setJustification("");
      setWorkflowReason("");
      setControls("");
    } catch (err) {
      setErrors([friendlyError(err, "The request couldn't be saved.")]);
    } finally {
      setSaving(false);
    }
  }

  const label: React.CSSProperties = { display: "flex", flexDirection: "column", gap: 4, fontSize: 13 };
  return (
    <div style={{ display: "grid", gap: 10, gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))" }}>
      <label style={label}>
        Exception type
        <select aria-label="Exception type" value={type} onChange={(e) => { setType(e.target.value as SodType); setAffected(""); }}>
          {SOD_TYPES.map((t) => (
            <option key={t.value} value={t.value}>{t.label}</option>
          ))}
        </select>
        <span style={{ color: "#6b7280" }}>{SOD_TYPES.find((t) => t.value === type)?.help}</span>
      </label>
      <label style={label}>
        Affected person
        <select aria-label="Affected person" value={affected} onChange={(e) => setAffected(e.target.value)}>
          <option value="">Choose…</option>
          {candidates.map((c) => (
            <option key={c.id} value={String(c.id)}>{c.name}</option>
          ))}
        </select>
      </label>
      <label style={label}>
        Assessment {type === "ADMIN_COMMITTEE_DUAL_ROLE" && "(leave empty for enterprise-wide)"}
        <select aria-label="Assessment" value={assessmentId} onChange={(e) => setAssessmentId(e.target.value)}>
          <option value="">{type === "ADMIN_COMMITTEE_DUAL_ROLE" ? "Enterprise-wide (Committee tier)" : "Choose…"}</option>
          {assessments.map((a) => (
            <option key={a.id} value={String(a.id)}>{a.reference_id ?? `#${a.id}`} — {a.title}</option>
          ))}
        </select>
      </label>
      <label style={label}>
        Risk level
        <select aria-label="Risk level" value={risk} onChange={(e) => setRisk(e.target.value as RiskLevel)}>
          {["LOW", "MEDIUM", "HIGH", "CRITICAL"].map((r) => (
            <option key={r} value={r}>{r}</option>
          ))}
        </select>
      </label>
      <label style={label}>
        Start
        <input type="datetime-local" aria-label="Start" value={start} onChange={(e) => setStart(e.target.value)} />
      </label>
      <label style={label}>
        End (expiry)
        <input type="datetime-local" aria-label="End (expiry)" value={end} onChange={(e) => setEnd(e.target.value)} />
      </label>
      <label style={{ ...label, gridColumn: "1 / -1" }}>
        Business justification
        <textarea aria-label="Business justification" rows={2} value={justification} onChange={(e) => setJustification(e.target.value)} />
      </label>
      <label style={{ ...label, gridColumn: "1 / -1" }}>
        Why the standard workflow can't be followed
        <textarea aria-label="Why the standard workflow can't be followed" rows={2} value={workflowReason} onChange={(e) => setWorkflowReason(e.target.value)} />
      </label>
      <label style={{ ...label, gridColumn: "1 / -1" }}>
        Compensating controls
        <textarea aria-label="Compensating controls" rows={2} value={controls} onChange={(e) => setControls(e.target.value)} />
      </label>
      {errors.length > 0 && (
        <ul role="alert" className="field-error" style={{ gridColumn: "1 / -1", margin: 0 }}>
          {errors.map((e) => <li key={e}>{e}</li>)}
        </ul>
      )}
      <div style={{ gridColumn: "1 / -1" }}>
        <button type="button" className="primary-button" disabled={saving} onClick={submit}>
          {saving ? "Saving…" : "Save draft request"}
        </button>
        <span style={{ marginLeft: 8, fontSize: 13, color: "#6b7280" }}>A request grants nothing until it is independently approved.</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------

function ActionWithReason({
  label,
  allowed,
  reason,
  confirmLabel,
  onRun,
  placeholder,
  needsText = true,
}: {
  label: string;
  allowed: boolean;
  reason: string | null;
  confirmLabel?: string;
  onRun: (text: string) => Promise<void>;
  placeholder?: string;
  needsText?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [missing, setMissing] = useState(false);

  if (!allowed) {
    return (
      <button type="button" className="doc-action-button" disabled title={reason ?? undefined} aria-describedby={undefined}>
        {label}
      </button>
    );
  }
  if (!needsText) {
    return (
      <button type="button" className="doc-action-button" disabled={busy} onClick={async () => { setBusy(true); try { await onRun(""); } finally { setBusy(false); } }}>
        {label}
      </button>
    );
  }
  return open ? (
    <div style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 260 }}>
      <textarea aria-label={`${label}: rationale (required)`} placeholder={placeholder ?? "Rationale (required)"} rows={2} value={value}
        onChange={(e) => { setValue(e.target.value); if (e.target.value.trim()) setMissing(false); }} />
      {missing && <span className="field-error">A rationale is required.</span>}
      <div style={{ display: "flex", gap: 6 }}>
        <button type="button" className="doc-action-button" disabled={busy} onClick={async () => {
          if (!value.trim()) { setMissing(true); return; }
          setBusy(true);
          try { await onRun(value.trim()); setOpen(false); setValue(""); } finally { setBusy(false); }
        }}>{confirmLabel ?? label}</button>
        <button type="button" onClick={() => setOpen(false)}>Cancel</button>
      </div>
    </div>
  ) : (
    <button type="button" className="doc-action-button" onClick={() => setOpen(true)}>{label}</button>
  );
}

function ExceptionDetail({ exception, onChanged }: { exception: SodException; onChanged: (updated: SodException) => void }) {
  const [error, setError] = useState<string | null>(null);
  const a = exception.actions;

  async function run(fn: () => Promise<SodException>, failure: string) {
    setError(null);
    try {
      onChanged(await fn());
    } catch (err) {
      setError(friendlyError(err, failure));
    }
  }

  const unavailable = Object.entries(a).filter(([, v]) => !v.allowed).map(([k, v]) => [k, v.reason] as const);

  return (
    <div style={{ border: "1px solid #e5e7eb", borderRadius: 6, padding: 12, marginTop: 8 }} aria-label={`Exception ${exception.reference} details`}>
      <dl style={{ display: "grid", gridTemplateColumns: "max-content 1fr", gap: "4px 12px", margin: 0, fontSize: 13 }}>
        <dt>Conflicting roles</dt><dd style={{ margin: 0 }}>{exception.conflicting_roles.join("; ")}</dd>
        <dt>Assessment</dt><dd style={{ margin: 0 }}>{exception.assessment_id ? `#${exception.assessment_id}` : "Enterprise-wide"}</dd>
        <dt>Justification</dt><dd style={{ margin: 0 }}>{exception.business_justification}</dd>
        <dt>Why not standard</dt><dd style={{ margin: 0 }}>{exception.standard_workflow_reason}</dd>
        <dt>Compensating controls</dt><dd style={{ margin: 0 }}>{exception.compensating_controls}</dd>
        <dt>Period</dt><dd style={{ margin: 0 }}>{when(exception.start_at)} → {when(exception.end_at)}</dd>
        <dt>Approval tier</dt>
        <dd style={{ margin: 0 }}>
          {exception.tier ? `${exception.tier === "COMMITTEE" ? "Committee (Chair)" : "Standard"}${exception.tier_reasons.length ? ` — ${exception.tier_reasons.join("; ")}` : ""}` : "Set on submission"}
        </dd>
        {exception.decided_by && (<><dt>Decision</dt><dd style={{ margin: 0 }}>{exception.decision} by {exception.decided_by}, {when(exception.decided_at)}: {exception.decision_rationale}</dd></>)}
        {exception.declared_at && (<><dt>Declaration</dt><dd style={{ margin: 0 }}>{exception.declaration} ({when(exception.declared_at)})</dd></>)}
        {exception.revoked_at && (<><dt>Revoked</dt><dd style={{ margin: 0 }}>{exception.revoked_by}, {when(exception.revoked_at)}: {exception.revocation_reason}</dd></>)}
        {exception.last_reviewed_at && (<><dt>Last review</dt><dd style={{ margin: 0 }}>{exception.last_reviewed_by}, {when(exception.last_reviewed_at)}: {exception.last_review_note}</dd></>)}
      </dl>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 10, alignItems: "flex-start" }}>
        <ActionWithReason label="Submit for approval" allowed={a.submit.allowed} reason={a.submit.reason} needsText={false}
          onRun={() => run(() => submitSodException(exception.id), "The request couldn't be submitted.")} />
        <ActionWithReason label="Approve" allowed={a.decide.allowed} reason={a.decide.reason}
          onRun={(t) => run(() => decideSodException(exception.id, "APPROVE", t), "The approval couldn't be recorded.")} />
        <ActionWithReason label="Reject" allowed={a.decide.allowed} reason={a.decide.reason}
          onRun={(t) => run(() => decideSodException(exception.id, "REJECT", t), "The rejection couldn't be recorded.")} />
        <ActionWithReason label="Declare conflicts of interest" allowed={a.declare.allowed} reason={a.declare.reason} confirmLabel="Record declaration"
          placeholder="Your conflict-of-interest declaration"
          onRun={(t) => run(() => declareSodException(exception.id, t), "The declaration couldn't be recorded.")} />
        <ActionWithReason label="Revoke" allowed={a.revoke.allowed} reason={a.revoke.reason}
          onRun={(t) => run(() => revokeSodException(exception.id, t), "The exception couldn't be revoked.")} />
        <ActionWithReason label="Record periodic review" allowed={a.review.allowed} reason={a.review.reason} confirmLabel="Record review"
          onRun={(t) => run(() => reviewSodException(exception.id, t), "The review couldn't be recorded.")} />
      </div>
      {unavailable.length > 0 && (
        <details style={{ marginTop: 6, fontSize: 12, color: "#6b7280" }}>
          <summary>Why some actions are unavailable to you</summary>
          <ul style={{ margin: "4px 0 0" }}>
            {unavailable.map(([k, r]) => <li key={k}><strong>{k}</strong>: {r}</li>)}
          </ul>
        </details>
      )}
      {error && <p role="alert" className="field-error">{error}</p>}

      <h4 style={{ margin: "12px 0 4px", fontSize: 14 }}>History (read-only)</h4>
      <ol aria-label={`History of ${exception.reference}`} style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
        {exception.history.map((h, i) => (
          <li key={i}>{when(h.at)} — <strong>{h.action}</strong> by {h.actor}{h.to_status && h.from_status !== h.to_status ? ` (${h.from_status ?? "—"} → ${h.to_status})` : ""}{h.detail ? `: ${h.detail}` : ""}</li>
        ))}
      </ol>
    </div>
  );
}

// ---------------------------------------------------------------------------

export default function SodExceptionsPage({ user, assessments }: { user: CurrentUser; assessments: Assessment[] }) {
  const [scope, setScope] = useState<SodScope>("mine");
  const [rows, setRows] = useState<SodException[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);
  const readOnly = user.role === "AUDITOR" || user.role === "EXECUTIVE";

  // Bumped to reload the current list (e.g. after creating a request).
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    listSodExceptions(scope)
      .then((data) => {
        if (!cancelled) {
          setRows(data);
          setError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setRows([]);
          setError(friendlyError(err, "SoD exceptions couldn't be loaded."));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [scope, reloadKey]);

  function replace(updated: SodException) {
    setRows((current) => (current ?? []).map((r) => (r.id === updated.id ? updated : r)));
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <h2>SoD Exceptions</h2>
          <p>
            Time-limited exceptions to segregation-of-duties rules. A request never grants access by itself: it needs an
            independent approver of the right tier, and the affected person's conflict-of-interest declaration. Every step is
            recorded.
          </p>
        </div>
      </div>
      <PolicyBanner />

      {!readOnly && (
        <section className="content-card">
          <h3 style={{ marginTop: 0 }}>New exception request</h3>
          <RequestForm assessments={assessments} onCreated={(created) => { setScope("mine"); setReloadKey((k) => k + 1); setOpenId(created.id); }} />
        </section>
      )}

      <section className="content-card">
        <div role="tablist" aria-label="Which exceptions" style={{ display: "flex", gap: 6, marginBottom: 10 }}>
          {([["mine", "Mine"], ["awaiting_me", "Awaiting my approval"], ["all", "All (governance)"]] as const).map(([value, label]) => (
            <button key={value} role="tab" aria-selected={scope === value} className={scope === value ? "primary-button" : "doc-action-button"} onClick={() => setScope(value)}>
              {label}
            </button>
          ))}
        </div>
        {error && <p role="alert" className="field-error">{error}</p>}
        {rows === null ? (
          <p role="status">Loading…</p>
        ) : rows.length === 0 ? (
          <div className="empty-state"><h3>No exceptions here</h3></div>
        ) : (
          <table className="override-table" aria-label="SoD exceptions">
            <thead>
              <tr>
                <th scope="col">Reference</th>
                <th scope="col">Type</th>
                <th scope="col">For</th>
                <th scope="col">Assessment</th>
                <th scope="col">Risk / tier</th>
                <th scope="col">Expires</th>
                <th scope="col">Status</th>
                <th scope="col">Flags</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <Fragment key={r.id}>
                  <tr>
                    <td>
                      <button type="button" className="row-link-button" aria-expanded={openId === r.id} onClick={() => setOpenId(openId === r.id ? null : r.id)}>
                        {r.reference}
                      </button>
                    </td>
                    <td>{SOD_TYPES.find((t) => t.value === r.exception_type)?.label}</td>
                    <td>{r.affected_user}</td>
                    <td>{r.assessment_id ? `#${r.assessment_id}` : "Enterprise-wide"}</td>
                    <td>{r.risk_level}{r.tier ? ` / ${r.tier.toLowerCase()}` : ""}</td>
                    <td>{when(r.end_at)}</td>
                    <td><SodStatusBadge status={r.status} /></td>
                    <td>{r.flags.map((f) => <div key={f} style={{ fontSize: 12, color: "#9a3412" }}>⚠ {FLAG_TEXT[f]}</div>)}</td>
                  </tr>
                  {openId === r.id && (
                    <tr>
                      <td colSpan={8}><ExceptionDetail exception={r} onChanged={replace} /></td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
