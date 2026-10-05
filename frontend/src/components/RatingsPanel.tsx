import { useEffect, useState } from "react";
import {
  getIndicativeScore,
  rateRiskFactorsBulk,
  type IndicativeScore,
  type RiskFactor,
} from "../api/assessments";

interface RatingsPanelProps {
  assessmentId: number;
  factors: RiskFactor[];
  categoryLabel: (category: string) => string;
  canEdit: boolean;
  // Reloads the factors and the official calculation after a save.
  onSaved: () => Promise<void>;
}

interface Draft {
  likelihood: number;
  impact: number;
  reason: string;
}

const SCALE = [1, 2, 3, 4, 5];

// "Save all ratings": every factor in one table, rated in place and saved in
// one request. Also shows the INDICATIVE score -- the inherent score as it
// would be if the AI's suggestions were confirmed. It is indicative only:
// the official score counts only ratings an analyst has saved.
export default function RatingsPanel({ assessmentId, factors, categoryLabel, canEdit, onSaved }: RatingsPanelProps) {
  const [drafts, setDrafts] = useState<Record<number, Draft>>({});
  const [indicative, setIndicative] = useState<IndicativeScore | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedCount, setSavedCount] = useState<number | null>(null);

  // The control-environment factor is assessed as a control, not rated here.
  const rows = factors.filter(
    (factor) => factor.applicable && !factor.excluded && factor.category !== "CONTROL_ENVIRONMENT_RISK"
  );
  const ratingKey = rows.map((f) => `${f.id}:${f.likelihood}:${f.impact}:${f.ai_suggested_likelihood}`).join("|");

  useEffect(() => {
    getIndicativeScore(assessmentId).then(setIndicative).catch(() => setIndicative(null));
  }, [assessmentId, ratingKey]);

  if (rows.length === 0) return null;

  const suggestionOf = (f: RiskFactor) =>
    f.ai_suggested_likelihood != null && f.ai_suggested_impact != null
      ? { likelihood: f.ai_suggested_likelihood, impact: f.ai_suggested_impact }
      : null;

  function draftFor(f: RiskFactor): Draft {
    const suggestion = suggestionOf(f);
    return (
      drafts[f.id] ?? {
        likelihood: f.likelihood ?? suggestion?.likelihood ?? 3,
        impact: f.impact ?? suggestion?.impact ?? 3,
        reason: "",
      }
    );
  }

  // Rows that would be saved: a factor with a suggestion that nobody has rated
  // yet (saved as confirmed), one the analyst changed, or an unrated one they
  // touched.
  function isPending(f: RiskFactor): boolean {
    const draft = draftFor(f);
    if (f.likelihood == null || f.impact == null) {
      return suggestionOf(f) !== null || drafts[f.id] !== undefined;
    }
    return draft.likelihood !== f.likelihood || draft.impact !== f.impact;
  }

  function needsReason(f: RiskFactor): boolean {
    const suggestion = suggestionOf(f);
    const draft = draftFor(f);
    return (
      suggestion !== null &&
      (draft.likelihood !== suggestion.likelihood || draft.impact !== suggestion.impact) &&
      isPending(f)
    );
  }

  const pending = rows.filter(isPending);
  const missingReason = pending.filter((f) => needsReason(f) && !draftFor(f).reason.trim());

  function update(f: RiskFactor, patch: Partial<Draft>) {
    setDrafts((current) => ({ ...current, [f.id]: { ...draftFor(f), ...patch } }));
    setSavedCount(null);
  }

  async function handleSave() {
    setError(null);
    setSavedCount(null);
    setSaving(true);
    try {
      await rateRiskFactorsBulk(
        assessmentId,
        pending.map((f) => {
          const draft = draftFor(f);
          return {
            risk_factor_id: f.id,
            likelihood: draft.likelihood,
            impact: draft.impact,
            reason: draft.reason.trim() || undefined,
          };
        })
      );
      setDrafts({});
      setSavedCount(pending.length);
      await onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save the ratings");
    } finally {
      setSaving(false);
    }
  }

  const fmt = (value: number | null) => (value == null ? "—" : value.toFixed(1));

  return (
    <section className="workflow-card" style={{ marginBottom: 16 }}>
      <div className="workflow-card-header">
        <div>
          <h2>Rate all factors</h2>
          <p>
            Rate every factor here and save once. Ratings left at the AI's suggestion are saved as confirmed; a rating
            that differs needs a reason.
          </p>
        </div>
      </div>

      {indicative && indicative.suggestions_used > 0 && (
        <div role="note" style={{ padding: "8px 12px", borderRadius: 6, background: "#eff8ff", color: "#175cd3", marginBottom: 12 }}>
          <strong>
            Indicative score: {fmt(indicative.indicative_score)}
            {indicative.indicative_band ? ` (${indicative.indicative_band})` : ""}
          </strong>
          <div style={{ fontSize: 13 }}>
            Based on the AI's suggested ratings for {indicative.suggestions_used} factor
            {indicative.suggestions_used === 1 ? "" : "s"} you haven't confirmed yet. It is not the official score
            {indicative.official_score != null ? ` (currently ${fmt(indicative.official_score)})` : " (not final until every factor is rated)"}
            .{indicative.unrated_without_suggestion > 0
              ? ` ${indicative.unrated_without_suggestion} factor${indicative.unrated_without_suggestion === 1 ? " has" : "s have"} no suggestion and ${indicative.unrated_without_suggestion === 1 ? "isn't" : "aren't"} counted.`
              : ""}
          </div>
        </div>
      )}

      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
          <thead>
            <tr style={{ textAlign: "left", borderBottom: "1px solid #e5e7eb" }}>
              <th style={{ padding: "6px 8px" }}>Factor</th>
              <th style={{ padding: "6px 8px" }}>AI suggestion</th>
              <th style={{ padding: "6px 8px" }}>Likelihood</th>
              <th style={{ padding: "6px 8px" }}>Impact</th>
              <th style={{ padding: "6px 8px" }}>Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((f) => {
              const suggestion = suggestionOf(f);
              const draft = draftFor(f);
              const label = categoryLabel(f.category);
              return (
                <tr key={f.id} style={{ borderBottom: "1px solid #f2f4f7", verticalAlign: "top" }}>
                  <td style={{ padding: "6px 8px" }}>{label}</td>
                  <td style={{ padding: "6px 8px" }}>
                    {suggestion ? `L${suggestion.likelihood} × I${suggestion.impact}` : "—"}
                  </td>
                  <td style={{ padding: "6px 8px" }}>
                    <select
                      aria-label={`${label} likelihood`}
                      value={draft.likelihood}
                      disabled={!canEdit || saving}
                      onChange={(event) => update(f, { likelihood: Number(event.target.value) })}
                    >
                      {SCALE.map((value) => (
                        <option key={value} value={value}>
                          {value}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td style={{ padding: "6px 8px" }}>
                    <select
                      aria-label={`${label} impact`}
                      value={draft.impact}
                      disabled={!canEdit || saving}
                      onChange={(event) => update(f, { impact: Number(event.target.value) })}
                    >
                      {SCALE.map((value) => (
                        <option key={value} value={value}>
                          {value}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td style={{ padding: "6px 8px", minWidth: 220 }}>
                    {needsReason(f) ? (
                      <input
                        aria-label={`${label}: reason for differing from the AI suggestion (required)`}
                        placeholder="Reason for differing from the AI (required)"
                        value={draft.reason}
                        disabled={!canEdit || saving}
                        onChange={(event) => update(f, { reason: event.target.value })}
                        style={{ width: "100%" }}
                      />
                    ) : f.likelihood != null && !isPending(f) ? (
                      <span>Rated {f.likelihood} × {f.impact}</span>
                    ) : isPending(f) ? (
                      <span style={{ color: "#175cd3" }}>
                        {suggestion ? "AI suggestion — not yet confirmed" : "Not saved"}
                      </span>
                    ) : (
                      <span style={{ color: "#667085" }}>Not rated</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {canEdit && (
        <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 12, flexWrap: "wrap" }}>
          <button
            type="button"
            className="primary-button"
            onClick={() => void handleSave()}
            disabled={saving || pending.length === 0 || missingReason.length > 0}
          >
            {saving ? "Saving..." : `Save all ratings (${pending.length})`}
          </button>
          {missingReason.length > 0 && (
            <span style={{ fontSize: 13, color: "#b45309" }}>
              A reason is needed for: {missingReason.map((f) => categoryLabel(f.category)).join(", ")}
            </span>
          )}
          {savedCount !== null && (
            <span role="status" style={{ fontSize: 13, color: "#067647" }}>
              Saved {savedCount} rating{savedCount === 1 ? "" : "s"}.
            </span>
          )}
        </div>
      )}
      {error && (
        <p role="alert" style={{ marginTop: 8, color: "#b91c1c" }}>
          {error}
        </p>
      )}
    </section>
  );
}
