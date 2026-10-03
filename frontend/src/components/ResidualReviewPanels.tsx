import { useEffect, useId, useState } from "react";

import {
  confirmResidualRisk,
  decideRecommendedCondition,
  getRecommendedConditions,
  type RecommendedCondition,
  type ResidualRiskCalculation,
} from "../api/assessments";
import RiskLevelBadge from "./RiskLevelBadge";
import { friendlyError } from "../utils/errorMessages";

const BANDS = ["LOW", "MEDIUM", "HIGH", "CRITICAL"];

const STATUS_LABEL: Record<RecommendedCondition["status"], string> = {
  PROPOSED: "Awaiting decision",
  ACCEPTED: "Accepted",
  MODIFIED: "Accepted as modified",
  REJECTED: "Rejected",
};

/** R8.6: recommended conditions for elevated residual risk -- accept, modify or reject, with a reason. */
export function RecommendedConditionsPanel({
  assessmentId,
  canEdit,
  refreshKey,
}: {
  assessmentId: number;
  canEdit: boolean;
  refreshKey: unknown;
}) {
  const [conditions, setConditions] = useState<RecommendedCondition[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    getRecommendedConditions(assessmentId)
      .then((rows) => !cancelled && setConditions(rows))
      .catch((err) => !cancelled && setError(friendlyError(err, "Recommended conditions couldn't be loaded.")));
    return () => {
      cancelled = true;
    };
  }, [assessmentId, refreshKey]);

  return (
    <section className="workflow-card">
      <div className="workflow-card-header">
        <div>
          <h2>Recommended Conditions</h2>
          <p>
            Proposed by fixed rules when residual risk is high, critical or above tolerance. None is
            adopted until an analyst accepts or modifies it; every decision needs a reason.
          </p>
        </div>
      </div>

      {error && <p role="alert" style={{ color: "#b91c1c" }}>{error}</p>}
      {!conditions && !error && <p className="risk-reason">Loading…</p>}
      {conditions && conditions.length === 0 && (
        <p className="risk-reason">Residual risk is not elevated, so no conditions are recommended.</p>
      )}
      {conditions && conditions.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {conditions.map((condition) => (
            <ConditionRow
              key={condition.id}
              assessmentId={assessmentId}
              condition={condition}
              canEdit={canEdit}
              onDecided={(updated) =>
                setConditions((current) => (current ?? []).map((row) => (row.id === updated.id ? updated : row)))
              }
            />
          ))}
        </div>
      )}
    </section>
  );
}

