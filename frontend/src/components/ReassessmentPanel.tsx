// P6 (Stage 18, R18.1-R18.4): reassessment and change management for one
// assessment, shown at every stage. Where the approval stands (in force,
// under reassessment, superseded), its triggers (flag, acknowledge,
// dismiss), proposing a change, and -- on a reassessment -- the reused
// intake fields with their source and age and the structured comparison
// with its parent. What the user may do comes from the server.
import { useCallback, useEffect, useState } from "react";
import {
  TRIGGER_TYPES,
  checkTriggers,
  flagTrigger,
  getComparison,
  getReassessmentStatus,
  listTriggers,
  proposeChange,
  resolveTrigger,
  triggerLabel,
  type ChangeKind,
  type Comparison,
  type ReassessmentStatus,
  type Trigger,
} from "../api/reassessment";
import { friendlyError } from "../utils/errorMessages";

function when(value: string | null | undefined) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

const CHANGE_STYLE: Record<ChangeKind, [string, string]> = {
  ADDED: ["#dcfce7", "#166534"],
  REMOVED: ["#fee2e2", "#991b1b"],
  CHANGED: ["#fef9c3", "#854d0e"],
  UNCHANGED: ["#f3f4f6", "#4b5563"],
};

function ChangeBadge({ change }: { change: ChangeKind }) {
  const [background, color] = CHANGE_STYLE[change];
  return <span style={{ background, color, borderRadius: 4, padding: "1px 6px", fontSize: 12 }}>{change.toLowerCase()}</span>;
}

const PROPOSE_FIELDS: { key: string; label: string }[] = [
  { key: "product_or_service_name", label: "Product or service" },
  { key: "countries_jurisdictions", label: "Countries / jurisdictions" },
  { key: "customer_segment", label: "Customer segment" },
  { key: "delivery_channels", label: "Delivery channels" },
  { key: "expected_transaction_volume", label: "Expected transaction volume" },
  { key: "third_party_vendor_usage", label: "Third-party vendors" },
  { key: "technology_process_changes", label: "Technology / process changes" },
];

function factorText(f: { applicable: boolean; likelihood: number | null; impact: number | null; score: number | null; severity: string | null } | null) {
  if (!f) return "—";
  if (!f.applicable) return "not applicable";
  if (f.score == null) return "unrated";
  return `${f.likelihood ?? "?"} × ${f.impact ?? "?"} = ${f.score} (${f.severity ?? "—"})`;
}

