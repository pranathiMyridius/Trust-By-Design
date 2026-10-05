import { useEffect, useState } from "react";
import {
  decideEvidenceLink,
  listEvidenceLinks,
  runEvidenceCheck,
  type EvidenceCheckSummary,
  type EvidenceLink,
} from "../api/assessments";

interface ControlEvidencePanelProps {
  assessmentId: number;
  // Mapped controls, with the label to show for each.
  controls: { id: number; label: string; riskLabel?: string }[];
  canEdit: boolean;
  // Called after a decision changes a control's evidence.
  onChanged: () => Promise<void>;
}

const SUPPORT_LABELS: Record<EvidenceLink["support_level"], string> = {
  SUPPORTED: "Supported",
  PARTIAL: "Partly supported",
  NONE: "No supporting document found",
};

const CHECK_MESSAGES: Record<Exclude<EvidenceCheckSummary["status"], "OK">, string> = {
  NO_DOCUMENTS: "No documents have been uploaded yet, so there is nothing to check. Upload evidence on the Evidence stage.",
  AI_UNAVAILABLE: "The AI provider isn't configured, so the evidence check couldn't run.",
  NO_CONTROLS: "There are no mapped controls to check.",
};

// AI evidence check: for each control, which uploaded documents appear to
// support it. The AI only suggests -- an analyst accepts or rejects each
// suggestion, and accepting is what records evidence on the control.
export default function ControlEvidencePanel({
  assessmentId,
  controls,
  canEdit,
  onChanged,
}: ControlEvidencePanelProps) {
  const [links, setLinks] = useState<EvidenceLink[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [running, setRunning] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [bulkBusy, setBulkBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listEvidenceLinks(assessmentId)
      .then(setLinks)
      .catch(console.error)
      .finally(() => setLoaded(true));
  }, [assessmentId]);

  async function handleRun() {
    setRunning(true);
    setError(null);
    setMessage(null);
    try {
      const summary = await runEvidenceCheck(assessmentId);
      setLinks(summary.links);
      if (summary.status !== "OK") {
        setMessage(CHECK_MESSAGES[summary.status]);
      } else if (summary.controls_failed > 0) {
        setMessage(`${summary.controls_failed} control(s) couldn't be checked; try again.`);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "The evidence check couldn't be run");
    } finally {
      setRunning(false);
    }
  }

  async function handleDecision(link: EvidenceLink, decision: "ACCEPT" | "REJECT") {
    setBusyId(link.id);
    setError(null);
    try {
      const updated = await decideEvidenceLink(assessmentId, link.id, decision);
      setLinks((current) => current.map((item) => (item.id === updated.id ? updated : item)));
      if (decision === "ACCEPT") await onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "The decision couldn't be recorded");
    } finally {
      setBusyId(null);
    }
  }

  // Accept every waiting suggestion for one control type in a single action.
  // Each mapping is still accepted (and audited) on its own; this saves
  // clicking the same passage through once per risk.
  async function handleAcceptAll(groupLabel: string, waiting: EvidenceLink[]) {
    setBulkBusy(groupLabel);
    setError(null);
    try {
      for (const link of waiting) {
        const updated = await decideEvidenceLink(assessmentId, link.id, "ACCEPT");
        setLinks((current) => current.map((item) => (item.id === updated.id ? updated : item)));
      }
      await onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "The decisions couldn't be recorded");
    } finally {
      setBulkBusy(null);
    }
  }

  const byControl = new Map<number, EvidenceLink[]>();
  for (const link of links) {
    byControl.set(link.control_id, [...(byControl.get(link.control_id) ?? []), link]);
  }
  const rows = controls.filter((control) => byControl.has(control.id));
  const groups = new Map<string, typeof rows>();
  for (const control of rows) {
    groups.set(control.label, [...(groups.get(control.label) ?? []), control]);
  }
  const pending = links.filter((link) => link.status === "SUGGESTED" && link.support_level !== "NONE").length;

  return (
    <section className="workflow-card" style={{ marginTop: 16 }}>
      <div className="workflow-card-header">
        <div>
          <h2>Evidence Check</h2>
          <p>
            The AI reads the documents uploaded to this assessment and suggests which support each control. Nothing
            changes until you accept a suggestion.
          </p>
        </div>
        <span className="ai-badge">AI SUGGESTION</span>
      </div>

      {canEdit && (
        <button className="secondary-button" onClick={handleRun} disabled={running}>
          {running ? "Checking evidence..." : links.length > 0 ? "Re-run evidence check" : "Run evidence check"}
        </button>
      )}
      {pending > 0 && (
        <p style={{ margin: "8px 0 0", fontSize: 13 }}>
          {pending} suggestion{pending === 1 ? "" : "s"} waiting for your decision.
        </p>
      )}
      {message && <p role="status" style={{ marginTop: 8, color: "#667085" }}>{message}</p>}
      {error && <p role="alert" style={{ marginTop: 8, color: "#b91c1c" }}>{error}</p>}

      {loaded && rows.length === 0 && !message && (
        <p style={{ marginTop: 8, color: "#667085" }}>
          No evidence check has been run yet. It runs automatically when this stage is first entered, and again on request.
        </p>
      )}

      {/* One group per control type: the same control is often mapped to
          several risks, so grouping avoids a wall of near-identical cards
          and shows which risk each suggestion belongs to. */}
      <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 12 }}>
        {[...groups.entries()].map(([label, members]) => {
          const memberLinks = members.flatMap((member) => byControl.get(member.id) ?? []);
          const accepted = memberLinks.filter((link) => link.status === "ACCEPTED").length;
          const awaiting = memberLinks.filter(
            (link) => link.status === "SUGGESTED" && link.support_level !== "NONE"
          ).length;
          const unsupported = memberLinks.filter((link) => link.support_level === "NONE").length;
          const waiting = memberLinks.filter(
            (link) => link.status === "SUGGESTED" && link.support_level !== "NONE"
          );

          return (
            <details
              key={label}
              open={awaiting > 0}
              style={{ border: "1px solid #e5e7eb", borderRadius: 6, padding: "8px 12px" }}
            >
              <summary style={{ cursor: "pointer", fontSize: 14 }}>
                <strong>{label}</strong>
                <span style={{ color: "#667085", fontSize: 13 }}>
                  {` — mapped to ${members.length} risk${members.length === 1 ? "" : "s"}`}
                  {accepted > 0 ? ` · ${accepted} accepted` : ""}
                  {awaiting > 0 ? ` · ${awaiting} awaiting decision` : ""}
                  {unsupported > 0 ? ` · ${unsupported} with no document` : ""}
                </span>
              </summary>
              {canEdit && waiting.length > 1 && (
                <button
                  className="secondary-button"
                  style={{ marginTop: 8 }}
                  disabled={bulkBusy !== null}
                  onClick={() => void handleAcceptAll(label, waiting)}
                >
                  {bulkBusy === label ? "Accepting..." : `Accept all ${waiting.length} as evidence`}
                </button>
              )}
              {members.map((control) => (
                <div key={control.id} style={{ marginTop: 8 }}>
                  {control.riskLabel && (
                    <h4 style={{ fontSize: 13, margin: "0 0 4px" }}>For: {control.riskLabel}</h4>
                  )}
                  {(byControl.get(control.id) ?? []).map((link) => (
                    <EvidenceLinkItem
                      key={link.id}
                      link={link}
                      canEdit={canEdit}
                      busy={busyId === link.id}
                      onDecision={(decision) => void handleDecision(link, decision)}
                    />
                  ))}
                </div>
              ))}
            </details>
          );
        })}
      </div>
    </section>
  );
}

