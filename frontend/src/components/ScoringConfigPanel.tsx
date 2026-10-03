import { useId, useState } from "react";

import { saveScoringConfig, type MethodologyConfig, type ScaleStep } from "../api/governance";
import { friendlyError } from "../utils/errorMessages";

const BANDS = ["LOW", "MEDIUM", "HIGH", "CRITICAL"];
const APPROVALS = ["ANALYST", "REVIEWER", "COMMITTEE"];
const APPROVAL_LABEL: Record<string, string> = {
  ANALYST: "FCRM analyst review",
  REVIEWER: "Reviewing manager",
  COMMITTEE: "Risk committee",
};

function humanize(value: string): string {
  return value.replace(/_RISK$/, "").replace(/_/g, " ").toLowerCase();
}

/**
 * R6.1: factor weights, likelihood and impact scales, risk bands and the
 * approvals each band requires, edited on an unlocked methodology version.
 * Saving needs a reason; a locked (used) methodology is changed on a clone.
 */
export default function ScoringConfigPanel({
  methodologyId,
  name,
  version,
  locked,
  config,
  onSaved,
}: {
  methodologyId: number;
  name: string;
  version: number;
  locked: boolean;
  config: MethodologyConfig;
  onSaved: (message: string) => void;
}) {
  const [weights, setWeights] = useState<Record<string, string>>(() =>
    Object.fromEntries(Object.entries(config.factor_weights).map(([key, value]) => [key, String(value)]))
  );
  const [likelihood, setLikelihood] = useState<ScaleStep[]>(config.likelihood_scale ?? []);
  const [impact, setImpact] = useState<ScaleStep[]>(config.impact_scale ?? []);
  const [bands, setBands] = useState(config.risk_bands.map((band) => ({ ...band })));
  const [approvals, setApprovals] = useState<Record<string, string[]>>(config.required_approvals ?? {});
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const id = useId();

  const sortedBands = [...bands].sort((a, b) => a.min - b.min);

  async function save() {
    if (!reason.trim()) {
      setError("Give a reason for the change.");
      return;
    }
    const parsedWeights = Object.fromEntries(Object.entries(weights).map(([key, value]) => [key, Number(value)]));
    if (Object.values(parsedWeights).some((value) => Number.isNaN(value) || value < 0)) {
      setError("Weights must be zero or positive numbers.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      await saveScoringConfig(methodologyId, {
        factor_weights: parsedWeights,
        likelihood_scale: likelihood,
        impact_scale: impact,
        risk_bands: sortedBands,
        required_approvals: approvals,
        reason: reason.trim(),
      });
      setReason("");
      onSaved(`Scoring configuration saved on ${name} v${version}.`);
    } catch (err) {
      setError(friendlyError(err, "The scoring configuration couldn't be saved."));
    } finally {
      setSaving(false);
    }
  }

  function scaleEditor(label: string, scale: ScaleStep[], setScale: (next: ScaleStep[]) => void) {
    return (
      <fieldset style={{ border: "none", padding: 0, margin: "8px 0" }}>
        <legend style={{ fontWeight: 600 }}>{label} scale</legend>
        {scale.map((step, index) => (
          <label key={step.value} style={{ display: "flex", gap: 8, alignItems: "center", margin: "2px 0" }}>
            <span style={{ width: 24 }}>{step.value}</span>
            <input
              aria-label={`${label} ${step.value} label`}
              value={step.label}
              disabled={locked || saving}
              onChange={(event) =>
                setScale(scale.map((item, i) => (i === index ? { ...item, label: event.target.value } : item)))
              }
            />
          </label>
        ))}
        {!locked && (
          <div style={{ display: "flex", gap: 8 }}>
            <button
              type="button"
              className="secondary-button"
              onClick={() => setScale([...scale, { value: scale.length + 1, label: `Level ${scale.length + 1}` }])}
            >
              Add step
            </button>
            <button
              type="button"
              className="secondary-button"
              disabled={scale.length <= 2}
              onClick={() => setScale(scale.slice(0, -1))}
            >
              Remove last step
            </button>
          </div>
        )}
      </fieldset>
    );
  }

  return (
    <section className="content-card nfr-section" aria-labelledby={`${id}-title`}>
      <h3 id={`${id}-title`}>
        Scoring configuration — {name} v{version}
      </h3>
      <p className="risk-reason">
        Factor weights, the likelihood and impact scales, the risk bands and the approvals each residual band requires.
        {locked && " 🔒 This version has produced results, so it can't be edited — clone it to change these."}
      </p>

      <fieldset style={{ border: "none", padding: 0, margin: "8px 0" }}>
        <legend style={{ fontWeight: 600 }}>Factor weights</legend>
        <div style={{ display: "grid", gridTemplateColumns: "minmax(200px, max-content) 100px", gap: "4px 12px" }}>
          {Object.keys(weights).map((category) => (
            <label key={category} style={{ display: "contents" }}>
              <span>{humanize(category)}</span>
              <input
                type="number"
                min={0}
                step="0.01"
                aria-label={`Weight for ${humanize(category)}`}
                value={weights[category]}
                disabled={locked || saving}
                onChange={(event) => setWeights((current) => ({ ...current, [category]: event.target.value }))}
              />
            </label>
          ))}
        </div>
      </fieldset>

      {scaleEditor("Likelihood", likelihood, setLikelihood)}
      {scaleEditor("Impact", impact, setImpact)}

      <fieldset style={{ border: "none", padding: 0, margin: "8px 0" }}>
        <legend style={{ fontWeight: 600 }}>Risk bands (scores 0–100, contiguous)</legend>
        {sortedBands.map((band) => (
          <div key={band.name} style={{ display: "flex", gap: 8, alignItems: "center", margin: "2px 0" }}>
            <span style={{ width: 80 }}>{band.name}</span>
            {(["min", "max"] as const).map((edge) => (
              <input
                key={edge}
                type="number"
                min={0}
                max={100}
                aria-label={`${band.name} ${edge}`}
                value={band[edge]}
                disabled={locked || saving}
                onChange={(event) =>
                  setBands((current) =>
                    current.map((row) => (row.name === band.name ? { ...row, [edge]: Number(event.target.value) } : row))
                  )
                }
                style={{ width: 80 }}
              />
            ))}
          </div>
        ))}
      </fieldset>

      <fieldset style={{ border: "none", padding: 0, margin: "8px 0" }}>
        <legend style={{ fontWeight: 600 }}>Approvals required by residual band</legend>
        {BANDS.map((band) => (
          <div key={band} style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center", margin: "2px 0" }}>
            <span style={{ width: 80 }}>{band}</span>
            {APPROVALS.map((approval) => (
              <label key={approval} style={{ display: "flex", gap: 4, alignItems: "center" }}>
                <input
                  type="checkbox"
                  checked={(approvals[band] ?? []).includes(approval)}
                  disabled={locked || saving}
                  onChange={(event) =>
                    setApprovals((current) => ({
                      ...current,
                      [band]: event.target.checked
                        ? [...(current[band] ?? []), approval]
                        : (current[band] ?? []).filter((item) => item !== approval),
                    }))
                  }
                />
                {APPROVAL_LABEL[approval]}
              </label>
            ))}
          </div>
        ))}
      </fieldset>

      {!locked && (
        <div className="form-group">
          <label htmlFor={`${id}-reason`}>Reason for the change (required)</label>
          <textarea id={`${id}-reason`} rows={2} value={reason} onChange={(event) => setReason(event.target.value)} />
          {error && (
            <p role="alert" style={{ color: "#b91c1c" }}>
              {error}
            </p>
          )}
          <button type="button" className="primary-button" onClick={save} disabled={saving}>
            {saving ? "Saving…" : "Save scoring configuration"}
          </button>
        </div>
      )}
    </section>
  );
}