function ComparisonView({ comparison }: { comparison: Comparison }) {
  const { structured } = comparison;
  const cell: React.CSSProperties = { padding: "2px 6px", verticalAlign: "top" };
  return (
    <div style={{ display: "grid", gap: 10, margin: "8px 0" }} aria-label="Comparison with the parent assessment">
      <div>
        <h4 style={{ margin: "4px 0" }}>Scores</h4>
        <table className="risk-table" style={{ width: "100%" }}>
          <thead><tr><th style={cell}></th><th style={cell}>Parent #{comparison.parent_assessment_id}</th><th style={cell}>Reassessment #{comparison.reassessment_id}</th><th style={cell}>Change</th></tr></thead>
          <tbody>
            {structured.scores.map((s) => (
              <tr key={s.measure}>
                <td style={cell}>{s.measure}</td>
                <td style={cell}>{s.before ?? "—"} ({s.before_level ?? "—"})</td>
                <td style={cell}>{s.after ?? "—"} ({s.after_level ?? "—"})</td>
                <td style={cell}>{s.delta == null ? "—" : s.delta > 0 ? `+${s.delta}` : String(s.delta)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div>
        <h4 style={{ margin: "4px 0" }}>Intake fields changed</h4>
        {comparison.fields_changed.length === 0 ? <p style={{ margin: 0 }}>None.</p> : (
          <table className="risk-table" style={{ width: "100%" }}>
            <thead><tr><th style={cell}>Field</th><th style={cell}>Before</th><th style={cell}>After</th></tr></thead>
            <tbody>{comparison.fields_changed.map((f) => <tr key={f.field}><td style={cell}>{f.field.replace(/_/g, " ")}</td><td style={cell}>{f.old_value ?? "—"}</td><td style={cell}>{f.new_value ?? "—"}</td></tr>)}</tbody>
          </table>
        )}
      </div>
      <div>
        <h4 style={{ margin: "4px 0" }}>Risk factors by category</h4>
        <table className="risk-table" style={{ width: "100%" }} aria-label="Risk factor comparison">
          <thead><tr><th style={cell}>Category</th><th style={cell}>Change</th><th style={cell}>Parent (L × I = score)</th><th style={cell}>Reassessment</th></tr></thead>
          <tbody>
            {structured.factors.map((f) => (
              <tr key={f.key}>
                <td style={cell}>{f.key.replace(/_/g, " ")}</td>
                <td style={cell}><ChangeBadge change={f.change} />{f.changed_fields.length > 0 && <div style={{ fontSize: 12, color: "#6b7280" }}>{f.changed_fields.join(", ")}</div>}</td>
                <td style={cell}>{factorText(f.before)}</td>
                <td style={cell}>{factorText(f.after)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div>
        <h4 style={{ margin: "4px 0" }}>Controls</h4>
        {structured.controls.length === 0 ? <p style={{ margin: 0 }}>No controls on either assessment.</p> : (
          <table className="risk-table" style={{ width: "100%" }}>
            <thead><tr><th style={cell}>Control</th><th style={cell}>Change</th><th style={cell}>Parent (design / effectiveness)</th><th style={cell}>Reassessment</th></tr></thead>
            <tbody>
              {structured.controls.map((c) => (
                <tr key={c.key}>
                  <td style={cell}>{c.key}</td>
                  <td style={cell}><ChangeBadge change={c.change} /></td>
                  <td style={cell}>{c.before ? `${c.before.design_adequacy ?? "—"} / ${c.before.operating_effectiveness ?? "—"}` : "—"}</td>
                  <td style={cell}>{c.after ? `${c.after.design_adequacy ?? "—"} / ${c.after.operating_effectiveness ?? "—"}` : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <div>
        <h4 style={{ margin: "4px 0" }}>Committee conditions</h4>
        {structured.conditions.length === 0 ? <p style={{ margin: 0 }}>No conditions on either assessment.</p> : (
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {structured.conditions.map((c) => (
              <li key={c.key}><ChangeBadge change={c.change} /> {(c.after ?? c.before)?.description} {c.after?.status ? `(${c.after.status})` : c.before?.status ? `(${c.before.status})` : ""}</li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function TriggerRow({ trigger, canResolve, hasReassessment, onDone }: { trigger: Trigger; canResolve: boolean; hasReassessment: boolean; onDone: () => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const open = trigger.status === "OPEN" || trigger.status === "ACKNOWLEDGED";

  async function run(status: "ACKNOWLEDGED" | "DISMISSED" | "REASSESSMENT_CREATED") {
    if (status === "DISMISSED" && !text.trim()) {
      setError("Enter the reason for dismissing this trigger.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await resolveTrigger(trigger.assessment_id, trigger.id, status, text.trim() || undefined);
      setText("");
      onDone();
    } catch (err) {
      setError(friendlyError(err, "The trigger couldn't be updated."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <tr>
      <td>{triggerLabel(trigger.trigger_type)}</td>
      <td>{trigger.description}</td>
      <td>
        {trigger.status.replace(/_/g, " ").toLowerCase()}
        {trigger.reassessment_id ? ` (#${trigger.reassessment_id})` : ""}
        {(trigger.dismissed_reason || trigger.resolution_note) && (
          <div style={{ fontSize: 12, color: "#6b7280" }}>{trigger.dismissed_reason || trigger.resolution_note}</div>
        )}
      </td>
      <td>{when(trigger.detected_at)}{trigger.detected_by ? ` by ${trigger.detected_by}` : ""}</td>
      <td>
        {open && canResolve ? (
          <div style={{ display: "grid", gap: 4 }}>
            <input aria-label={`Note or reason for trigger ${trigger.id}`} placeholder="Note / reason (required to dismiss)" value={text} onChange={(e) => setText(e.target.value)} />
            <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
              {trigger.status === "OPEN" && <button type="button" className="doc-action-button" disabled={busy} onClick={() => run("ACKNOWLEDGED")}>Acknowledge</button>}
              <button type="button" className="doc-action-button" disabled={busy} onClick={() => run("DISMISSED")}>Dismiss</button>
              {hasReassessment && <button type="button" className="doc-action-button" disabled={busy} onClick={() => run("REASSESSMENT_CREATED")}>Link to reassessment</button>}
            </div>
            {error && <span role="alert" className="field-error">{error}</span>}
          </div>
        ) : open ? (
          <span style={{ fontSize: 12, color: "#6b7280" }}>Settled by an FCRM Analyst, Manager or Admin</span>
        ) : (
          <span style={{ fontSize: 12, color: "#6b7280" }}>{trigger.resolved_by ? `${trigger.resolved_by}, ${when(trigger.resolved_at)}` : "—"}</span>
        )}
      </td>
    </tr>
  );
}

export default function ReassessmentPanel({ assessmentId, onOpenAssessment }: { assessmentId: number; onOpenAssessment?: (id: number) => void }) {
  const [status, setStatus] = useState<ReassessmentStatus | null>(null);
  const [triggers, setTriggers] = useState<Trigger[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [showComparison, setShowComparison] = useState(false);
  const [flagType, setFlagType] = useState("REGULATORY_POLICY_CHANGE");
  const [flagText, setFlagText] = useState("");
  const [proposing, setProposing] = useState(false);
  const [changes, setChanges] = useState<Record<string, string>>({});
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [s, t] = await Promise.all([getReassessmentStatus(assessmentId), listTriggers(assessmentId)]);
      setStatus(s);
      setTriggers(t);
      setError(null);
      if (s.parent_assessment_id) setComparison(await getComparison(assessmentId));
    } catch (err) {
      setError(friendlyError(err, "Reassessment details couldn't be loaded."));
    }
  }, [assessmentId]);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getReassessmentStatus(assessmentId), listTriggers(assessmentId)])
      .then(async ([s, t]) => {
        if (cancelled) return;
        setStatus(s);
        setTriggers(t);
        if (s.parent_assessment_id) {
          const c = await getComparison(assessmentId);
          if (!cancelled) setComparison(c);
        }
      })
      .catch((err) => !cancelled && setError(friendlyError(err, "Reassessment details couldn't be loaded.")));
    return () => {
      cancelled = true;
    };
  }, [assessmentId]);

  async function act(fn: () => Promise<unknown>, done: string, fallback: string) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      setNotice(done);
      await load();
    } catch (err) {
      setError(friendlyError(err, fallback));
    } finally {
      setBusy(false);
    }
  }

  async function submitProposal() {
    const filled = Object.fromEntries(Object.entries(changes).filter(([, v]) => v.trim()));
    if (!reason.trim()) {
      setError("Enter the reason for the proposed change.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await proposeChange(assessmentId, filled, reason.trim());
      setNotice(`Reassessment opened as Assessment #${result.reassessment_id}. This approval stays in force until it is decided.`);
      setProposing(false);
      setChanges({});
      setReason("");
      await load();
    } catch (err) {
      setError(friendlyError(err, "The change couldn't be proposed."));
    } finally {
      setBusy(false);
    }
  }

  if (!status) {
    return (
      <section className="workflow-card" style={{ marginTop: 16 }} aria-label="Reassessment">
        <div className="workflow-card-header">
          <div>
            <h2>Reassessment &amp; Change Management</h2>
            <p>Review dates, change triggers and reassessments for this assessment.</p>
          </div>
          <span className="source-badge">REASSESSMENT</span>
        </div>
        <div className="workflow-card-body">
          {error ? <p role="alert" className="field-error">{error}</p> : <p>Loading…</p>}
        </div>
      </section>
    );
  }

  const link = (id: number, text: string) =>
    onOpenAssessment ? <button type="button" className="doc-action-button" onClick={() => onOpenAssessment(id)}>{text}</button> : <strong>{text}</strong>;
  const hasReassessment = status.reassessments.length > 0;

  return (
    <section className="workflow-card" style={{ marginTop: 16 }} aria-label="Reassessment">
      <div className="workflow-card-header">
        <div>
          <h2>Reassessment &amp; Change Management</h2>
          <p>Review dates, change triggers and reassessments for this assessment.</p>
        </div>
        <span className="source-badge">REASSESSMENT</span>
      </div>
      <div className="workflow-card-body">

      <div data-testid="reassessment-state" style={{ marginBottom: 8 }}>
        {status.reassessment_state === "SUPERSEDED" && status.superseded_by_id ? (
          <p role="note" style={{ margin: 0, padding: "6px 10px", background: "#e5e7eb", borderRadius: 6 }}>
            <strong>Superseded</strong> by reassessment {link(status.superseded_by_id, `#${status.superseded_by_id}`)} on {when(status.superseded_at)}.
            This approval is kept on record but is no longer in force; review dates follow the reassessment.
          </p>
        ) : status.reassessment_state === "UNDER_REASSESSMENT" ? (
          <p role="note" style={{ margin: 0, padding: "6px 10px", background: "#fef9c3", borderRadius: 6 }}>
            <strong>Under reassessment</strong>
            {status.in_progress_reassessment_id ? <> by {link(status.in_progress_reassessment_id, `#${status.in_progress_reassessment_id}`)}</> : null}.
            This approval stays in force until the reassessment is decided.
          </p>
        ) : status.status === "APPROVED" || status.status === "APPROVED_WITH_CONDITIONS" ? (
          <p style={{ margin: 0 }}>Approval in force{status.next_review_date ? `; next review due ${status.next_review_date}` : ""}.</p>
        ) : null}
        {status.parent_assessment_id && (
          <p style={{ margin: "6px 0 0" }}>This is a reassessment of {link(status.parent_assessment_id, `Assessment #${status.parent_assessment_id}`)}.</p>
        )}
      </div>

      {notice && <p className="risk-reason" style={{ color: "#15803d" }}>{notice}</p>}
      {error && <p role="alert" className="field-error">{error}</p>}

      {comparison && comparison.reused_fields.length > 0 && (
        <div style={{ marginBottom: 8 }}>
          <h4 style={{ margin: "4px 0" }}>Information carried over from the parent (R18.3)</h4>
          <table className="risk-table" style={{ width: "100%" }} aria-label="Reused fields">
            <thead><tr><th>Field</th><th>Value</th><th>Source</th><th>Age</th></tr></thead>
            <tbody>
              {comparison.reused_fields.map((r) => (
                <tr key={r.field}>
                  <td>{r.label}</td>
                  <td>{r.value ?? "—"}</td>
                  <td>Assessment #{r.source_assessment_id ?? "?"}, {when(r.source_date)}</td>
                  <td>{r.age_days == null ? "—" : `${r.age_days} day(s)`}{r.age_days != null && r.age_days > 365 ? " (over a year old: confirm it is still accurate)" : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {comparison && (
        <div style={{ marginBottom: 8 }}>
          <button type="button" className="doc-action-button" onClick={() => setShowComparison((v) => !v)}>
            {showComparison ? "Hide comparison" : "Compare with parent"}
          </button>
          <span style={{ marginLeft: 8, fontSize: 12, color: "#6b7280" }}>
            Factors: {comparison.structured.summary.factors.CHANGED} changed, {comparison.structured.summary.factors.ADDED} added, {comparison.structured.summary.factors.REMOVED} removed
          </span>
          {showComparison && <ComparisonView comparison={comparison} />}
        </div>
      )}

      <h4 style={{ margin: "8px 0 4px" }}>Triggers</h4>
      <div style={{ display: "flex", gap: 8, marginBottom: 6 }}>
        <button type="button" className="doc-action-button" disabled={busy} onClick={() => act(() => checkTriggers(assessmentId), "Review dates checked.", "Review dates couldn't be checked.")}>
          Check review dates
        </button>
      </div>
      {triggers.length === 0 ? <p style={{ margin: 0, fontSize: 13 }}>No triggers recorded.</p> : (
        <table className="risk-table" style={{ width: "100%", marginBottom: 8 }} aria-label="Reassessment triggers">
          <thead><tr><th>Type</th><th>Description</th><th>Status</th><th>Detected</th><th>Action</th></tr></thead>
          <tbody>
            {triggers.map((t) => (
              <TriggerRow key={t.id} trigger={t} canResolve={status.actions.resolve.allowed} hasReassessment={hasReassessment} onDone={load} />
            ))}
          </tbody>
        </table>
      )}

      {status.actions.flag.allowed ? (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center", marginBottom: 8 }}>
          <select aria-label="Trigger type" value={flagType} onChange={(e) => setFlagType(e.target.value)}>
            {TRIGGER_TYPES.filter((t) => t.value !== "EXPIRY" && t.value !== "PERIODIC_REVIEW").map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
          </select>
          <input aria-label="Trigger description" placeholder="What changed? (required)" style={{ flex: 1, minWidth: 220 }} value={flagText} onChange={(e) => setFlagText(e.target.value)} />
          <button type="button" className="doc-action-button" disabled={busy || !flagText.trim()}
            onClick={() => act(async () => { await flagTrigger(assessmentId, flagType, flagText.trim()); setFlagText(""); }, "Trigger flagged.", "The trigger couldn't be flagged.")}>
            Flag trigger
          </button>
        </div>
      ) : status.actions.flag.reason ? (
        <p style={{ fontSize: 12, color: "#6b7280" }}>Flagging a trigger: {status.actions.flag.reason}</p>
      ) : null}

      {status.actions.propose.allowed ? (
        proposing ? (
          <div className="form-group" aria-label="Propose a change">
            <label>Proposed changes (leave blank to keep as-is; unchanged fields are carried over and shown with their source)</label>
            {PROPOSE_FIELDS.map((f) => (
              <input key={f.key} aria-label={`New ${f.label.toLowerCase()}`} placeholder={`New ${f.label.toLowerCase()}`} style={{ marginBottom: 6, width: "100%" }}
                value={changes[f.key] ?? ""} onChange={(e) => setChanges((c) => ({ ...c, [f.key]: e.target.value }))} />
            ))}
            <label>Reason for the proposed change (required)</label>
            <textarea aria-label="Reason for the proposed change (required)" rows={2} value={reason} onChange={(e) => setReason(e.target.value)} />
            <div className="form-actions">
              <button type="button" className="secondary-button" onClick={() => setProposing(false)} disabled={busy}>Cancel</button>
              <button type="button" className="primary-button" onClick={submitProposal} disabled={busy}>{busy ? "Submitting..." : "Propose change and open reassessment"}</button>
            </div>
          </div>
        ) : (
          <button type="button" className="doc-action-button" onClick={() => setProposing(true)}>Propose a change</button>
        )
      ) : status.actions.propose.reason && (status.status === "APPROVED" || status.status === "APPROVED_WITH_CONDITIONS" || status.status === "CLOSED") ? (
        <p style={{ fontSize: 12, color: "#6b7280" }}>Proposing a change: {status.actions.propose.reason}</p>
      ) : null}
      </div>
    </section>
  );
}
