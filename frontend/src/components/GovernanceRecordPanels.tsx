// P2 governance records in the UI:
//  * ValueComparisonTable  -- calculated values and the human values beside them
//  * ChallengeSignoffPanel -- the mandatory challenge-review sign-off (R11)
//  * VoteHistoryList       -- append-only committee votes (R12.6)
//  * OverrideLedgerPanel   -- propose / independently review overrides (R10.2-R10.4)
//  * ControlChangePanel    -- versioned edit / remap / unmap of a control (R7.2)
//  * CommitteeReadinessPanel -- P3: the authoritative readiness result
// The backend enforces every rule and says what the user may do (and why
// not); the UI only displays that. P3 rules are provisional -- pending
// governance approval.
import { useEffect, useState } from "react";
import {
  getRiskFactors,
  OVERRIDE_SECTIONS,
  type Control,
  type RiskFactor,
} from "../api/assessments";
import {
  POLICY_PENDING_LABEL,
  approveOverride,
  changeControl,
  completeChallengeReview,
  getChallengeSignoff,
  getReadiness,
  getControlRevisions,
  getLedger,
  proposeOverride,
  reviewOverride,
  signOffChallengeReview,
  unmapControl,
  type Allowed,
  type ChallengeSignoffState,
  type ControlRevision,
  type Readiness,
  type LedgerEntry,
  type ReviewStatus,
  type ValueComparison,
  type VoteRecord,
} from "../api/governanceRecords";
import { friendlyError } from "../utils/errorMessages";

const STATUS_TEXT: Record<string, string> = {
  APPLIED: "Applied",
  PROPOSED: "Proposed — awaiting review",
  CONFIRMED: "Confirmed by reviewer",
  REJECTED: "Rejected by reviewer",
  LEGACY_UNREVIEWED: "Recorded before review existed",
};

function statusStyle(status: string | null): React.CSSProperties {
  const palette: Record<string, [string, string]> = {
    APPLIED: ["#e0f2fe", "#075985"],
    CONFIRMED: ["#dcfce7", "#166534"],
    PROPOSED: ["#fef9c3", "#854d0e"],
    REJECTED: ["#fee2e2", "#991b1b"],
  };
  const [background, color] = palette[status ?? ""] ?? ["#f3f4f6", "#374151"];
  return { background, color, borderRadius: 4, padding: "1px 6px", fontSize: 12, whiteSpace: "nowrap" };
}

export function ReviewStatusBadge({ status }: { status: ReviewStatus | "LEGACY_UNREVIEWED" | null }) {
  const key = status ?? "LEGACY_UNREVIEWED";
  return <span style={statusStyle(key)}>{STATUS_TEXT[key] ?? key}</span>;
}

function when(value: string | null | undefined): string {
  return value ? new Date(value).toLocaleString() : "—";
}

// ---------------------------------------------------------------------------

