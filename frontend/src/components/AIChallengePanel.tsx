import { useEffect, useState } from "react";
import {
  decideAIChallengeFinding,
  listAIChallengeFindings,
  runAIChallenge,
  type AIChallengeFinding,
  type AIChallengeRun,
} from "../api/assessments";

interface AIChallengePanelProps {
  assessmentId: number;
  // Mapped controls, to name the control a finding concerns.
  controls: { id: number; label: string }[];
  canEdit: boolean;
}

const CATEGORY_LABELS: Record<AIChallengeFinding["category"], string> = {
  MISSING_CONTROL: "Missing control",
  CONTROL_MISMATCH: "Control mismatch",
  EVIDENCE_GAP: "Evidence gap",
  CONTRADICTION: "Contradiction",
  COVERAGE: "Coverage",
  OTHER: "Other",
};

const RUN_MESSAGES: Record<Exclude<AIChallengeRun["status"], "OK">, string> = {
  AI_UNAVAILABLE: "The AI provider isn't configured, so the gap analysis couldn't run.",
  NO_RISKS: "There are no applicable risk factors to analyse yet.",
  FAILED: "The AI gap analysis couldn't be completed. Existing findings are unchanged; try again.",
};

// AI challenge analysis: gaps the model raises beyond the rule-based
// findings above. Advisory only -- they never block progression or change a
// score; a reviewer confirms or dismisses each one.
export default function AIChallengePanel({ assessmentId, controls, canEdit }: AIChallengePanelProps) {
  const [findings, setFindings] = useState<AIChallengeFinding[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [running, setRunning] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [dismissingId, setDismissingId] = useState<number | null>(null);
  const [reason, setReason] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listAIChallengeFindings(assessmentId)
      .then(setFindings)
      .catch(console.error)
      .finally(() => setLoaded(true));
  }, [assessmentId]);

  async function handleRun() {
    setRunning(true);
    setError(null);
    setMessage(null);
    try {
      const run = await runAIChallenge(assessmentId);
      setFindings(run.findings);
      if (run.status !== "OK") {
        setMessage(RUN_MESSAGES[run.status]);
      } else if (run.findings_raised === 0) {
        setMessage("The AI found nothing further to raise beyond the rule-based findings.");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "The AI gap analysis couldn't be run");
    } finally {
      setRunning(false);
    }
  }

  async function handleDecision(finding: AIChallengeFinding, decision: "CONFIRM" | "DISMISS") {
    if (decision === "DISMISS" && !reason.trim()) {
      setError("Give a reason for dismissing this finding.");
      return;
    }
    setBusyId(finding.id);
    setError(null);
    try {
      const updated = await decideAIChallengeFinding(assessmentId, finding.id, decision, reason.trim());
      setFindings((current) => current.map((item) => (item.id === updated.id ? updated : item)));
      setDismissingId(null);
      setReason("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "The decision couldn't be recorded");
    } finally {
      setBusyId(null);
    }
  }

  const controlLabel = (id: number | null) => controls.find((control) => control.id === id)?.label;
  const open = findings.filter((finding) => finding.status === "OPEN").length;

  return (
    <section className="workflow-card">
      <div className="workflow-card-header">
        <div>
          <h2>AI Gap Analysis</h2>
          <p>
            The AI reviews the risks, controls, evidence and uploaded documents and raises gaps the rules didn't. These
            are advisory: they don't block progression or change any score, and each needs your decision.
          </p>
        </div>
        <span className="ai-badge">AI SUGGESTION</span>
      </div>

      {canEdit && (
        <button className="secondary-button" onClick={handleRun} disabled={running}>
          {running ? "Analysing..." : findings.length > 0 ? "Re-run AI gap analysis" : "Run AI gap analysis"}
        </button>
      )}
      {open > 0 && (
        <p style={{ margin: "8px 0 0", fontSize: 13 }}>
          {open} finding{open === 1 ? "" : "s"} waiting for a decision.
        </p>
      )}
      {message && <p role="status" style={{ marginTop: 8, color: "#667085" }}>{message}</p>}
      {error && <p role="alert" style={{ marginTop: 8, color: "#b91c1c" }}>{error}</p>}

      {loaded && findings.length === 0 && !message && (
        <p style={{ marginTop: 8, color: "#667085" }}>No AI gap analysis has been run for this assessment yet.</p>
      )}

      <div className="challenge-list" style={{ marginTop: 12 }}>
        {findings.map((finding) => (
          <div className="challenge-item" key={finding.id} style={{ alignItems: "flex-start" }}>
            <div className={`challenge-icon ${finding.status === "OPEN" ? "warning" : "success"}`}>
              {finding.status === "OPEN" ? "!" : "✓"}
            </div>

            <div style={{ flex: 1 }}>
              <strong>{finding.title}</strong>
              <p style={{ margin: "2px 0" }}>
                {CATEGORY_LABELS[finding.category]} · {finding.severity.charAt(0) + finding.severity.slice(1).toLowerCase()}{" "}
                severity
                {controlLabel(finding.control_id) ? ` · Control: ${controlLabel(finding.control_id)}` : ""}
              </p>
              <p>{finding.detail}</p>
              {finding.quote && (
                <blockquote style={{ margin: "6px 0", paddingLeft: 10, borderLeft: "3px solid #d0d5dd", fontSize: 13 }}>
                  “{finding.quote}”
                </blockquote>
              )}

              {finding.status === "OPEN" && canEdit && (
                <div style={{ marginTop: 6 }}>
                  {dismissingId === finding.id ? (
                    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                      <textarea
                        aria-label="Reason for dismissing this finding (required)"
                        placeholder="Reason for dismissing (required)"
                        value={reason}
                        onChange={(event) => setReason(event.target.value)}
                        rows={2}
                      />
                      <div style={{ display: "flex", gap: 8 }}>
                        <button
                          className="primary-button"
                          disabled={busyId === finding.id}
                          onClick={() => void handleDecision(finding, "DISMISS")}
                        >
                          Dismiss finding
                        </button>
                        <button
                          className="secondary-button"
                          onClick={() => {
                            setDismissingId(null);
                            setReason("");
                          }}
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div style={{ display: "flex", gap: 8 }}>
                      <button
                        className="primary-button"
                        disabled={busyId === finding.id}
                        onClick={() => void handleDecision(finding, "CONFIRM")}
                      >
                        Confirm gap
                      </button>
                      <button
                        className="secondary-button"
                        onClick={() => {
                          setDismissingId(finding.id);
                          setReason("");
                          setError(null);
                        }}
                      >
                        Dismiss
                      </button>
                    </div>
                  )}
                </div>
              )}

              {finding.status !== "OPEN" && finding.decided_by && (
                <p style={{ margin: "4px 0 0", fontSize: 12, color: "#667085" }}>
                  {finding.status === "CONFIRMED" ? "Confirmed" : "Dismissed"} by {finding.decided_by}
                  {finding.decision_note ? ` — ${finding.decision_note}` : ""}
                </p>
              )}
            </div>

            <span className={`finding-status ${finding.status === "OPEN" ? "" : "resolved"}`}>
              {finding.status === "OPEN" ? "REVIEW" : finding.status}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}
