import { useState } from "react";

import {
  getAssessmentDraftVersion,
  getAssessmentDraftVersions,
  type AssessmentDraft,
  type AssessmentDraftVersionSummary,
} from "../api/assessments";
import { friendlyError } from "../utils/errorMessages";

/**
 * R9.3 / R9.4: every generated and edited version of the assessment
 * draft. An analyst edit creates a new version, so the original generated
 * content can always be opened and compared.
 */
export default function DraftVersionHistory({
  assessmentId,
  currentVersion,
}: {
  assessmentId: number;
  currentVersion: number;
}) {
  const [versions, setVersions] = useState<AssessmentDraftVersionSummary[] | null>(null);
  const [loadedFor, setLoadedFor] = useState<number | null>(null);
  const [opened, setOpened] = useState<AssessmentDraft | null>(null);
  const [error, setError] = useState("");

  function load() {
    setError("");
    getAssessmentDraftVersions(assessmentId)
      .then((rows) => {
        setVersions(rows);
        setLoadedFor(currentVersion);
      })
      .catch((err) => setError(friendlyError(err, "The draft history couldn't be loaded.")));
  }

  function open(versionId: number) {
    setError("");
    getAssessmentDraftVersion(assessmentId, versionId)
      .then(setOpened)
      .catch((err) => setError(friendlyError(err, "That draft version couldn't be loaded.")));
  }

  return (
    <details
      style={{ margin: "8px 0" }}
      onToggle={(event) => {
        if ((event.currentTarget as HTMLDetailsElement).open && loadedFor !== currentVersion) load();
      }}
    >
      <summary style={{ cursor: "pointer", fontWeight: 600, fontSize: 14 }}>
        Version history (v{currentVersion} is current)
      </summary>
      {error && <p role="alert">{error}</p>}
      {!versions && !error && <p className="risk-reason">Loading…</p>}
      {versions && (
        <table className="risk-table">
          <thead>
            <tr>
              <th scope="col">Version</th>
              <th scope="col">Generated</th>
              <th scope="col">Edited</th>
              <th scope="col">Accepted</th>
              <th scope="col" aria-label="Open" />
            </tr>
          </thead>
          <tbody>
            {versions.map((row) => (
              <tr key={row.id}>
                <td>
                  v{row.version}
                  {row.is_current ? " (current)" : ""}
                  {row.is_edited ? "" : " — as generated"}
                </td>
                <td>{new Date(row.generated_at).toLocaleString()}</td>
                <td>
                  {row.is_edited && row.edited_at
                    ? `${row.edited_by ?? "—"}, ${new Date(row.edited_at).toLocaleString()}`
                    : "—"}
                </td>
                <td>
                  {row.accepted_by && row.accepted_at
                    ? `${row.accepted_by}, ${new Date(row.accepted_at).toLocaleString()}`
                    : "—"}
                </td>
                <td>
                  <button type="button" className="doc-action-button" onClick={() => open(row.id)}>
                    View
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {opened && (
        <div style={{ border: "1px solid #e5e7eb", borderRadius: 6, padding: 12, marginTop: 8 }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
            <strong>
              Version {opened.version}
              {opened.is_edited ? ` — edited by ${opened.edited_by}` : " — as generated"}
            </strong>
            <button type="button" className="secondary-button" onClick={() => setOpened(null)}>
              Close
            </button>
          </div>
          <h4>Executive summary</h4>
          <p className="risk-reason">{opened.executive_summary}</p>
          <h4>Business change description</h4>
          <p className="risk-reason">{opened.business_change_description}</p>
          <h4>Recommendation</h4>
          <p className="risk-reason">{opened.analyst_recommendation}</p>
        </div>
      )}
    </details>
  );
}