/** R10.4 / R6.7: calculated value, human value, difference, reason, who, status. */
export function ValueComparisonTable({ comparisons }: { comparisons: ValueComparison[] }) {
  if (comparisons.length === 0) {
    return <p className="workflow-empty">No human value differs from a calculated one.</p>;
  }
  return (
    <table className="override-table" aria-label="Calculated values and human overrides">
      <thead>
        <tr>
          <th scope="col">Area</th>
          <th scope="col">Item</th>
          <th scope="col">Calculated (system)</th>
          <th scope="col">Human value</th>
          <th scope="col">Difference</th>
          <th scope="col">Reason</th>
          <th scope="col">By / when</th>
          <th scope="col">Status</th>
          <th scope="col">Used in decision</th>
        </tr>
      </thead>
      <tbody>
        {comparisons.map((entry, index) => (
          <tr key={`${entry.section}-${entry.item}-${index}`}>
            <td>{entry.section.replace(/_/g, " ").toLowerCase()}</td>
            <td>{entry.item}</td>
            <td>{entry.calculated_value ?? "—"}</td>
            <td>
              <strong>{entry.human_value ?? "—"}</strong>
            </td>
            <td>{entry.difference ?? "—"}</td>
            <td>{entry.reason ?? "—"}</td>
            <td>
              {entry.by ?? "—"}
              <br />
              {when(entry.at)}
              {entry.reviewed_by && (
                <>
                  <br />
                  Reviewed by {entry.reviewed_by}
                </>
              )}
            </td>
            <td>
              <ReviewStatusBadge status={entry.review_status} />
            </td>
            <td>{entry.counts_in_decision ? "Yes" : "No"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ---------------------------------------------------------------------------

/** Shows why an action is unavailable, as computed by the backend. */
function Unavailable({ action }: { action?: Allowed }) {
  if (!action || action.allowed || !action.reason) return null;
  return <span style={{ fontSize: 12, color: "#6b7280" }}>{action.reason}</span>;
}

function StepRow({ title, row, empty }: { title: string; row: ChallengeSignoffState["review"]; empty: string }) {
  return (
    <p role="status" style={{ margin: "0 0 6px" }}>
      <strong>{title}: </strong>
      {row ? (
        <>
          <span aria-hidden="true">✓ </span>
          {row.reviewer} ({row.reviewer_role.replace(/_/g, " ").toLowerCase()}), {when(row.completed_at)} —{" "}
          {row.outcome === "NO_TRIGGERS_FIRED" ? "no challenge trigger fired" : "findings addressed"}
          {row.committee_escalation ? " — critical case escalated to the Committee" : ""}. <em>{row.reason}</em>
        </>
      ) : (
        empty
      )}
    </p>
  );
}

function ReasonedButton({
  label,
  action,
  placeholder,
  onRun,
}: {
  label: string;
  action?: Allowed;
  placeholder: string;
  onRun: (text: string) => Promise<void>;
}) {
  const [text, setText] = useState("");
  const [missing, setMissing] = useState(false);
  const [busy, setBusy] = useState(false);
  if (!action?.allowed) return null;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 8 }}>
      <textarea
        aria-label={`${label}: summary (required)`}
        aria-required="true"
        aria-invalid={missing || undefined}
        placeholder={placeholder}
        rows={2}
        value={text}
        onChange={(event) => {
          setText(event.target.value);
          if (event.target.value.trim()) setMissing(false);
        }}
      />
      {missing && (
        <span className="field-error">
          <span aria-hidden="true">⚠</span> A summary is required.
        </span>
      )}
      <button
        type="button"
        className="doc-action-button"
        disabled={busy}
        style={{ alignSelf: "flex-start" }}
        onClick={async () => {
          if (!text.trim()) {
            setMissing(true);
            return;
          }
          setBusy(true);
          try {
            await onRun(text.trim());
            setText("");
          } finally {
            setBusy(false);
          }
        }}
      >
        {busy ? "Saving…" : label}
      </button>
    </div>
  );
}

/** R11 / P3: the independent challenge review, then the sign-off. */
export function ChallengeSignoffPanel({ assessmentId, onChanged }: { assessmentId: number; onChanged?: () => void }) {
  const [state, setState] = useState<ChallengeSignoffState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getChallengeSignoff(assessmentId)
      .then((data) => {
        if (!cancelled) setState(data);
      })
      .catch((err) => {
        if (!cancelled) setError(friendlyError(err, "The challenge review couldn't be loaded."));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [assessmentId]);

  async function run(fn: () => Promise<unknown>, failure: string) {
    setError(null);
    try {
      await fn();
      setState(await getChallengeSignoff(assessmentId));
      onChanged?.();
    } catch (err) {
      setError(friendlyError(err, failure));
    }
  }

  return (
    <section aria-labelledby={`signoff-${assessmentId}`} style={{ border: "1px solid #e5e7eb", borderRadius: 6, padding: 12 }}>
      <h4 id={`signoff-${assessmentId}`} style={{ margin: "0 0 6px" }}>
        Challenge review (mandatory)
      </h4>
      <p style={{ margin: "0 0 8px", fontSize: 13 }}>
        Every assessment needs an independent challenge review (Challenge Reviewer, Senior Analyst or QA Reviewer who did not
        prepare it), then a sign-off by an FCRM Manager or the Head of FCRM — even when no challenge trigger fired.{" "}
        <em>{POLICY_PENDING_LABEL}.</em>
      </p>
      {loading ? (
        <p role="status">Loading challenge review…</p>
      ) : (
        <>
          <StepRow title="1. Independent review" row={state?.review ?? null} empty="not completed" />
          <StepRow title="2. Sign-off" row={state?.current ?? null} empty="not signed off" />
          {state?.valid ? (
            <p role="status" style={{ margin: "0 0 8px", color: "#166534" }}>
              <span aria-hidden="true">✓ </span>Complete.
            </p>
          ) : (
            <p role="alert" style={{ margin: "0 0 8px", color: "#991b1b" }}>
              <span aria-hidden="true">⚠ </span>
              {state?.problem ?? "Not complete."}
            </p>
          )}
          <ReasonedButton
            label="Complete independent review"
            action={state?.actions?.review}
            placeholder="What you challenged, and how any findings (or the absence of triggers) were dealt with"
            onRun={(text) => run(() => completeChallengeReview(assessmentId, text), "The review couldn't be recorded.")}
          />
          <ReasonedButton
            label="Sign off challenge review"
            action={state?.actions?.signoff}
            placeholder="Sign-off rationale"
            onRun={(text) => run(() => signOffChallengeReview(assessmentId, text), "The sign-off couldn't be recorded.")}
          />
          {!state?.actions?.review.allowed && !state?.actions?.signoff.allowed && (
            <p style={{ margin: 0, fontSize: 12, color: "#6b7280" }}>
              You can't act here: <Unavailable action={state?.actions?.review} />{" "}
              {state?.actions?.signoff.reason !== state?.actions?.review.reason && <Unavailable action={state?.actions?.signoff} />}
            </p>
          )}
        </>
      )}
      {error && (
        <p role="alert" className="field-error">
          {error}
        </p>
      )}
      {state && state.history.length > 0 && (
        <>
          <button type="button" className="row-link-button" onClick={() => setShowHistory((v) => !v)}>
            {showHistory ? "Hide history" : `Challenge review history (${state.history.length})`}
          </button>
          {showHistory && (
            <ul style={{ margin: "6px 0 0", fontSize: 13 }}>
              {state.history.map((row) => (
                <li key={row.id}>
                  {row.stage.toLowerCase()} v{row.version} — {row.reviewer}, {when(row.completed_at)}
                  {row.is_current ? " (current)" : ` — superseded ${when(row.superseded_at)}: ${row.superseded_reason ?? ""}`}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------

/** R12.6: every vote, superseded ones visibly kept. */
export function VoteHistoryList({ votes }: { votes: VoteRecord[] }) {
  if (votes.length === 0) {
    return <p className="workflow-empty">No votes recorded yet.</p>;
  }
  return (
    <ul style={{ margin: "0 0 8px", paddingLeft: 18 }} aria-label="Committee votes, including superseded ones">
      {votes.map((v) => (
        <li key={v.id} style={v.is_current ? undefined : { color: "#6b7280" }}>
          {v.member_name}: <strong>{v.vote}</strong> (v{v.version}
          {v.is_current ? ", current" : ", superseded"})
          {v.delegate_name ? ` — cast by delegate ${v.delegate_name}` : ""}
          {v.comment ? ` — ${v.comment}` : ""}
          {v.recast_reason ? ` — changed because: ${v.recast_reason}` : ""}
          <span style={{ fontSize: 12 }}> · {when(v.voted_at)}</span>
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------

const SECTION_FIELDS: Record<string, string[]> = {
  RISK_CATEGORY: ["applicable", "category", "indicators"],
  FACTOR_RATING: ["likelihood", "impact", "score", "severity"],
  RISK_RATIONALE: ["rationale", "misuse_scenario"],
  CONTROL_MAPPING: ["risk_factor_id", "control_type", "owner", "operating_status", "scope", "frequency"],
  CONTROL_EFFECTIVENESS: ["design_adequacy", "operating_effectiveness", "has_evidence", "coverage_complete"],
  RESIDUAL_RISK: ["band", "score"],
  CONDITION: ["recommended_text", "status", "condition_type"],
};
const FACTOR_SECTIONS = ["RISK_CATEGORY", "FACTOR_RATING", "RISK_RATIONALE"];
const CONTROL_SECTIONS = ["CONTROL_MAPPING", "CONTROL_EFFECTIVENESS"];

const STATE_TEXT: Record<string, [string, string]> = {
  PENDING_REVIEW: ["Pending independent review", "PROPOSED"],
  PENDING_APPROVAL: ["Pending approval", "PROPOSED"],
  APPROVED: ["Approved", "CONFIRMED"],
  REJECTED_STILL_IN_EFFECT: ["Rejected — still applied, must be corrected", "REJECTED"],
  REJECTED_RESOLVED: ["Rejected — resolved", "REJECTED"],
  PROPOSED_NONMATERIAL: ["Proposed (non-material)", "PROPOSED"],
  NONMATERIAL_DONE: ["Done (non-material)", "APPLIED"],
  LEGACY: ["Recorded before independent review", "LEGACY_UNREVIEWED"],
};

function GovernanceState({ state }: { state?: string | null }) {
  const [text, tone] = STATE_TEXT[state ?? "LEGACY"] ?? [state ?? "—", "LEGACY_UNREVIEWED"];
  return <span style={statusStyle(tone)}>{text}</span>;
}

/** R10.2-R10.4 / P3: the override ledger -- proposal, independent review,
 * material approval. */
export function OverrideLedgerPanel({
  assessmentId,
  controls,
  canPropose,
}: {
  assessmentId: number;
  controls: Control[];
  canPropose: boolean;
}) {
  const [entries, setEntries] = useState<LedgerEntry[]>([]);
  const [factors, setFactors] = useState<RiskFactor[]>([]);
  const [section, setSection] = useState<string>(OVERRIDE_SECTIONS[0].value);
  const [entityId, setEntityId] = useState("");
  const [fieldName, setFieldName] = useState("");
  const [humanValue, setHumanValue] = useState("");
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reviewNotes, setReviewNotes] = useState<Record<number, string>>({});
  const [approvalNotes, setApprovalNotes] = useState<Record<number, string>>({});

  useEffect(() => {
    let cancelled = false;
    getLedger(assessmentId)
      .then((data) => !cancelled && setEntries(data))
      .catch((err) => !cancelled && setError(friendlyError(err, "The override ledger couldn't be loaded.")));
    getRiskFactors(assessmentId)
      .then((data) => !cancelled && setFactors(data.filter((f) => f.is_current)))
      .catch(() => !cancelled && setFactors([]));
    return () => {
      cancelled = true;
    };
  }, [assessmentId]);

  const fields = SECTION_FIELDS[section];
  const needsFactor = FACTOR_SECTIONS.includes(section);
  const needsControl = CONTROL_SECTIONS.includes(section);
  const needsId = section === "CONDITION";
  const mayReview = (entry: LedgerEntry) => Boolean(entry.actions?.review?.allowed);
  const mayApprove = (entry: LedgerEntry) => Boolean(entry.actions?.approve?.allowed);

  async function propose() {
    const field = fields ? fieldName || fields[0] : fieldName.trim();
    if (!field || !humanValue.trim() || !reason.trim() || ((needsFactor || needsControl || needsId) && !entityId)) {
      setError("Choose what to change, then give the proposed value and a reason.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const created = await proposeOverride(assessmentId, {
        section,
        field_name: field,
        entity_id: entityId || null,
        human_value: humanValue.trim(),
        reason: reason.trim(),
      });
      setEntries((current) => [...current, created]);
      setHumanValue("");
      setReason("");
    } catch (err) {
      setError(friendlyError(err, "The override couldn't be recorded."));
    } finally {
      setSaving(false);
    }
  }

  async function approve(entry: LedgerEntry, decision: "APPROVE" | "REJECT") {
    const rationale = (approvalNotes[entry.id] ?? "").trim();
    if (!rationale) {
      setError("An approval rationale is required.");
      return;
    }
    setError(null);
    try {
      const updated = await approveOverride(assessmentId, entry.id, decision, rationale);
      setEntries((current) => current.map((e) => (e.id === updated.id ? updated : e)));
    } catch (err) {
      setError(friendlyError(err, "The approval couldn't be recorded."));
    }
  }

  async function review(entry: LedgerEntry, decision: "CONFIRM" | "REJECT") {
    const note = (reviewNotes[entry.id] ?? "").trim();
    if (!note) {
      setError("A review note is required to confirm or reject an override.");
      return;
    }
    setError(null);
    try {
      const updated = await reviewOverride(assessmentId, entry.id, decision, note);
      setEntries((current) => current.map((e) => (e.id === updated.id ? updated : e)));
    } catch (err) {
      setError(friendlyError(err, "The review couldn't be recorded."));
    }
  }

  return (
    <div>
      <p style={{ fontSize: 13, margin: "0 0 8px" }}>
        The <strong>system value</strong> is read from the record when an override is recorded — it can't be typed in, and
        the calculation itself never changes. Materiality is set by the system from the actual effect. A material change
        needs an independent review and then approval (critical: Head of FCRM) before the committee.{" "}
        <em>{POLICY_PENDING_LABEL}.</em>
      </p>
      {entries.length === 0 ? (
        <div className="workflow-empty">No overrides recorded yet.</div>
      ) : (
        <table className="override-table" aria-label="Override ledger">
          <thead>
            <tr>
              <th scope="col">Section</th>
              <th scope="col">Field</th>
              <th scope="col">System value</th>
              <th scope="col">Human value</th>
              <th scope="col">Reason</th>
              <th scope="col">By / when</th>
              <th scope="col">Materiality</th>
              <th scope="col">Status</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((entry) => (
              <tr key={entry.id}>
                <td>{OVERRIDE_SECTIONS.find((s) => s.value === entry.section)?.label ?? entry.section.replace(/_/g, " ")}</td>
                <td>
                  {entry.field_name}
                  {entry.entity_id ? ` (#${entry.entity_id})` : ""}
                </td>
                <td>
                  {entry.ai_value ?? "—"}
                  {entry.ai_value_source !== "SYSTEM" && (
                    <div style={{ fontSize: 11, color: "#6b7280" }}>entered by hand (legacy)</div>
                  )}
                </td>
                <td>
                  <strong>{entry.human_value}</strong>
                </td>
                <td>{entry.reason}</td>
                <td>
                  {entry.overridden_by ?? "—"}
                  <br />
                  {when(entry.created_at)}
                </td>
                <td>
                  {entry.materiality ?? "—"}
                  {entry.materiality_reasons && entry.materiality_reasons.length > 0 && (
                    <div style={{ fontSize: 11, color: "#6b7280" }}>{entry.materiality_reasons.join("; ")}</div>
                  )}
                </td>
                <td>
                  <GovernanceState state={entry.state} />
                  {entry.reviewed_by && (
                    <div style={{ fontSize: 12 }}>
                      {entry.reviewed_by}: {entry.review_note}
                    </div>
                  )}
                  {mayReview(entry) && (
                    <div style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 4 }}>
                      <input
                        aria-label={`Review note for override ${entry.id}`}
                        placeholder="Review note (required)"
                        value={reviewNotes[entry.id] ?? ""}
                        onChange={(event) => setReviewNotes((n) => ({ ...n, [entry.id]: event.target.value }))}
                      />
                      <div style={{ display: "flex", gap: 4 }}>
                        <button type="button" onClick={() => review(entry, "CONFIRM")}>
                          Confirm
                        </button>
                        <button type="button" onClick={() => review(entry, "REJECT")}>
                          Reject
                        </button>
                      </div>
                    </div>
                  )}
                  {entry.approved_by && (
                    <div style={{ fontSize: 12 }}>
                      Approval: {entry.approved_by}: {entry.approval_rationale}
                    </div>
                  )}
                  {mayApprove(entry) && (
                    <div style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 4 }}>
                      <input
                        aria-label={`Approval rationale for override ${entry.id}`}
                        placeholder="Approval rationale (required)"
                        value={approvalNotes[entry.id] ?? ""}
                        onChange={(event) => setApprovalNotes((n) => ({ ...n, [entry.id]: event.target.value }))}
                      />
                      <div style={{ display: "flex", gap: 4 }}>
                        <button type="button" onClick={() => approve(entry, "APPROVE")}>
                          Approve
                        </button>
                        <button type="button" onClick={() => approve(entry, "REJECT")}>
                          Reject approval
                        </button>
                      </div>
                    </div>
                  )}
                  {!mayReview(entry) && !mayApprove(entry) && (entry.state === "PENDING_REVIEW" || entry.state === "PENDING_APPROVAL") && (
                    <div style={{ fontSize: 11, color: "#6b7280" }}>
                      {(entry.state === "PENDING_REVIEW" ? entry.actions?.review?.reason : entry.actions?.approve?.reason) ?? ""}
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {canPropose && (
        <div className="override-form">
          <div className="override-form-row">
            <select
              aria-label="Override section"
              value={section}
              onChange={(event) => {
                setSection(event.target.value);
                setEntityId("");
                setFieldName("");
              }}
            >
              {OVERRIDE_SECTIONS.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
            {needsFactor && (
              <select aria-label="Risk factor" value={entityId} onChange={(event) => setEntityId(event.target.value)}>
                <option value="">Choose a risk factor…</option>
                {factors.map((f) => (
                  <option key={f.id} value={String(f.id)}>
                    {f.category.replace(/_/g, " ")}
                  </option>
                ))}
              </select>
            )}
            {needsControl && (
              <select aria-label="Control" value={entityId} onChange={(event) => setEntityId(event.target.value)}>
                <option value="">Choose a control…</option>
                {controls.map((c) => (
                  <option key={c.id} value={String(c.id)}>
                    {c.control_type.replace(/_/g, " ")} (#{c.id})
                  </option>
                ))}
              </select>
            )}
            {needsId && (
              <input
                aria-label="Recommended condition id"
                placeholder="Condition id"
                value={entityId}
                onChange={(event) => setEntityId(event.target.value.replace(/\D/g, ""))}
              />
            )}
            {fields ? (
              <select aria-label="Field" value={fieldName || fields[0]} onChange={(event) => setFieldName(event.target.value)}>
                {fields.map((f) => (
                  <option key={f} value={f}>
                    {f.replace(/_/g, " ")}
                  </option>
                ))}
              </select>
            ) : (
              <input
                aria-label="Field name (e.g. customer_segment)"
                placeholder="Field name (e.g. customer_segment)"
                value={fieldName}
                onChange={(event) => setFieldName(event.target.value)}
              />
            )}
          </div>
          <div className="override-form-row">
            <input
              aria-label="Proposed human value"
              placeholder="Proposed human value"
              value={humanValue}
              onChange={(event) => setHumanValue(event.target.value)}
            />
          </div>
          <textarea
            aria-label="Reason for this change (required)"
            placeholder="Reason for this change (required)"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            rows={2}
          />
          <button type="button" className="save-review-button" disabled={saving} onClick={propose}>
            {saving ? "Saving…" : "Propose Override"}
          </button>
        </div>
      )}
      {error && (
        <p className="save-review-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------

const EDITABLE_CONTROL_FIELDS: { key: keyof Control; label: string }[] = [
  { key: "owner", label: "Control owner" },
  { key: "performing_department", label: "Performing department" },
  { key: "frequency", label: "Frequency" },
  { key: "trigger", label: "Trigger" },
  { key: "scope", label: "Scope" },
  { key: "evidence_source", label: "Evidence source" },
];
const OPERATING_STATUSES = ["ACTIVE", "PARTIALLY_IMPLEMENTED", "PLANNED", "INACTIVE"];

/** R7.2 / R10.2: versioned edit, remap and unmap of one control. */
export function ControlChangePanel({
  assessmentId,
  control,
  factors,
  canChange,
  onChanged,
}: {
  assessmentId: number;
  control: Control;
  factors: { id: number; category: string }[];
  canChange: boolean;
  onChanged: () => void;
}) {
  const [mode, setMode] = useState<"none" | "edit" | "unmap" | "history">("none");
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [riskFactorId, setRiskFactorId] = useState(String(control.risk_factor_id));
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [revisions, setRevisions] = useState<ControlRevision[] | null>(null);

  function open(next: typeof mode) {
    setMode(mode === next ? "none" : next);
    setError(null);
    setReason("");
    if (next === "edit") {
      setDraft(
        Object.fromEntries(
          [...EDITABLE_CONTROL_FIELDS.map((f) => f.key), "operating_status"].map((key) => [
            key,
            String(control[key as keyof Control] ?? ""),
          ])
        )
      );
      setRiskFactorId(String(control.risk_factor_id));
    }
    if (next === "history") {
      getControlRevisions(assessmentId, control.id)
        .then(setRevisions)
        .catch((err) => setError(friendlyError(err, "The control's history couldn't be loaded.")));
    }
  }

  async function save() {
    if (!reason.trim()) {
      setError("A reason is required for every control change.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      if (mode === "unmap") {
        await unmapControl(assessmentId, control.id, reason.trim());
      } else {
        const changes: Record<string, unknown> = {};
        for (const [key, value] of Object.entries(draft)) {
          if (value !== String(control[key as keyof Control] ?? "")) changes[key] = value;
        }
        if (Number(riskFactorId) !== control.risk_factor_id) changes.risk_factor_id = Number(riskFactorId);
        if (Object.keys(changes).length === 0) {
          setError("Nothing has changed.");
          setSaving(false);
          return;
        }
        await changeControl(assessmentId, control.id, { ...changes, reason: reason.trim() });
      }
      setMode("none");
      onChanged();
    } catch (err) {
      setError(friendlyError(err, "The change couldn't be saved."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div style={{ marginTop: 6 }}>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {canChange && (
          <>
            <button type="button" className="doc-action-button" onClick={() => open("edit")} aria-expanded={mode === "edit"}>
              Edit / remap
            </button>
            <button type="button" className="doc-action-button" onClick={() => open("unmap")} aria-expanded={mode === "unmap"}>
              Unmap
            </button>
          </>
        )}
        <button type="button" className="doc-action-button" onClick={() => open("history")} aria-expanded={mode === "history"}>
          History (v{control.version})
        </button>
      </div>

      {mode === "edit" && (
        <div className="override-form" style={{ marginTop: 6 }}>
          <label>
            Mapped to risk
            <select aria-label="Mapped risk factor" value={riskFactorId} onChange={(event) => setRiskFactorId(event.target.value)}>
              {factors.map((f) => (
                <option key={f.id} value={String(f.id)}>
                  {f.category.replace(/_/g, " ")}
                </option>
              ))}
            </select>
          </label>
          {EDITABLE_CONTROL_FIELDS.map((field) => (
            <label key={field.key}>
              {field.label}
              <input
                value={draft[field.key] ?? ""}
                onChange={(event) => setDraft((d) => ({ ...d, [field.key]: event.target.value }))}
              />
            </label>
          ))}
          <label>
            Operating status
            <select
              value={draft.operating_status ?? control.operating_status}
              onChange={(event) => setDraft((d) => ({ ...d, operating_status: event.target.value }))}
            >
              {OPERATING_STATUSES.map((s) => (
                <option key={s} value={s}>
                  {s.replace(/_/g, " ").toLowerCase()}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}

      {(mode === "edit" || mode === "unmap") && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 6 }}>
          {mode === "unmap" && (
            <p style={{ margin: 0, fontSize: 13 }}>
              The control stops counting for this risk (its gaps and the residual risk are recalculated) but stays on
              record with this reason.
            </p>
          )}
          <textarea
            aria-label="Reason for this control change (required)"
            aria-required="true"
            placeholder="Reason (required)"
            rows={2}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
          />
          <button type="button" className="save-review-button" disabled={saving} onClick={save} style={{ alignSelf: "flex-start" }}>
            {saving ? "Saving…" : mode === "unmap" ? "Unmap control" : "Save new version"}
          </button>
        </div>
      )}

      {mode === "history" &&
        (revisions === null ? (
          <p role="status">Loading history…</p>
        ) : revisions.length === 0 ? (
          <p className="workflow-empty">No changes since the control was mapped.</p>
        ) : (
          <ul style={{ fontSize: 13, margin: "6px 0 0" }} aria-label="Control change history">
            {revisions.map((r) => (
              <li key={r.id}>
                v{r.version} {r.change_type.toLowerCase()} by {r.changed_by}, {when(r.changed_at)} —{" "}
                {r.change_type === "UNMAP"
                  ? "removed"
                  : r.changed_fields
                      .map((f) => `${f}: ${String(r.previous_config[f] ?? "—")} → ${String(r.new_config?.[f] ?? "—")}`)
                      .join("; ")}
                . Reason: {r.reason}
              </li>
            ))}
          </ul>
        ))}

      {error && (
        <p role="alert" className="field-error">
          {error}
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------

/** P3 (R-GOV-04): the backend's committee-readiness result, displayed as is. */
export function CommitteeReadinessPanel({
  assessmentId,
  purpose = "COMMITTEE_SUBMISSION",
  refreshKey = 0,
}: {
  assessmentId: number;
  purpose?: Readiness["purpose"];
  refreshKey?: number;
}) {
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    getReadiness(assessmentId, purpose)
      .then((data) => {
        if (!cancelled) {
          setReadiness(data);
          setError(null);
        }
      })
      .catch((err) => !cancelled && setError(friendlyError(err, "Readiness couldn't be loaded.")))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [assessmentId, purpose, refreshKey]);

  const title = purpose === "FINAL_DECISION" ? "Final decision readiness" : "Committee readiness";
  return (
    <section aria-label={title} style={{ border: "1px solid #e5e7eb", borderRadius: 6, padding: 12 }}>
      <h4 style={{ margin: "0 0 6px", display: "flex", gap: 8, alignItems: "center" }}>
        {title}
        {readiness && (
          <span
            style={{
              ...statusStyle(readiness.ready ? "CONFIRMED" : "REJECTED"),
              fontSize: 12,
            }}
          >
            {readiness.ready ? "Ready" : `Blocked (${readiness.blockers.length})`}
          </span>
        )}
      </h4>
      <p style={{ margin: "0 0 8px", fontSize: 12, color: "#9a3412" }}>
        Calculated by the server. Rules: <strong>{POLICY_PENDING_LABEL}</strong>.
      </p>
      {loading && !readiness ? (
        <p role="status">Checking readiness…</p>
      ) : error ? (
        <p role="alert" className="field-error">{error}</p>
      ) : readiness ? (
        <>
          {readiness.blockers.length === 0 ? (
            <p role="status" style={{ margin: 0, color: "#166534" }}>
              <span aria-hidden="true">✓ </span>Nothing blocks this step.
            </p>
          ) : (
            <ul aria-label="Blocking reasons" style={{ margin: 0, paddingLeft: 18 }}>
              {readiness.blockers.map((b) => (
                <li key={b.code} style={{ marginBottom: 4 }}>
                  <strong>{b.message}</strong>
                  <div style={{ fontSize: 12 }}>
                    Next action: {b.next_action} <span style={{ color: "#6b7280" }}>— responsible: {b.responsible_role}</span>
                  </div>
                </li>
              ))}
            </ul>
          )}
          {readiness.warnings.length > 0 && (
            <ul aria-label="Readiness notes" style={{ margin: "8px 0 0", paddingLeft: 18, fontSize: 12, color: "#374151" }}>
              {readiness.warnings.map((w) => (
                <li key={w.code}>{w.message}</li>
              ))}
            </ul>
          )}
          {readiness.sod.active_exceptions.length > 0 && (
            <p style={{ margin: "8px 0 0", fontSize: 12 }}>
              Active SoD exceptions:{" "}
              {readiness.sod.active_exceptions
                .map((e) => `${e.reference} (${e.type.replace(/_/g, " ").toLowerCase()}, until ${when(e.end_at)}${e.declared ? "" : ", declaration missing"})`)
                .join("; ")}
            </p>
          )}
        </>
      ) : null}
    </section>
  );
}
