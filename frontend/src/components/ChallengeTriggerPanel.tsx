import { useEffect, useId, useState } from "react";

import {
  getChallengeTriggerConfig,
  updateChallengeTriggerConfig,
  type ChallengeTriggerConfig,
} from "../api/assessments";
import { friendlyError } from "../utils/errorMessages";

// R11.1: plain-language names for the challenge-review trigger conditions.
const TRIGGER_LABELS: Record<string, string> = {
  RISK_HIGH_OR_CRITICAL: "Risk is high or critical",
  EVIDENCE_MISSING: "Evidence is missing",
  CONTRADICTIONS_EXIST: "Contradictions exist",
  LOW_CONFIDENCE: "Confidence is low (provisional result)",
  MISSING_RISK_FACTORS: "Important risk factors are missing",
  RATING_MISMATCH: "Human and system ratings differ",
  WEAK_CONTROLS: "Controls are weak or unsupported",
  RESIDUAL_RISK_EXCEEDS_TOLERANCE: "Residual risk exceeds tolerance",
  HIGH_RISK_JURISDICTION_OR_TECHNOLOGY: "New high-risk jurisdiction or technology",
  UNRESOLVED_COUNTRY_REFERENCE: "A stated country could not be identified",
};

const RISK_LEVELS = ["LOW", "MEDIUM", "HIGH", "CRITICAL"];

function listText(values: string[]): string {
  return values.join(", ");
}

function parseList(text: string): string[] {
  return text
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean);
}

