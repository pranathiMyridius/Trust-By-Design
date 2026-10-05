// P5 (R16.1, R16.4): one assessment's retention status, legal hold and
// controlled soft delete. Shown at every stage, because a legal hold can be
// placed at any time. Eligibility, hold history and which actions the user
// may take all come from the server (app/governance/retention.py); the UI
// never computes or overrides them. Periods are provisional.
import { useCallback, useEffect, useState } from "react";
import {
  getAssessmentRetention,
  setAssessmentLegalHold,
  softDeleteAssessment,
  type AssessmentRetentionInfo,
} from "../api/assessments";
import { POLICY_PENDING_LABEL } from "../api/governanceRecords";

function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export default function RetentionHoldPanel({ assessmentId }: { assessmentId: number }) {
  const [retention, setRetention] = useState<AssessmentRetentionInfo | null>(null);
  const [retentionError, setRetentionError] = useState<string | null>(null);
  const [holdReason, setHoldReason] = useState("");
  const [matterReference, setMatterReference] = useState("");
  const [holdSaving, setHoldSaving] = useState(false);
  const [deleteReason, setDeleteReason] = useState("");
  const [deleteSaving, setDeleteSaving] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const loadRetention = useCallback(async () => {
    try {
      setRetention(await getAssessmentRetention(assessmentId));
      setRetentionError(null);
    } catch (error) {
      setRetentionError(error instanceof Error ? error.message : "Failed to load retention status");
    }
  }, [assessmentId]);

  useEffect(() => {
    let cancelled = false;
    getAssessmentRetention(assessmentId)
      .then((data) => !cancelled && (setRetention(data), setRetentionError(null)))
      .catch((error) => !cancelled && setRetentionError(error instanceof Error ? error.message : "Failed to load retention status"));
    return () => {
      cancelled = true;
    };
  }, [assessmentId]);

  async function handleSetLegalHold(hold: boolean) {
    if (!holdReason.trim()) {
      setRetentionError(hold ? "A reason is required to place a legal hold." : "A reason is required to release a legal hold.");
      return;
    }
    setHoldSaving(true);
    try {
      await setAssessmentLegalHold(assessmentId, hold, holdReason.trim(), matterReference.trim() || undefined);
      setHoldReason("");
      setMatterReference("");
      setRetentionError(null);
      await loadRetention();
    } catch (error) {
      setRetentionError(error instanceof Error ? error.message : "Failed to update legal hold");
    } finally {
      setHoldSaving(false);
    }
  }

  async function handleSoftDelete() {
    if (!deleteReason.trim()) {
      setDeleteError("A reason is required to delete this assessment's record.");
      return;
    }
    setDeleteSaving(true);
    setDeleteError(null);
    try {
      await softDeleteAssessment(assessmentId, deleteReason);
      setDeleteReason("");
      await loadRetention();
    } catch (error) {
      setDeleteError(error instanceof Error ? error.message : "Failed to delete assessment");
    } finally {
      setDeleteSaving(false);
    }
  }

  const actions = retention?.actions ?? {};
  return (
    // Bottom margin: the last card on the page stays clear of the fixed
    // "Back to top" button.
    <section className="workflow-card" style={{ marginTop: 16, marginBottom: 72 }} aria-label="Retention and legal hold">
      <div className="workflow-card-header">
        <div>
          <h2>Retention &amp; Legal Hold</h2>
          <p>Legal hold status, retention policy and deletion eligibility for this assessment.</p>
        </div>
        <span className="source-badge">RETENTION</span>
      </div>
      <div className="workflow-card-body">
      <p role="note" style={{ fontSize: 12, color: "#9a3412", margin: "0 0 6px" }}>
        Retention periods are provisional ({POLICY_PENDING_LABEL}); not compliance-approved.
      </p>

      {retentionError && <p role="alert" style={{ color: "#b91c1c" }}>{retentionError}</p>}
      {!retention && !retentionError && <p>Loading…</p>}

      {retention && (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <p>
            Legal hold: <strong>{retention.legal_hold ? "ACTIVE" : "None"}</strong>
            {retention.legal_hold_reason ? ` — ${retention.legal_hold_reason}` : ""}
          </p>
          <p>
            Retention policy:{" "}
            <strong>
              {retention.policy_version != null
                ? `version ${retention.policy_version}, ${retention.retention_days} days from the final decision`
                : "none in force"}
            </strong>
          </p>
          <p data-testid="retention-eligibility">
            Eligibility: <strong>{retention.eligibility.eligibility_status.replace(/_/g, " ").toLowerCase()}</strong>
            {retention.eligible_for_deletion_at ? ` (eligible from ${formatDateTime(retention.eligible_for_deletion_at)})` : ""}
            {" — "}
            {retention.eligibility.reason}
          </p>
          {retention.is_deleted && (
            <p role="alert" style={{ color: "#b91c1c" }}>
              Deleted by {retention.deleted_by} on {formatDateTime(retention.deleted_at)}: {retention.deletion_reason}
            </p>
          )}

          {(actions.set_hold?.allowed || actions.release_hold?.allowed) && (
            <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
              <input
                aria-label={retention.legal_hold ? "Reason for releasing the legal hold" : "Reason for legal hold"}
                placeholder={retention.legal_hold ? "Reason for releasing the hold (required)" : "Reason for legal hold (required)"}
                value={holdReason}
                onChange={(e) => setHoldReason(e.target.value)}
                style={{ flex: 1, minWidth: 220 }}
              />
              {!retention.legal_hold && (
                <input
                  aria-label="Matter reference"
                  placeholder="Matter reference (optional)"
                  maxLength={100}
                  value={matterReference}
                  onChange={(e) => setMatterReference(e.target.value)}
                  style={{ width: 200 }}
                />
              )}
              <button className="secondary-button" onClick={() => handleSetLegalHold(!retention.legal_hold)} disabled={holdSaving}>
                {retention.legal_hold ? "Release legal hold" : "Place legal hold"}
              </button>
            </div>
          )}
          {retention.hold_detail_visible && retention.legal_hold && actions.release_hold && !actions.release_hold.allowed && (
            <p style={{ fontSize: 12, color: "#6b7280" }}>Release unavailable: {actions.release_hold.reason}</p>
          )}
          {retention.hold_detail_visible && !retention.legal_hold && actions.set_hold && !actions.set_hold.allowed && (
            <p style={{ fontSize: 12, color: "#6b7280" }}>Placing a hold unavailable: {actions.set_hold.reason}</p>
          )}

          {actions.soft_delete?.allowed && (
            <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
              <input
                aria-label="Reason for deletion"
                placeholder="Reason for deletion"
                value={deleteReason}
                onChange={(e) => setDeleteReason(e.target.value)}
                style={{ flex: 1 }}
              />
              <button className="secondary-button" onClick={handleSoftDelete} disabled={deleteSaving}>
                {deleteSaving ? "Deleting..." : "Delete assessment record"}
              </button>
            </div>
          )}
          {deleteError && <p role="alert" style={{ color: "#b91c1c" }}>{deleteError}</p>}

          {retention.hold_detail_visible && retention.hold_history.length > 0 && (
            <>
              <strong style={{ fontSize: 13, marginTop: 6 }}>Legal hold history (read-only)</strong>
              <ol aria-label="Legal hold history" style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
                {retention.hold_history.map((h) => (
                  <li key={h.id}>
                    {formatDateTime(h.at)} — <strong>{h.action === "SET" ? "Placed" : "Released"}</strong> by {h.actor}: {h.reason}
                    {h.matter_reference ? ` (matter ${h.matter_reference})` : ""}
                  </li>
                ))}
              </ol>
            </>
          )}
        </div>
      )}
      </div>
    </section>
  );
}
