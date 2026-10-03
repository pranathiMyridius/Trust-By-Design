import { useEffect, useState } from "react";

import {
  RISK_INDICATORS,
  type AssessmentIntelligence,
  type DocumentWarning,
  type FieldProvenance,
  type RiskFactor,
} from "../api/assessments";
import {
  acknowledgeExpiredDocument,
  getIntakeHistory,
  updateFactorIndicators,
  type EvidenceDecision,
  type IntakeVersion,
} from "../api/traceability";
import { friendlyError } from "../utils/errorMessages";

// P4: evidence traceability on the assessment workflow.

const CONFIDENCE_STYLE: Record<string, { label: string; color: string }> = {
  HIGH: { label: "High", color: "#166534" },
  MEDIUM: { label: "Medium", color: "#92400e" },
  LOW: { label: "Low", color: "#991b1b" },
  USER_PROVIDED: { label: "Entered by a person", color: "#374151" },
};

const ORIGIN_LABEL: Record<string, string> = {
  AI_EXTRACTION: "AI extraction",
  RULE_EXTRACTION: "Rule-based extraction",
  INTAKE_FORM: "Intake form",
  USER_CORRECTION: "Corrected",
};

function show(value: unknown): string {
  if (Array.isArray(value)) return value.join(", ") || "—";
  if (value === null || value === undefined || value === "") return "—";
  return String(value);
}