/** R11.1: authorized users configure the conditional challenge review. */
export default function ChallengeTriggerPanel() {
  const [config, setConfig] = useState<ChallengeTriggerConfig | null>(null);
  const [disabled, setDisabled] = useState<string[]>([]);
  const [levels, setLevels] = useState<string[]>([]);
  const [tolerance, setTolerance] = useState("");
  const [jurisdictions, setJurisdictions] = useState("");
  const [technologies, setTechnologies] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const id = useId();

  function apply(next: ChallengeTriggerConfig) {
    setConfig(next);
    setDisabled(next.disabled_triggers ?? []);
    setLevels(next.trigger_risk_levels);
    setTolerance(String(next.residual_risk_tolerance));
    setJurisdictions(listText(next.high_risk_jurisdictions));
    setTechnologies(listText(next.high_risk_technologies));
  }

  useEffect(() => {
    let cancelled = false;
    getChallengeTriggerConfig()
      .then((next) => !cancelled && apply(next))
      .catch((err) => !cancelled && setError(friendlyError(err, "Challenge triggers couldn't be loaded.")));
    return () => {
      cancelled = true;
    };
  }, []);

  if (!config) {
    return (
      <section className="content-card nfr-section" aria-labelledby={`${id}-title`}>
        <h3 id={`${id}-title`} style={{ margin: 0 }}>Challenge review triggers</h3>
        {error ? (
          <p className="nfr-banner nfr-banner--error" role="alert">{error}</p>
        ) : (
          <p className="risk-reason">Loading…</p>
        )}
      </section>
    );
  }

  const toleranceValue = Number(tolerance);
  const toleranceInvalid = tolerance.trim() === "" || Number.isNaN(toleranceValue) || toleranceValue < 0 || toleranceValue > 100;
  const levelsInvalid = levels.length === 0;

  async function save() {
    if (toleranceInvalid || levelsInvalid) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      apply(
        await updateChallengeTriggerConfig({
          disabled_triggers: disabled,
          trigger_risk_levels: levels,
          residual_risk_tolerance: toleranceValue,
          high_risk_jurisdictions: parseList(jurisdictions),
          high_risk_technologies: parseList(technologies),
        })
      );
      setMessage("Challenge trigger configuration saved. It applies the next time each assessment's challenge review runs.");
    } catch (err) {
      setError(friendlyError(err, "The challenge triggers couldn't be saved."));
    } finally {
      setBusy(false);
    }
  }

  const allTriggers = [...config.mandatory_triggers, ...config.configurable_triggers];

  return (
    <section className="content-card nfr-section" aria-labelledby={`${id}-title`}>
      <h3 id={`${id}-title`} style={{ margin: 0 }}>Challenge review triggers</h3>
      <p className="risk-reason">
        When any enabled condition holds, the assessment gets an additional challenge review, and
        each condition raises findings that must be resolved or accepted before committee. A
        disabled condition raises no findings. Two conditions can&apos;t be switched off: a high or
        critical risk always gets a challenge review, and an unidentified country is a safety
        check.
      </p>

      {error && (
        <p className="nfr-banner nfr-banner--error" role="alert">
          <span aria-hidden="true">✕ </span>
          {error}
        </p>
      )}
      {message && (
        <p className="nfr-banner nfr-banner--ok" role="status">
          <span aria-hidden="true">✓ </span>
          {message}
        </p>
      )}

      <fieldset style={{ border: "none", padding: 0, margin: "8px 0" }}>
        <legend style={{ fontWeight: 600 }}>Trigger conditions</legend>
        {allTriggers.map((name) => {
          const mandatory = config.mandatory_triggers.includes(name);
          return (
            <label key={name} style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 14, margin: "4px 0" }}>
              <input
                type="checkbox"
                checked={mandatory || !disabled.includes(name)}
                disabled={mandatory || busy}
                onChange={(event) =>
                  setDisabled((current) =>
                    event.target.checked ? current.filter((item) => item !== name) : [...current, name]
                  )
                }
              />
              {TRIGGER_LABELS[name] ?? name}
              {mandatory && <small style={{ color: "#6b7280" }}>(always on)</small>}
            </label>
          );
        })}
      </fieldset>

      <fieldset style={{ border: "none", padding: 0, margin: "8px 0" }}>
        <legend style={{ fontWeight: 600 }}>Risk levels that count as high or critical</legend>
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
          {RISK_LEVELS.map((level) => (
            <label key={level} style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 14 }}>
              <input
                type="checkbox"
                checked={levels.includes(level)}
                disabled={busy}
                onChange={(event) =>
                  setLevels((current) =>
                    event.target.checked ? [...current, level] : current.filter((item) => item !== level)
                  )
                }
              />
              {level}
            </label>
          ))}
        </div>
        {levelsInvalid && (
          <small role="alert" style={{ color: "#b91c1c" }}>Choose at least one risk level.</small>
        )}
      </fieldset>

      <div className="form-group">
        <label htmlFor={`${id}-tolerance`}>Residual risk tolerance (0–100)</label>
        <input
          id={`${id}-tolerance`}
          type="number"
          min={0}
          max={100}
          value={tolerance}
          onChange={(event) => setTolerance(event.target.value)}
          aria-invalid={toleranceInvalid}
          aria-describedby={toleranceInvalid ? `${id}-tolerance-error` : undefined}
        />
        {toleranceInvalid && (
          <small id={`${id}-tolerance-error`} role="alert" style={{ color: "#b91c1c" }}>
            Enter a number from 0 to 100.
          </small>
        )}
      </div>

      <div className="form-group">
        <label htmlFor={`${id}-jurisdictions`}>
          Additional high-risk jurisdictions (comma-separated, beyond the attested published lists)
        </label>
        <input
          id={`${id}-jurisdictions`}
          value={jurisdictions}
          onChange={(event) => setJurisdictions(event.target.value)}
        />
      </div>

      <div className="form-group">
        <label htmlFor={`${id}-technologies`}>High-risk technology keywords (comma-separated)</label>
        <input
          id={`${id}-technologies`}
          value={technologies}
          onChange={(event) => setTechnologies(event.target.value)}
        />
      </div>

      <button
        type="button"
        className="primary-button"
        onClick={save}
        disabled={busy || toleranceInvalid || levelsInvalid}
      >
        {busy ? "Saving…" : "Save trigger configuration"}
      </button>
    </section>
  );
}
