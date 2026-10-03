import { useState } from "react";

import { attachSourceEvidence, getFactorSourceEvidence, type FactorSourceEvidence } from "../api/sources";
import { friendlyError } from "../utils/errorMessages";

/**
 * R5.1/R5.3/R5.5: approved policy and regulatory references for one risk
 * factor -- passages already attached as evidence, and suggestions from
 * the library an analyst can attach. A factor with nothing attached is
 * shown as not supported by an approved source.
 */
export default function FactorSourceEvidencePanel({
  assessmentId,
  riskFactorId,
  canAttach,
}: {
  assessmentId: number;
  riskFactorId: number;
  canAttach: boolean;
}) {
  const [data, setData] = useState<FactorSourceEvidence | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState<string | null>(null);
  // P4 (Stage 5 AC): an outdated source needs an acknowledgement to attach.
  const [acknowledgements, setAcknowledgements] = useState<Record<string, string>>({});

  function load() {
    setError("");
    getFactorSourceEvidence(assessmentId, riskFactorId)
      .then((result) => {
        setData(result);
        setLoaded(true);
      })
      .catch((err) => setError(friendlyError(err, "Policy references couldn't be loaded.")));
  }

  async function attach(sourceId: number, passage: string, outdatedAcknowledgement?: string) {
    setSaving(`${sourceId}:${passage}`);
    setError("");
    try {
      await attachSourceEvidence(assessmentId, riskFactorId, sourceId, passage, outdatedAcknowledgement);
      load();
    } catch (err) {
      setError(friendlyError(err, "The reference couldn't be attached."));
    } finally {
      setSaving(null);
    }
  }

  return (
    <details
      style={{ marginTop: 6 }}
      onToggle={(event) => {
        if ((event.currentTarget as HTMLDetailsElement).open && !loaded) load();
      }}
    >
      <summary style={{ cursor: "pointer", fontSize: 13, fontWeight: 600 }}>Policy &amp; regulatory references</summary>
      {error && <p role="alert" style={{ color: "#b91c1c" }}>{error}</p>}
      {!data && !error && <p className="risk-reason">Loading…</p>}
      {data && (
        <div style={{ fontSize: 13 }}>
          {data.linked.length === 0 ? (
            <p className="risk-reason">
              <span aria-hidden="true">! </span>
              Not yet supported by an approved source.
            </p>
          ) : (
            <ul style={{ paddingLeft: 18, margin: "4px 0" }}>
              {data.linked.map((link) => (
                <li key={link.id}>
                  <strong>
                    {link.source_title} v{link.source_version}
                  </strong>
                  {link.effective_date ? `, effective ${link.effective_date}` : ""}
                  {link.reference ? ` — ${link.reference}` : ""}
                  {link.outdated && <span> — ⚠ outdated or retired; re-check before relying on it</span>}
                  <div>“{link.passage}”</div>
                  {link.outdated_at_attach && (
                    <div className="risk-reason">
                      Outdated when attached; acknowledged: {link.outdated_acknowledgement_reason}
                    </div>
                  )}
                  <div className="risk-reason">
                    Retrieved {new Date(link.retrieved_at).toLocaleString()} by {link.retrieved_by ?? "—"}
                  </div>
                </li>
              ))}
            </ul>
          )}

          {data.suggestions.length > 0 && (
            <>
              <p style={{ margin: "6px 0 2px" }}>Suggested from the approved library:</p>
              <ul style={{ paddingLeft: 18, margin: 0 }}>
                {data.suggestions.map((suggestion) => {
                  const key = `${suggestion.source_id}:${suggestion.passage}`;
                  return (
                    <li key={key} style={{ marginBottom: 4 }}>
                      <strong>
                        {suggestion.source_title} v{suggestion.source_version}
                      </strong>{" "}
                      ({suggestion.source_type_label}){suggestion.outdated && " — ⚠ past its review date"}
                      <div>“{suggestion.passage}”</div>
                      {canAttach && suggestion.outdated && (
                        <input
                          aria-label={`Why ${suggestion.source_title} can still be relied on`}
                          placeholder="Acknowledge: why it can still be relied on (at least 10 characters)"
                          value={acknowledgements[key] ?? ""}
                          onChange={(event) => setAcknowledgements((current) => ({ ...current, [key]: event.target.value }))}
                          style={{ display: "block", width: "100%", margin: "4px 0" }}
                        />
                      )}
                      {canAttach && (
                        <button
                          type="button"
                          className="doc-action-button"
                          disabled={
                            saving === key || (suggestion.outdated && (acknowledgements[key] ?? "").trim().length < 10)
                          }
                          onClick={() =>
                            attach(
                              suggestion.source_id,
                              suggestion.passage,
                              suggestion.outdated ? (acknowledgements[key] ?? "").trim() : undefined
                            )
                          }
                        >
                          {saving === key
                            ? "Attaching…"
                            : suggestion.outdated
                            ? "Acknowledge and attach"
                            : "Attach as evidence"}
                        </button>
                      )}
                    </li>
                  );
                })}
              </ul>
            </>
          )}
          {data.suggestions.length === 0 && data.linked.length === 0 && (
            <p className="risk-reason">No approved source in the library matches this factor.</p>
          )}
        </div>
      )}
    </details>
  );
}