function ConditionRow({
  assessmentId,
  condition,
  canEdit,
  onDecided,
}: {
  assessmentId: number;
  condition: RecommendedCondition;
  canEdit: boolean;
  onDecided: (updated: RecommendedCondition) => void;
}) {
  const [mode, setMode] = useState<"accept" | "modify" | "reject" | null>(null);
  const [reason, setReason] = useState("");
  const [text, setText] = useState(condition.recommended_text);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const id = useId();

  async function save() {
    if (!mode) return;
    if (!reason.trim()) {
      setError("Give a reason for this decision.");
      return;
    }
    if (mode === "modify" && !text.trim()) {
      setError("Give the modified wording.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      onDecided(
        await decideRecommendedCondition(assessmentId, condition.id, {
          decision: mode,
          reason: reason.trim(),
          text: mode === "modify" ? text.trim() : undefined,
        })
      );
      setMode(null);
      setReason("");
    } catch (err) {
      setError(friendlyError(err, "The decision couldn't be saved."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="control-item" style={{ flexDirection: "column", alignItems: "stretch" }}>
      <div className="control-item-main">
        <h3 style={{ fontSize: 14 }}>
          {condition.condition_label} — {STATUS_LABEL[condition.status]}
        </h3>
        <p>{condition.status === "MODIFIED" || condition.status === "ACCEPTED" ? condition.final_text : condition.recommended_text}</p>
        <p style={{ color: "#667085", fontSize: 13 }}>Why: {condition.rationale}</p>
        {condition.decided_by && (
          <p style={{ color: "#667085", fontSize: 13 }}>
            {STATUS_LABEL[condition.status]} by {condition.decided_by}
            {condition.decided_at ? ` on ${new Date(condition.decided_at).toLocaleString()}` : ""}: {condition.decision_reason}
          </p>
        )}
      </div>

      {canEdit && (
        <div style={{ marginTop: 6 }}>
          {!mode ? (
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <button type="button" className="secondary-button" onClick={() => setMode("accept")}>
                {condition.status === "PROPOSED" ? "Accept" : "Change to accept"}
              </button>
              <button type="button" className="secondary-button" onClick={() => setMode("modify")}>
                Modify
              </button>
              <button type="button" className="secondary-button" onClick={() => setMode("reject")}>
                Reject
              </button>
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {mode === "modify" && (
                <>
                  <label htmlFor={`${id}-text`}>Condition as it will be adopted</label>
                  <textarea id={`${id}-text`} rows={2} value={text} onChange={(event) => setText(event.target.value)} />
                </>
              )}
              <label htmlFor={`${id}-reason`}>
                Reason for {mode === "accept" ? "accepting" : mode === "modify" ? "modifying" : "rejecting"} (required)
              </label>
              <textarea id={`${id}-reason`} rows={2} value={reason} onChange={(event) => setReason(event.target.value)} />
              {error && <p role="alert" style={{ color: "#b91c1c" }}>{error}</p>}
              <div style={{ display: "flex", gap: 8 }}>
                <button type="button" className="primary-button" onClick={save} disabled={saving}>
                  {saving ? "Saving..." : "Save decision"}
                </button>
                <button type="button" className="secondary-button" onClick={() => setMode(null)} disabled={saving}>
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** R8: confirm or adjust the calculated residual risk, and show the difference. */
export function ResidualConfirmationPanel({
  assessmentId,
  residual,
  canEdit,
}: {
  assessmentId: number;
  residual: ResidualRiskCalculation | null;
  canEdit: boolean;
}) {
  const [record, setRecord] = useState<ResidualRiskCalculation | null>(residual);
  const [band, setBand] = useState(residual?.confirmed_band ?? residual?.residual_band ?? "MEDIUM");
  const [score, setScore] = useState("");
  const [reason, setReason] = useState("");
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const id = useId();

  const current = record ?? residual;
  if (!current) return null;

  async function save() {
    if (!reason.trim()) {
      setError("Give a reason for the confirmed residual risk.");
      return;
    }
    const parsed = score.trim() === "" ? null : Number(score);
    if (parsed != null && (Number.isNaN(parsed) || parsed < 0 || parsed > 100)) {
      setError("The score must be a number from 0 to 100.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      setRecord(await confirmResidualRisk(assessmentId, { band, score: parsed, reason: reason.trim() }));
      setOpen(false);
      setReason("");
    } catch (err) {
      setError(friendlyError(err, "The residual risk couldn't be confirmed."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="workflow-card">
      <div className="workflow-card-header">
        <div>
          <h2>Calculated vs Human-Confirmed Residual Risk</h2>
          <p>The calculated result is never overwritten; a confirmed value sits beside it with its reason.</p>
        </div>
      </div>

      <p className="risk-reason">
        <strong>Calculated:</strong> <RiskLevelBadge level={current.residual_band} />
        {current.residual_score != null ? ` — ${current.residual_score.toFixed(1)}` : ""}
      </p>
      {current.confirmed_band ? (
        <>
          <p className="risk-reason">
            <strong>Confirmed:</strong> <RiskLevelBadge level={current.confirmed_band} />
            {current.confirmed_score != null ? ` — ${current.confirmed_score.toFixed(1)}` : ""} by {current.confirmed_by}
            {current.confirmed_at ? ` on ${new Date(current.confirmed_at).toLocaleString()}` : ""}
          </p>
          <p className="risk-reason">
            <strong>Difference:</strong>{" "}
            {current.confirmation_differs
              ? `band changed from ${current.residual_band} to ${current.confirmed_band}`
              : "same band as calculated"}
            {current.confirmed_score_difference != null
              ? ` (score ${current.confirmed_score_difference > 0 ? "+" : ""}${current.confirmed_score_difference})`
              : ""}
            . Reason: {current.confirmation_reason}
          </p>
        </>
      ) : (
        <p className="risk-reason">Not yet confirmed by an analyst.</p>
      )}

      {canEdit && current.frozen && (
        open ? (
          <div className="form-group">
            <label htmlFor={`${id}-band`}>Confirmed residual band</label>
            <select id={`${id}-band`} value={band} onChange={(event) => setBand(event.target.value)}>
              {BANDS.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
            <label htmlFor={`${id}-score`}>Confirmed score (optional, 0–100)</label>
            <input id={`${id}-score`} type="number" min={0} max={100} value={score} onChange={(event) => setScore(event.target.value)} />
            <label htmlFor={`${id}-reason`}>Reason (required)</label>
            <textarea id={`${id}-reason`} rows={2} value={reason} onChange={(event) => setReason(event.target.value)} />
            {error && <p role="alert" style={{ color: "#b91c1c" }}>{error}</p>}
            <div className="form-actions">
              <button type="button" className="secondary-button" onClick={() => setOpen(false)} disabled={saving}>
                Cancel
              </button>
              <button type="button" className="primary-button" onClick={save} disabled={saving}>
                {saving ? "Saving..." : "Save confirmation"}
              </button>
            </div>
          </div>
        ) : (
          <button type="button" className="secondary-button" onClick={() => setOpen(true)}>
            {current.confirmed_band ? "Change confirmation" : "Confirm or adjust residual risk"}
          </button>
        )
      )}
      {canEdit && !current.frozen && (
        <p className="risk-reason">The residual risk can be confirmed once the assessment reaches the Residual Risk stage.</p>
      )}
    </section>
  );
}