function EvidenceLinkItem({
  link,
  canEdit,
  busy,
  onDecision,
}: {
  link: EvidenceLink;
  canEdit: boolean;
  busy: boolean;
  onDecision: (decision: "ACCEPT" | "REJECT") => void;
}) {
  if (link.support_level === "NONE") {
    return (
      <p style={{ margin: "4px 0", color: "#667085", fontSize: 13 }}>
        {SUPPORT_LABELS.NONE}. The control stays unverified until evidence is uploaded.
      </p>
    );
  }

  return (
    <div style={{ borderTop: "1px solid #f2f4f7", padding: "8px 0" }}>
      <p style={{ margin: 0, fontSize: 13 }}>
        <strong>{link.document_name ?? "Document"}</strong>
        {link.document_version ? ` (v${link.document_version})` : ""} — {SUPPORT_LABELS[link.support_level]}
        {link.confidence ? `, ${link.confidence.toLowerCase()} confidence` : ""}
        {" · "}
        <span>{link.status.charAt(0) + link.status.slice(1).toLowerCase()}</span>
      </p>
      {!link.document_is_current && (
        <p role="alert" style={{ margin: "4px 0 0", fontSize: 13, color: "#b45309" }}>
          This document has since been replaced by a newer version. Re-run the check.
        </p>
      )}
      <details open={link.status === "SUGGESTED"}>
        <summary style={{ cursor: "pointer", fontSize: 12, color: "#667085" }}>Show evidence detail</summary>
      {link.quote && (
        <blockquote style={{ margin: "6px 0", paddingLeft: 10, borderLeft: "3px solid #d0d5dd", fontSize: 13 }}>
          “{link.quote}”
        </blockquote>
      )}
      {link.rationale && <p style={{ margin: "4px 0", fontSize: 13 }}>{link.rationale}</p>}
      {link.shortfalls.length > 0 && (
        <p style={{ margin: "4px 0", fontSize: 13, color: "#667085" }}>
          Not shown by this evidence: {link.shortfalls.join("; ")}
        </p>
      )}
      </details>
      {link.suggested_effectiveness && link.status === "SUGGESTED" && (
        <p style={{ margin: "4px 0", fontSize: 13, color: "#667085" }}>
          AI view of effectiveness: {link.suggested_effectiveness.replace(/_/g, " ").toLowerCase()} (for your
          information; accepting this evidence starts the rating from it, which you can confirm or change under
          “Assess control”).
        </p>
      )}
      {link.status === "SUGGESTED" && canEdit && (
        <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
          <button className="primary-button" disabled={busy} onClick={() => onDecision("ACCEPT")}>
            Accept as evidence
          </button>
          <button className="secondary-button" disabled={busy} onClick={() => onDecision("REJECT")}>
            Reject
          </button>
        </div>
      )}
      {link.status !== "SUGGESTED" && link.decided_by && (
        <p style={{ margin: "4px 0 0", fontSize: 12, color: "#667085" }}>
          {link.status === "ACCEPTED" ? "Accepted" : "Rejected"} by {link.decided_by}
        </p>
      )}
    </div>
  );
}
