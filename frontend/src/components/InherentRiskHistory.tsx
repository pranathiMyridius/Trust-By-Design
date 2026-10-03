import { useState } from "react";

import { getInherentRiskHistory, type InherentRiskCalculation } from "../api/assessments";
import RiskLevelBadge from "./RiskLevelBadge";
import { friendlyError } from "../utils/errorMessages";

/**
 * R6.5: the thresholds the score is banded against, and every earlier
 * calculation -- a recalculation supersedes the previous result but never
 * deletes it (Stage 6 acceptance criteria).
 */
export default function InherentRiskHistory({ calc }: { calc: InherentRiskCalculation }) {
  const [history, setHistory] = useState<InherentRiskCalculation[] | null>(null);
  const [loadedFor, setLoadedFor] = useState<number | null>(null);
  const [error, setError] = useState("");

  const bands = [...calc.risk_bands].sort((a, b) => a.min - b.min);

  function load() {
    setError("");
    getInherentRiskHistory(calc.assessment_id)
      .then((rows) => {
        setHistory(rows);
        setLoadedFor(calc.id);
      })
      .catch((err) => setError(friendlyError(err, "The calculation history couldn't be loaded.")));
  }

  return (
    <>
      <div className="risk-reason">
        <strong>Risk bands (thresholds) — </strong>
        {bands.map((band, index) => (
          <span key={band.name}>
            {index > 0 && " · "}
            {band.name} {band.min}–{band.max}
          </span>
        ))}
      </div>

      <details
        onToggle={(event) => {
          if ((event.currentTarget as HTMLDetailsElement).open && loadedFor !== calc.id) load();
        }}
      >
        <summary style={{ cursor: "pointer", fontWeight: 600, fontSize: 14 }}>
          Calculation history (version {calc.version} is current)
        </summary>
        {error && <p role="alert">{error}</p>}
        {!history && !error && <p className="risk-reason">Loading…</p>}
        {history && (
          <div style={{ maxHeight: 280, overflow: "auto" }}>
            <table className="risk-table">
              <thead>
                <tr>
                  <th scope="col">Version</th>
                  <th scope="col">Calculated</th>
                  <th scope="col">Score</th>
                  <th scope="col">Band</th>
                  <th scope="col">Override</th>
                  <th scope="col">Methodology</th>
                </tr>
              </thead>
              <tbody>
                {history.map((row) => (
                  <tr key={row.id}>
                    <td>
                      v{row.version}
                      {row.is_current ? " (current)" : ""}
                    </td>
                    <td>
                      {new Date(row.calculated_at).toLocaleString()} by {row.calculated_by ?? "System"}
                    </td>
                    <td>
                      {row.calculated_score != null ? row.calculated_score.toFixed(2) : "—"}
                      {row.is_provisional ? " (provisional)" : ""}
                    </td>
                    <td>
                      <RiskLevelBadge level={row.calculated_band ?? row.risk_band} />
                    </td>
                    <td>
                      {row.overridden
                        ? `${row.override_value ?? "—"} (${row.override_band ?? "—"}) by ${row.override_by ?? "—"}: ${row.override_reason ?? ""}`
                        : "—"}
                    </td>
                    <td>
                      {row.methodology_name ?? "Default"}
                      {row.methodology_version ? ` v${row.methodology_version}` : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </details>
    </>
  );
}