function label(field: string): string {
  const text = field.replace(/_/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function location(record: FieldProvenance): string {
  if (!record.filename) return "—";
  const parts = [`${record.filename}${record.document_version ? ` v${record.document_version}` : ""}`];
  if (record.page) parts.push(`page ${record.page}`);
  if (record.sheet) parts.push(`sheet ${record.sheet}`);
  return parts.join(", ");
}

export function ConfidenceBadge({ level }: { level: string }) {
  const style = CONFIDENCE_STYLE[level] ?? { label: level, color: "#374151" };
  return (
    <span
      style={{
        border: `1px solid ${style.color}`,
        color: style.color,
        borderRadius: 10,
        padding: "0 8px",
        fontSize: 12,
        whiteSpace: "nowrap",
      }}
    >
      {style.label}
    </span>
  );
}

/** R2.4: where each profile value came from, and how reliable it is. */
export function ProfileProvenanceTable({ intelligence }: { intelligence: AssessmentIntelligence | null }) {
  const records = Object.values(intelligence?.field_provenance ?? {});
  if (!intelligence || records.length === 0) {
    return null;
  }
  return (
    <details style={{ margin: "4px 0 10px" }} data-testid="field-provenance">
      <summary>
        <strong>Field provenance</strong>{" "}
        <span className="risk-reason">
          {ORIGIN_LABEL[records[0].origin] ?? records[0].origin}
          {intelligence.extracted_at ? `, ${new Date(intelligence.extracted_at).toLocaleString()}` : ""}
          {" — "}confidence is checked against the documents' text, not taken from the AI
        </span>
      </summary>
      <table className="data-table" style={{ width: "100%", marginTop: 6 }}>
        <thead>
          <tr>
            <th>Field</th>
            <th>Value</th>
            <th>Source and location</th>
            <th>Confidence</th>
            <th>Confirmed</th>
          </tr>
        </thead>
        <tbody>
          {records.map((record) => (
            <tr key={record.field}>
              <td>{label(record.field)}</td>
              <td>
                {show(record.value)}
                {record.origin === "USER_CORRECTION" && record.replaces && (
                  <div className="risk-reason">
                    Extracted: <em>{show(record.replaces.value)}</em> (corrected by {record.corrected_by})
                  </div>
                )}
              </td>
              <td>
                {location(record)}
                {record.quote && <div className="risk-reason">"{record.quote}"</div>}
              </td>
              <td title={record.confidence_basis}>
                <ConfidenceBadge level={record.confidence} />
              </td>
              <td>{record.confirmed_by_user ? `Yes${record.confirmed_by ? `, ${record.confirmed_by}` : ""}` : "Not yet"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}

/** R2.6: an expired document is used only after someone decides on it. */
export function ExpiredEvidenceDecision({
  assessmentId,
  warning,
  canDecide,
  onChanged,
}: {
  assessmentId: number;
  warning: DocumentWarning;
  canDecide: boolean;
  onChanged: () => void;
}) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!warning.state) return null;

  const ack = warning.acknowledgement;
  async function decide(decision: EvidenceDecision) {
    setBusy(true);
    setError(null);
    try {
      await acknowledgeExpiredDocument(assessmentId, warning.document_id, decision, reason.trim());
      setReason("");
      onChanged();
    } catch (err) {
      setError(friendlyError(err, "Could not record the decision."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ margin: "4px 0 8px 26px" }} data-testid={`expired-evidence-${warning.document_id}`}>
      {ack && (
        <div className="risk-reason">
          {ack.decision === "USE_AS_EVIDENCE" ? "Used as evidence" : "Excluded from evidence"} by {ack.acknowledged_by}
          {ack.acknowledged_at ? ` on ${new Date(ack.acknowledged_at).toLocaleDateString()}` : ""}: {ack.reason}
        </div>
      )}
      {canDecide && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", marginTop: 4 }}>
          <input
            aria-label={`Reason for ${warning.filename}`}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="Reason (at least 10 characters)"
            style={{ flex: "1 1 240px" }}
          />
          <button
            type="button"
            className="secondary-button"
            disabled={busy || reason.trim().length < 10}
            onClick={() => decide("USE_AS_EVIDENCE")}
          >
            Use as evidence
          </button>
          <button
            type="button"
            className="secondary-button"
            disabled={busy || reason.trim().length < 10}
            onClick={() => decide("EXCLUDE_FROM_EVIDENCE")}
          >
            Exclude from evidence
          </button>
        </div>
      )}
      {error && (
        <div role="alert" className="risk-reason" style={{ color: "#991b1b" }}>
          {error}
        </div>
      )}
    </div>
  );
}

const TRIGGER_LABEL: Record<string, string> = {
  BASELINE: "State before first recorded change",
  CREATED: "Created",
  SUBMITTED: "Submitted",
  EDITED: "Edited",
  PROFILE_CREATED: "Profile created from the intake form",
  PROFILE_EXTRACTED: "Profile extracted from documents",
  PROFILE_CORRECTED: "Profile corrected",
  PROFILE_CONFIRMED: "Profile confirmed",
  PROFILE_UNCONFIRMED: "Profile confirmation withdrawn",
};

/** R3.4: every version of the request and the profile, with old -> new. */
export function IntakeHistoryPanel({ assessmentId, refreshKey }: { assessmentId: number; refreshKey?: unknown }) {
  const [versions, setVersions] = useState<IntakeVersion[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getIntakeHistory(assessmentId)
      .then((result) => !cancelled && setVersions(result.versions))
      .catch((err) => !cancelled && setError(friendlyError(err, "Could not load the intake history.")));
    return () => {
      cancelled = true;
    };
  }, [assessmentId, refreshKey]);

  if (error) return <div className="risk-reason">{error}</div>;
  if (!versions || versions.length === 0) return null;

  const ordered = [...versions].sort((a, b) => b.created_at.localeCompare(a.created_at) || b.version - a.version);
  return (
    <details style={{ margin: "8px 0" }} data-testid="intake-history">
      <summary>
        <strong>Intake history</strong> <span className="risk-reason">({versions.length} versions; the original is kept)</span>
      </summary>
      <ol style={{ margin: "6px 0 0", paddingLeft: 18 }}>
        {ordered.map((version) => (
          <li key={version.id} style={{ marginBottom: 6 }}>
            <strong>
              {version.record_type === "ASSESSMENT_REQUEST" ? "Request" : "Profile"} v{version.version}
            </strong>{" "}
            — {TRIGGER_LABEL[version.trigger] ?? version.trigger} by {version.changed_by},{" "}
            {new Date(version.created_at).toLocaleString()}
            {version.was_validated && <em> (after validation)</em>}
            {version.reason && <div className="risk-reason">Reason: {version.reason}</div>}
            {version.changes.length > 0 && (
              <ul style={{ margin: "2px 0 0", paddingLeft: 18 }}>
                {version.changes.map((change) => (
                  <li key={change.field}>
                    {label(change.field)}: <em>{show(change.old)}</em> → <strong>{show(change.new)}</strong>
                  </li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ol>
    </details>
  );
}

/** Stage 4 AC: the fixed rule that required this category. */
export function FactorRuleTriggers({ factor }: { factor: RiskFactor }) {
  const triggers = factor.rule_triggers ?? [];
  if (triggers.length === 0) return null;
  return (
    <div className="risk-reason" style={{ margin: "4px 0" }} data-testid={`rule-triggers-${factor.id}`}>
      {triggers.map((trigger) => (
        <div key={trigger.rule_id}>
          <strong>Required by fixed rule:</strong> {trigger.description}
          {trigger.effect === "FORCED_APPLICABLE" && " — the AI had assessed it as not applicable"}
          {trigger.effect === "ADDED" && " — the analysis returned nothing for it"}
          {". "}
          <span title={trigger.signals.map((s) => `${s.field}: ${s.context}`).join("\n")}>
            Signals: {trigger.signals.map((s) => s.keyword ?? s.field).join(", ")}
          </span>{" "}
          <em>(signal definitions pending business validation, ruleset {trigger.ruleset_version})</em>
        </div>
      ))}
    </div>
  );
}

/** R4.2: the analyst assesses the factor's detailed indicators. */
export function FactorIndicatorEditor({
  assessmentId,
  factor,
  canEdit,
  onSaved,
}: {
  assessmentId: number;
  factor: RiskFactor;
  canEdit: boolean;
  onSaved: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [selected, setSelected] = useState<string[]>(factor.indicators);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!canEdit || !factor.applicable || factor.excluded) return null;

  if (!editing) {
    return (
      <button
        type="button"
        className="secondary-button"
        onClick={() => {
          setSelected(factor.indicators);
          setEditing(true);
        }}
      >
        Edit indicators
      </button>
    );
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await updateFactorIndicators(assessmentId, factor.id, selected, reason.trim());
      setEditing(false);
      setReason("");
      onSaved();
    } catch (err) {
      setError(friendlyError(err, "Could not save the indicators."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <fieldset style={{ border: "1px solid #e5e7eb", borderRadius: 6, padding: "8px 12px", margin: "6px 0" }}>
      <legend>Indicators for this factor</legend>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 4 }}>
        {RISK_INDICATORS.map((indicator) => (
          <label key={indicator.value}>
            <input
              type="checkbox"
              checked={selected.includes(indicator.value)}
              onChange={(event) =>
                setSelected((current) =>
                  event.target.checked ? [...current, indicator.value] : current.filter((value) => value !== indicator.value)
                )
              }
            />{" "}
            {indicator.label}
          </label>
        ))}
      </div>
      <p className="risk-reason" style={{ margin: "6px 0" }}>
        The change is recorded in the override ledger. Escalation indicators (such as sanctions exposure) are critical
        changes and need approval before the committee.
      </p>
      <input
        aria-label="Reason for the indicator change"
        value={reason}
        onChange={(event) => setReason(event.target.value)}
        placeholder="Reason for the change"
        style={{ width: "100%" }}
      />
      <div className="form-actions">
        <button type="button" className="secondary-button" onClick={() => setEditing(false)} disabled={busy}>
          Cancel
        </button>
        <button type="button" className="primary-button" onClick={save} disabled={busy || !reason.trim()}>
          {busy ? "Saving..." : "Save indicators"}
        </button>
      </div>
      {error && (
        <div role="alert" className="risk-reason" style={{ color: "#991b1b" }}>
          {error}
        </div>
      )}
    </fieldset>
  );
}
