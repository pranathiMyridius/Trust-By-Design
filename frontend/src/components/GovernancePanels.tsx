import { useEffect, useState } from "react";

import {
  getDecisionReadiness,
  type InherentRiskCalculation,
  type ResidualRiskCalculation,
} from "../api/assessments";
import { getDecisionRecord, type DecisionRecordResponse } from "../api/governance";
import { RiskLevelIcon } from "./RiskLevelBadge";

const CONTROL_RATING_LABEL: Record<string, string> = {
  WEAK: "Weak / absent",
  PARTIAL: "Partially effective",
  EFFECTIVE: "Effective / strong",
};

const CATEGORY_LABEL = (category: string) =>
  category
    .replace(/_RISK$/, "")
    .split("_")
    .map((word) => word.charAt(0) + word.slice(1).toLowerCase())
    .join(" ");

export function ResidualGridPanel({
  residual,
  error,
}: {
  residual: ResidualRiskCalculation | null;
  error: string | null;
}) {
  if (error) {
    return <p className="risk-reason" style={{ color: "#b91c1c" }}>{error}</p>;
  }
  if (!residual) {
    return <p className="risk-reason">Loading residual risk…</p>;
  }

  const ratings = ["WEAK", "PARTIAL", "EFFECTIVE"];
  const bands = Object.keys(residual.residual_grid?.cells ?? {});

  return (
    <section className="risk-results-section">
      <h2>Residual Risk (grid lookup)</h2>
      <p className="risk-reason">
        Residual risk is looked up, not subtracted: the inherent band and the
        control-effectiveness rating select one cell of the approved grid
        (version {residual.grid_version ?? "—"}, methodology{" "}
        {residual.methodology_version ?? "—"}). Controls can only lower risk,
        and a non-mitigable rule keeps its minimum level whatever the controls.{" "}
        {residual.frozen
          ? `Frozen as the value of record on ${
              residual.calculated_at ? new Date(residual.calculated_at).toLocaleString() : "—"
            }.`
          : "Preview — it is frozen when the assessment reaches Residual Risk."}
      </p>

      {residual.residual_band == null && (
        <p className="risk-reason" style={{ color: "#b45309" }}>
          <strong>No residual result yet — </strong>
          {residual.reason}
        </p>
      )}

      <div style={{ display: "flex", gap: 24, flexWrap: "wrap", margin: "12px 0" }}>
        <div>
          <div style={{ fontSize: 13, color: "#6b7280" }}>Inherent band</div>
          <div style={{ fontSize: 20, fontWeight: 700 }}>
            <RiskLevelIcon level={residual.inherent_band} /> {residual.inherent_band ?? "—"}
          </div>
        </div>
        <div>
          <div style={{ fontSize: 13, color: "#6b7280" }}>Control rating (weakest risk)</div>
          <div style={{ fontSize: 20, fontWeight: 700 }}>
            {residual.control_rating ? CONTROL_RATING_LABEL[residual.control_rating] : "—"}
          </div>
        </div>
        <div>
          <div style={{ fontSize: 13, color: "#6b7280" }}>Residual band</div>
          <div style={{ fontSize: 20, fontWeight: 700 }}>
            <RiskLevelIcon level={residual.residual_band} /> {residual.residual_band ?? "—"}
          </div>
        </div>
      </div>

      {residual.floors_applied.length > 0 && (
        <p className="risk-reason" role="alert" style={{ color: "#b91c1c" }}>
          <strong>Held at a non-mitigable level — </strong>
          {residual.floors_applied
            .map((floor) => `${floor.rule_code} keeps the grid's ${floor.band_before} at ${floor.band_after}`)
            .join("; ")}
          .
        </p>
      )}

      <div style={{ overflowX: "auto" }}>
        <table className="risk-table" style={{ width: "100%", marginBottom: 12 }}>
          <caption className="risk-reason" style={{ textAlign: "left" }}>
            Residual grid v{residual.residual_grid?.version} — the highlighted cell is this assessment
          </caption>
          <thead>
            <tr>
              <th scope="col">Inherent \ Controls</th>
              {ratings.map((rating) => (
                <th scope="col" key={rating}>{CONTROL_RATING_LABEL[rating]}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {bands.map((band) => (
              <tr key={band}>
                <th scope="row">{band}</th>
                {ratings.map((rating) => {
                  const active = band === residual.inherent_band && rating === residual.control_rating;
                  return (
                    <td
                      key={rating}
                      aria-current={active ? "true" : undefined}
                      style={active ? { outline: "2px solid #2563eb", fontWeight: 700 } : undefined}
                    >
                      {residual.residual_grid.cells[band]?.[rating] ?? "—"}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {residual.control_ratings.length > 0 && (
        <table className="risk-table" style={{ width: "100%" }}>
          <thead>
            <tr>
              <th scope="col">Risk</th>
              <th scope="col">Controls mapped</th>
              <th scope="col">Rating</th>
            </tr>
          </thead>
          <tbody>
            {residual.control_ratings.map((item) => (
              <tr key={item.risk_factor_id}>
                <td>{CATEGORY_LABEL(item.category)}</td>
                <td>{item.control_count}</td>
                <td>{CONTROL_RATING_LABEL[item.rating]}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

/** Methodology version and reference data behind an inherent result. */
export function CalculationProvenance({ calc }: { calc: InherentRiskCalculation }) {
  const used = calc.reference_data?.snapshots_used ?? [];
  const skipped = calc.reference_data?.snapshots_skipped ?? [];
  const unresolved = calc.reference_data?.unresolved_countries ?? [];

  return (
    <div style={{ margin: "8px 0" }}>
      <p className="risk-reason" style={{ margin: "0 0 4px" }}>
        <strong>Methodology: </strong>
        {calc.methodology_name ?? "Default"} {calc.methodology_version ?? "(unversioned)"}
        {calc.methodology_fingerprint && (
          <span style={{ color: "#6b7280" }} title={calc.methodology_fingerprint}>
            {" "}· {calc.methodology_fingerprint.slice(0, 19)}…
          </span>
        )}
      </p>
      <p className="risk-reason" style={{ margin: "0 0 4px" }}>
        <strong>Reference data used in scoring: </strong>
        {used.length
          ? used.map((s) => `${s.source} as of ${s.as_of} (attested by ${s.attested_by})`).join("; ")
          : "none attested"}
      </p>
      {calc.jurisdiction_matches.length > 0 && (
        <p className="risk-reason" style={{ margin: "0 0 4px" }}>
          <strong>Jurisdiction designations: </strong>
          {calc.jurisdiction_matches
            .map((m) => `${m.name} — ${m.tier.replace(/_/g, " ").toLowerCase()} (${m.source} ${m.as_of})`)
            .join("; ")}
        </p>
      )}
      {skipped.some((s) => s.would_match.length > 0) && (
        <p className="risk-reason" style={{ margin: "0 0 4px", color: "#b45309" }}>
          <strong>Held back (not attested, so not scored): </strong>
          {skipped
            .filter((s) => s.would_match.length > 0)
            .map((s) => `${s.source}${s.as_of ? ` as of ${s.as_of}` : ""}: ${s.would_match.join(", ")}`)
            .join("; ")}
          . An administrator must attest the snapshot against its primary source before it can
          affect a score.
        </p>
      )}
      {unresolved.length > 0 && (
        <p className="risk-reason" style={{ margin: 0, color: "#b45309" }}>
          <strong>Country text not recognised: </strong>
          {unresolved.join(", ")}
        </p>
      )}
    </div>
  );
}

/** The reproducibility record frozen at committee approval, if any. */
export function DecisionRecordPanel({ assessmentId }: { assessmentId: number }) {
  const [record, setRecord] = useState<DecisionRecordResponse | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getDecisionRecord(assessmentId)
      .then((result) => !cancelled && setRecord(result))
      .catch((err: Error) => !cancelled && setError(err.message));
    return () => {
      cancelled = true;
    };
  }, [assessmentId]);

  if (error) return <p className="risk-reason" style={{ color: "#b91c1c" }}>{error}</p>;
  if (record === undefined || record === null) return null;

  const r = record.record;
  const factors: {
    category: string;
    likelihood: number | null;
    excluded: boolean;
    evidence?: { quote_verified: boolean }[];
  }[] = r.factors ?? [];
  const rated = factors.filter((f) => f.likelihood != null && !f.excluded);
  const verifiedQuotes = factors.reduce(
    (total, f) => total + (f.evidence ?? []).filter((e) => e.quote_verified).length,
    0
  );
  const openFindings = (r.challenge_findings ?? []).filter(
    (f: { resolution_status: string }) => f.resolution_status === "OPEN"
  ).length;

  function download() {
    const blob = new Blob([JSON.stringify(record, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `decision-record-${assessmentId}-${record!.id}.json`;
    link.click();
    URL.revokeObjectURL(url);
  }

  return (
    <section
      aria-labelledby={`decision-record-${assessmentId}`}
      style={{ borderLeft: `3px solid ${record.intact ? "#166534" : "#b91c1c"}`, padding: "8px 12px", margin: "12px 0", background: "#fafafa" }}
    >
      <h4 id={`decision-record-${assessmentId}`} style={{ margin: "0 0 4px" }}>
        Decision record #{record.id}
      </h4>
      <p className="risk-reason" style={{ margin: "0 0 6px", color: record.intact ? "#166534" : "#b91c1c" }}>
        {record.intact
          ? "✓ Intact — the stored record matches its checksum."
          : "✕ Altered — the stored record no longer matches its checksum. Treat it as untrustworthy and escalate."}
      </p>
      <dl className="nfr-facts" style={{ margin: 0 }}>
        <div><dt>Decision</dt><dd>{record.decision.replace(/_/g, " ").toLowerCase()} by {record.decided_by}, {new Date(record.decided_at).toLocaleString()}</dd></div>
        <div><dt>Methodology</dt><dd>{r.methodology?.name ?? "Default"} {r.methodology?.version}</dd></div>
        <div>
          <dt>Reference data</dt>
          <dd>
            {(r.reference_data?.snapshots_used ?? []).length
              ? r.reference_data.snapshots_used.map((s: { source: string; as_of: string }) => `${s.source} ${s.as_of}`).join(", ")
              : "none attested"}
          </dd>
        </div>
        <div><dt>Inherent</dt><dd>{r.inherent?.risk_band}{r.inherent?.overridden ? ` (overridden to ${r.inherent.override_band})` : ""} · {rated.length} rated factors · {verifiedQuotes} verified quotes</dd></div>
        <div><dt>Rules fired</dt><dd>{(r.inherent?.triggered_rules ?? []).map((t: { rule_code: string }) => t.rule_code).join(", ") || "none"}</dd></div>
        <div><dt>Residual</dt><dd>{r.residual?.residual_band} (controls {r.residual?.control_rating}, grid v{r.residual?.grid_version})</dd></div>
        <div><dt>Challenge findings</dt><dd>{(r.challenge_findings ?? []).length} recorded, {openFindings} open at approval</dd></div>
        <div><dt>AI model</dt><dd>{r.ai?.risk_identification_model ?? "—"}</dd></div>
      </dl>
      <p className="risk-reason" style={{ margin: "6px 0 0" }}>
        <code title={record.checksum}>{record.checksum.slice(0, 27)}…</code>{" "}
        <button type="button" className="secondary-button" onClick={download}>
          Download full record (JSON)
        </button>
      </p>
    </section>
  );
}

/** What is still missing before a committee could approve. */
export function DecisionReadinessPanel({
  assessmentId,
  refreshKey,
}: {
  assessmentId: number;
  refreshKey?: unknown;
}) {
  const [state, setState] = useState<{ ready: boolean; missing: string[] } | null>(null);

  useEffect(() => {
    let cancelled = false;
    getDecisionReadiness(assessmentId)
      .then((result) => !cancelled && setState(result))
      .catch(() => !cancelled && setState(null));
    return () => {
      cancelled = true;
    };
  }, [assessmentId, refreshKey]);

  if (!state) return null;

  return (
    <div
      role="status"
      style={{
        borderLeft: `3px solid ${state.ready ? "#166534" : "#b45309"}`,
        padding: "6px 10px",
        margin: "12px 0",
        background: "#fafafa",
      }}
    >
      <p className="risk-reason" style={{ margin: 0 }}>
        <strong>
          {state.ready
            ? "✓ Decision record complete — approval can freeze a reproducible record."
            : "Decision record incomplete — approval will be refused until:"}
        </strong>
      </p>
      {!state.ready && (
        <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
          {state.missing.map((item) => (
            <li key={item} className="risk-reason">{item}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
