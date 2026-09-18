import { useEffect, useState } from "react";

import {
  getAssessmentDocuments,
  getAssessmentIntelligence,
  getRiskResults,
  getAssessmentAudit,
  updateAssessmentStatus,
  analyzeAssessment,
  getFcrmReview,
  saveFcrmReview,
} from "../api/assessments";

import type {
  Assessment,
  AssessmentDocument,
  AssessmentIntelligence,
  RiskResult,
  AuditEvent,
} from "../api/assessments";


interface AssessmentWorkflowProps {
  assessment: Assessment;
  onBack: () => void;
}

const workflowSteps = [
  "Intake",
  "Evidence",
  "Inherent Risk",
  "Controls",
  "Residual Risk",
  "FCRM Review",
  "Challenge",
  "Decision",
];
const controlMappings: Record<
  string,
  {
    name: string;
    adequacy: string;
    coverage: string;
    status: "EFFECTIVE" | "MINOR DEVIATION" | "NOT VALIDATED";
  }
> = {
  CUSTOMER: {
    name: "Customer Identity & KYC Verification",
    adequacy: "Strong",
    coverage: "95% Coverage",
    status: "EFFECTIVE",
  },

  OPERATIONAL: {
    name: "Transaction Monitoring & Velocity Controls",
    adequacy: "Medium",
    coverage: "Transaction Monitoring",
    status: "MINOR DEVIATION",
  },

  FINANCIAL: {
    name: "Transaction Limits & Financial Controls",
    adequacy: "Medium",
    coverage: "Limit Validation",
    status: "MINOR DEVIATION",
  },

  COMPLIANCE: {
    name: "Real-time Sanctions Screening",
    adequacy: "Strong",
    coverage: "Regulatory API Mapping",
    status: "EFFECTIVE",
  },

  TECHNOLOGY: {
    name: "API Security & Technology Controls",
    adequacy: "Strong",
    coverage: "API / AWS Controls",
    status: "EFFECTIVE",
  },

  THIRD_PARTY: {
    name: "Third-Party Processor Due Diligence",
    adequacy: "Weak",
    coverage: "Manual Intervention Fallback",
    status: "NOT VALIDATED",
  },
};


/* =========================================
   SHARED HELPERS (real-data formatting)
   ========================================= */

function formatDateTime(iso: string | null | undefined): string {
  if (!iso) {
    return "—";
  }

  const date = new Date(iso);

  if (Number.isNaN(date.getTime())) {
    return iso;
  }

  const pad = (value: number) => String(value).padStart(2, "0");

  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(
    date.getDate()
  )} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(
    date.getSeconds()
  )}`;
}

function classifyAuditTag(event: AuditEvent): "ai" | "deterministic" | "human" {
  const source = `${event.action} ${event.actor ?? ""}`.toLowerCase();

  if (
    source.includes("ai") ||
    source.includes("extract") ||
    source.includes("intelligence")
  ) {
    return "ai";
  }

  if (
    source.includes("analy") ||
    source.includes("risk") ||
    source.includes("deterministic") ||
    source.includes("score")
  ) {
    return "deterministic";
  }

  return "human";
}

function auditActionLabel(event: AuditEvent): string {
  if (event.details) {
    return event.details;
  }

  if (event.new_status) {
    return event.previous_status
      ? `Status changed from ${event.previous_status} to ${event.new_status}`
      : `Status set to ${event.new_status}`;
  }

  return event.action.replace(/_/g, " ");
}

/**
 * Backend confirmed: GET /api/assessments/documents/{document_id}/file
 * returns the original uploaded file (FileResponse), not nested under the
 * assessment id. Falls back to the extracted-text blob only if a document
 * somehow has no id (shouldn't happen) or the backend endpoint 404s.
 */
function documentFileUrl(
  _assessmentId: number,
  doc: AssessmentDocument
): string | null {
  return `http://127.0.0.1:8000/api/assessments/documents/${doc.id}/file`;
}

// Chrome/Edge/Firefox have no built-in renderer for Office formats — even
// with Content-Disposition: inline from the backend, opening a .docx/.xlsx
// URL in a new tab just downloads it (or offers to open it in a native
// app), which is what "View" was doing. There's no way around that without
// a server-side DOCX/XLSX -> PDF conversion step, which this app doesn't
// have. PDFs and plain text render inline in every browser, so only those
// use the real file for "View"; everything else falls back to showing the
// extracted text instead of silently downloading the binary.
const BROWSER_VIEWABLE_EXTENSIONS = new Set([".pdf", ".txt"]);

function isBrowserViewable(doc: AssessmentDocument): boolean {
  const fromFileType = doc.file_type?.toLowerCase();
  if (fromFileType && BROWSER_VIEWABLE_EXTENSIONS.has(fromFileType)) {
    return true;
  }

  const match = doc.filename?.toLowerCase().match(/\.[^.]+$/);
  return !!match && BROWSER_VIEWABLE_EXTENSIONS.has(match[0]);
}

function downloadDocument(
  assessmentId: number,
  doc: AssessmentDocument
) {
  const realFileUrl = documentFileUrl(assessmentId, doc);

  if (realFileUrl) {
    const link = document.createElement("a");
    link.href = realFileUrl;
    link.download = doc.filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    return;
  }

  const blob = new Blob([doc.extracted_text || ""], {
    type: "text/plain",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");

  link.href = url;
  link.download = doc.filename || `document-${doc.id}.txt`;

  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);

  URL.revokeObjectURL(url);
}

function openDocumentInBrowser(
  assessmentId: number,
  doc: AssessmentDocument
) {
  const realFileUrl = documentFileUrl(assessmentId, doc);

  if (realFileUrl && isBrowserViewable(doc)) {
    window.open(realFileUrl, "_blank", "noopener,noreferrer");
    return;
  }

  // No in-browser renderer for this format (e.g. DOCX/XLSX) — show the
  // extracted text as a best-effort preview instead of downloading the
  // original binary. "Download" (below) still fetches the real file.
  const blob = new Blob(
    [doc.extracted_text || "No extracted text available for this document."],
    { type: "text/plain" }
  );
  const url = URL.createObjectURL(blob);

  window.open(url, "_blank", "noopener,noreferrer");

  // Give the new tab time to load the blob before revoking it.
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

function calculateControlReduction(
  riskResults: RiskResult[]
) {
  if (riskResults.length === 0) {
    return 0;
  }

  const effectiveControls = riskResults.filter(
    (result) =>
      controlMappings[result.dimension]?.status ===
      "EFFECTIVE"
  ).length;

  const minorControls = riskResults.filter(
    (result) =>
      controlMappings[result.dimension]?.status ===
      "MINOR DEVIATION"
  ).length;

  const reduction =
    effectiveControls * 4 +
    minorControls * 2;

  return Math.min(reduction, 30);
}
function getRiskLevel(score: number) {
  if (score >= 80) {
    return "CRITICAL";
  }

  if (score >= 60) {
    return "HIGH";
  }

  if (score >= 30) {
    return "MEDIUM";
  }

  return "LOW";
}

/**
 * currentStep was plain component state with no persistence, so reopening
 * a record (or the parent re-rendering with a fresh `assessment` prop)
 * always fell back to step 1 regardless of how far along the assessment
 * actually was. These two helpers fix that:
 *  - stepStorageKey/readSavedStep/saveStep persist the last-viewed step
 *    per assessment id (localStorage), so a literal "reopen this record"
 *    resumes exactly where you left off.
 *  - inferStepFromStatus is the fallback for a record that's never been
 *    opened in this browser before — it maps the real backend status to
 *    a reasonable starting step instead of always guessing step 1.
 */
function stepStorageKey(assessmentId: number): string {
  return `assessment-step-${assessmentId}`;
}

function maxStepStorageKey(assessmentId: number): string {
  return `assessment-max-step-${assessmentId}`;
}

function readSavedMaxStep(assessmentId: number): number | null {
  if (typeof window === "undefined") {
    return null;
  }

  try {
    const raw = window.localStorage.getItem(
      maxStepStorageKey(assessmentId)
    );

    if (!raw) {
      return null;
    }

    const parsed = parseInt(raw, 10);

    if (
      !Number.isNaN(parsed) &&
      parsed >= 1 &&
      parsed <= workflowSteps.length
    ) {
      return parsed;
    }

    return null;
  } catch (error) {
    console.error(error);
    return null;
  }
}

function saveMaxStep(assessmentId: number, step: number) {
  if (typeof window === "undefined") {
    return;
  }

  try {
    window.localStorage.setItem(
      maxStepStorageKey(assessmentId),
      String(step)
    );
  } catch (error) {
    console.error(error);
  }
}

function readSavedStep(assessmentId: number): number | null {
  if (typeof window === "undefined") {
    return null;
  }

  try {
    const raw = window.localStorage.getItem(
      stepStorageKey(assessmentId)
    );

    if (!raw) {
      return null;
    }

    const parsed = parseInt(raw, 10);

    if (
      !Number.isNaN(parsed) &&
      parsed >= 1 &&
      parsed <= workflowSteps.length
    ) {
      return parsed;
    }

    return null;
  } catch (error) {
    console.error(error);
    return null;
  }
}

function saveStep(assessmentId: number, step: number) {
  if (typeof window === "undefined") {
    return;
  }

  try {
    window.localStorage.setItem(
      stepStorageKey(assessmentId),
      String(step)
    );
  } catch (error) {
    console.error(error);
  }
}

function inferStepFromStatus(status: string): number {
  switch (status) {
    case "DRAFT":
      return 1;
    case "READY_FOR_REVIEW":
    case "UNDER_REVIEW":
      // Somewhere in Evidence → Challenge; without a saved step we can't
      // know exactly which, so land on Evidence rather than re-guessing 1.
      return 2;
    case "APPROVED":
    case "REJECTED":
    case "REMEDIATION":
      return 8;
    default:
      return 1;
  }
}

/* =========================================
   STAGE 7 — CHALLENGE (real risk / evidence / audit data)
   ========================================= */

function ChallengeStage({
  assessment,
  riskResults,
  documents,
  intelligence,
  auditEvents,
  onDecision,
}: {
  assessment: Assessment;
  riskResults: RiskResult[];
  documents: AssessmentDocument[];
  intelligence: AssessmentIntelligence | null;
  auditEvents: AuditEvent[];
  onDecision: (status: string) => Promise<void>;
}) {
  const inherentScore = assessment.overall_score ?? 0;

  const controlReduction = calculateControlReduction(riskResults);

  const residualScore = Math.max(
    0,
    inherentScore - controlReduction
  );

  const residualLevel = getRiskLevel(residualScore);

  const [challengeComment, setChallengeComment] = useState("");
  const [decisionLoading, setDecisionLoading] = useState<string | null>(null);
  const [decisionLabel, setDecisionLabel] = useState<string | null>(null);
  const [decisionError, setDecisionError] = useState<string | null>(null);

  const challengeId = `CHL-${String(assessment.id).padStart(4, "0")}-01`;

  const riskFactorNames: Record<string, string> = {
    CUSTOMER: "Customer Segment & Onboarding",
    OPERATIONAL: "Operational & Transaction Risk",
    FINANCIAL: "Financial Crime Typology",
    COMPLIANCE: "Regulatory & Compliance Geography",
    TECHNOLOGY: "Channel & Technology Exposure",
    THIRD_PARTY: "Third-Party Processor Risk",
  };

  const getLikelihood = (score: number) => {
    if (score >= 90) return 5;
    if (score >= 80) return 4;
    if (score >= 60) return 3;
    if (score >= 30) return 2;
    return 1;
  };

  const getImpact = (score: number) => {
    if (score >= 90) return 5;
    if (score >= 80) return 4;
    if (score >= 60) return 4;
    if (score >= 30) return 3;
    return 2;
  };

  const gapCount = riskResults.filter(
    (result) =>
      controlMappings[result.dimension]?.status !==
      "EFFECTIVE"
  ).length;

  // Real regulatory considerations captured during intake, instead of fixed mock citations.
  const evidenceCitations = (
    intelligence?.regulatory_considerations?.length
      ? intelligence.regulatory_considerations
      : intelligence?.existing_controls ?? []
  ).slice(0, 4);

  // Rationale narrative built from the actual risk results, not a fixed paragraph.
  const sortedResults = [...riskResults].sort(
    (a, b) => b.score - a.score
  );
  const topResult = sortedResults[0];
  const secondResult = sortedResults[1];

  const rationaleText = topResult
    ? `Across ${riskResults.length} evaluated risk dimension${
        riskResults.length === 1 ? "" : "s"
      }, ${riskFactorNames[topResult.dimension] || topResult.dimension} carries the highest inherent exposure at ${topResult.score.toFixed(
        1
      )} (${topResult.severity}). ${topResult.reason}`
    : "No risk dimensions have been calculated for this assessment yet — run inherent risk analysis first.";

  const secondaryRationale = secondResult
    ? `${riskFactorNames[secondResult.dimension] || secondResult.dimension} follows at ${secondResult.score.toFixed(
        1
      )} (${secondResult.severity}). ${secondResult.reason}`
    : null;

  // Deterministic data-completeness score from real inputs (not a fabricated confidence figure).
  const dataCompleteness = Math.min(
    100,
    Math.round(
      (riskResults.length > 0 ? 35 : 0) +
        (documents.length > 0 ? 25 : 0) +
        (intelligence ? 25 : 0) +
        (assessment.evidence ? 15 : 0)
    )
  );

  const latestHumanAuditor = [...auditEvents]
    .reverse()
    .find((event) => classifyAuditTag(event) === "human" && event.actor)
    ?.actor;

  const sortedAudit = [...auditEvents].sort(
    (a, b) =>
      new Date(a.created_at).getTime() -
      new Date(b.created_at).getTime()
  );

  async function handleDecision(status: string, label: string) {
    setDecisionLoading(status);
    setDecisionError(null);

    try {
      await onDecision(status);
      setDecisionLabel(label);
    } catch (error) {
      setDecisionError(
        error instanceof Error
          ? error.message
          : "Failed to record decision"
      );
    } finally {
      setDecisionLoading(null);
    }
  }

  return (
    <div className="challenge-workspace">
      {/* =====================================================
          TOP SUMMARY
         ===================================================== */}

      <div className="challenge-summary-grid">
        <div className="challenge-summary-card">
          <span>INHERENT RISK CALCULATED</span>

          <strong>
            {inherentScore.toFixed(1)}
          </strong>

          <small
            className={`risk-chip ${
              getRiskLevel(inherentScore) === "LOW" ? "low" : "critical"
            }`}
          >
            {getRiskLevel(inherentScore)} VIA MATRIX
          </small>
        </div>

        <div className="challenge-summary-card">
          <span>RESIDUAL RISK TARGET</span>

          <strong>
            {residualScore.toFixed(1)}
          </strong>

          <small
            className={`risk-chip ${
              residualLevel === "LOW" ? "low" : "warning"
            }`}
          >
            {residualLevel} POST-CONTROL ENFORCEMENTS
          </small>
        </div>

        <div className="challenge-summary-card">
          <span>CONTROLS MAPPED</span>

          <strong>
            {riskResults.length}
          </strong>

          <small className="risk-chip warning">
            {gapCount} CONTROL GAPS
          </small>
        </div>
      </div>

      {/* =====================================================
          MAIN TWO COLUMN WORKSPACE
         ===================================================== */}

      <div className="challenge-layout">
        {/* ===================================================
            LEFT
           =================================================== */}

        <div className="challenge-left-column">

          {/* Risk Factor Ledger */}

          <section className="challenge-panel">
            <div className="challenge-panel-header">
              <div>
                <h3>Structured Risk Factor Ledger</h3>

                <p>
                  Likelihood × Impact — deterministic assessment
                </p>
              </div>

              <span className="deterministic-badge">
                DETERMINISTIC FORMULA ENFORCED
              </span>
            </div>

            {riskResults.length === 0 ? (
              <div className="risk-empty">
                <h3>No risk results yet</h3>
                <p>Run inherent risk analysis to populate this ledger.</p>
              </div>
            ) : (
              <>
                <div className="risk-ledger-header">
                  <span>RISK FACTOR</span>
                  <span>LIKELIHOOD</span>
                  <span>IMPACT</span>
                  <span>INHERENT SCORE</span>
                  <span>CONTROLS MAPPED</span>
                </div>

                <div className="risk-ledger">

                  {riskResults.map((result) => {
                    const likelihood =
                      getLikelihood(result.score);

                    const impact =
                      getImpact(result.score);

                    const control =
                      controlMappings[result.dimension];

                    return (
                      <div
                        className="risk-ledger-row"
                        key={result.id}
                      >
                        <div className="risk-factor">
                          <strong>
                            {riskFactorNames[result.dimension] ||
                              result.dimension}
                          </strong>

                          <p>
                            {result.reason}
                          </p>
                        </div>

                        <div className="risk-number">
                          {likelihood}
                        </div>

                        <div className="risk-number">
                          {impact}
                        </div>

                        <div>
                          <span
                            className={`inherent-score ${
                              result.score >= 80
                                ? "critical"
                                : result.score >= 60
                                ? "high"
                                : "medium"
                            }`}
                          >
                            {result.score.toFixed(1)}
                          </span>
                        </div>

                        <div>
                          <span
                            className={`control-count ${
                              control?.status ===
                              "EFFECTIVE"
                                ? "effective"
                                : control?.status ===
                                  "NOT VALIDATED"
                                ? "critical"
                                : "active"
                            }`}
                          >
                            {control ? control.status : "NOT MAPPED"}
                          </span>
                        </div>
                      </div>
                    );
                  })}

                </div>
              </>
            )}
          </section>

          {/* Regulatory Evidence */}

          <section className="challenge-panel evidence-panel">
            <div className="challenge-panel-header">
              <div>
                <h3>
                  Regulatory & SLA Evidence Retrieval
                </h3>

                <p>
                  Regulatory considerations captured during
                  assessment intake
                </p>
              </div>

              <span className="ai-badge">
                ✣ AI EXTRACTION
              </span>
            </div>

            {evidenceCitations.length === 0 ? (
              <div className="workflow-empty">
                No regulatory considerations captured for this
                assessment yet.
              </div>
            ) : (
              <div className="evidence-grid">

                {evidenceCitations.map((item, index) => (
                  <div className="evidence-card" key={index}>
                    <div className="evidence-card-title">
                      Regulatory Consideration {index + 1}
                    </div>

                    <p>
                      {item}
                    </p>

                    <small>
                      From assessment intelligence
                    </small>
                  </div>
                ))}

              </div>
            )}
          </section>
        </div>

        {/* ===================================================
            RIGHT
           =================================================== */}

        <div className="challenge-right-column">

          {/* AI Interpretation */}

          <section className="challenge-side-panel ai-panel">

            <div className="side-panel-title">
              <span className="ai-badge">
                ✣ AI INTERPRETATION
              </span>

              <span className="confidence">
                Data Completeness: {dataCompleteness}%
              </span>
            </div>

            <h3>
              Explainable Rationale & Inherent Risk Suggestion
            </h3>

            <p>
              {rationaleText}
            </p>

            {secondaryRationale && (
              <p>
                {secondaryRationale}
              </p>
            )}

          </section>

          {/* Human Decision */}

          <section className="challenge-side-panel human-panel">

            <div className="side-panel-title">
              <span className="human-label">
                ◉ HUMAN DECISION
              </span>

              <span className="challenge-filed">
                {challengeComment.trim().length > 0
                  ? "CHALLENGE DRAFTED"
                  : "AWAITING INPUT"}
              </span>
            </div>

            <h3>
              Challenge ID: {challengeId}
            </h3>

            <p>
              Document any challenge to the calculated risk or
              control assumptions before the committee decision.
            </p>

            <div className="challenge-comment-box">
              <strong>
                Challenge Finding
              </strong>

              <textarea
                value={challengeComment}
                onChange={(event) =>
                  setChallengeComment(event.target.value)
                }
                placeholder="e.g. Geographic likelihood should be independently validated against cross-border transaction volume..."
                rows={4}
              />
            </div>

            <div className="challenge-by">
              Reviewer:{" "}
              <strong>{latestHumanAuditor || "Current Reviewer"}</strong>
            </div>

          </section>

          {/* Decision Terminal */}

          <section className="decision-terminal">

            <div className="decision-terminal-header">
              <span>
                DECISION TERMINAL
              </span>

              <small>
                ◉ HUMAN DECISION
              </small>
            </div>

            <h3>
              Committee Approval Decision Queue
            </h3>

            <button
              className="approve-button"
              type="button"
              disabled={
                decisionLoading !== null ||
                assessment.status !== "UNDER_REVIEW"
              }
              onClick={() =>
                handleDecision(
                  "APPROVED",
                  "Risk mitigation approved & corridors authorized"
                )
              }
            >
              {decisionLoading === "APPROVED"
                ? "Recording…"
                : assessment.status !== "UNDER_REVIEW"
                ? `Assessment is ${assessment.status}`
                : "Approve Risk Mitigation & Authorize Corridors"}
            </button>

            <div className="decision-button-row">

              <button
                className="reject-button"
                type="button"
                disabled={
                  decisionLoading !== null ||
                  assessment.status !== "UNDER_REVIEW"
                }
                onClick={() =>
                  handleDecision("REJECTED", "Rejected & API blocked")
                }
              >
                {decisionLoading === "REJECTED"
                  ? "Recording…"
                  : "Reject & Block API"}
              </button>

              <button
                className="remand-button"
                type="button"
                disabled={
                  decisionLoading !== null ||
                  assessment.status !== "UNDER_REVIEW"
                }
                onClick={() =>
                  handleDecision(
                    "REMEDIATION",
                    "Sent back for remediation"
                  )
                }
              >
                {decisionLoading === "REMEDIATION"
                  ? "Recording…"
                  : "Remand for Remediation"}
              </button>

            </div>

            {decisionLabel && (
              <div className="decision-recorded">
                {decisionLabel} — recorded to audit trail.
              </div>
            )}

            {decisionError && (
              <div className="decision-error">
                {decisionError}
              </div>
            )}

          </section>

          {/* Audit Trail */}

          <section className="audit-trail-panel">

            <div className="audit-title">
              GOVERNED COMPLIANCE AUDIT TRAIL
            </div>

            {sortedAudit.length === 0 ? (
              <div className="audit-empty">
                No audit events recorded for this assessment yet.
              </div>
            ) : (
              <div className="audit-timeline">

                {sortedAudit.map((event) => (
                  <div className="audit-item" key={event.id}>
                    <div className="audit-dot" />

                    <div>
                      <small>
                        {formatDateTime(event.created_at)}
                      </small>

                      <p>
                        {auditActionLabel(event)}
                      </p>

                      <span
                        className={`audit-tag ${classifyAuditTag(
                          event
                        )}`}
                      >
                        {classifyAuditTag(event).toUpperCase()}
                      </span>
                    </div>
                  </div>
                ))}

              </div>
            )}
          </section>

        </div>
      </div>
    </div>
  );
}

/* =========================================
   STAGE 8 — COMMITTEE DECISION (new)
   ========================================= */

function DecisionStage({
  assessment,
  riskResults,
  documents,
  auditEvents,
  onDecision,
}: {
  assessment: Assessment;
  riskResults: RiskResult[];
  documents: AssessmentDocument[];
  auditEvents: AuditEvent[];
  onDecision: (status: string) => Promise<void>;
}) {
  const [decisionLoading, setDecisionLoading] = useState<string | null>(null);
  const [decisionLabel, setDecisionLabel] = useState<string | null>(null);
  const [decisionError, setDecisionError] = useState<string | null>(null);

  const inherentRisk = assessment.overall_score ?? 0;
  const controlReduction = calculateControlReduction(riskResults);
  const residualRisk = Math.max(inherentRisk - controlReduction, 0);

  // Proposed conditions are derived from real control gaps, not fixed mock text.
  const conditions = riskResults
    .filter(
      (result) =>
        controlMappings[result.dimension] &&
        controlMappings[result.dimension].status !== "EFFECTIVE"
    )
    .map((result) => {
      const mapping = controlMappings[result.dimension];

      return mapping.status === "NOT VALIDATED"
        ? `${mapping.name} must be independently validated before this corridor is authorized.`
        : `${mapping.name} must be remediated and re-tested within 90 days to close the identified deviation.`;
    });

  const latestReviewEvent = [...auditEvents]
    .reverse()
    .find(
      (event) =>
        event.actor && event.actor.trim().toLowerCase() !== "system"
    );

  const statusLower = (assessment.status || "").toLowerCase();
  const isApproved = statusLower === "approved";
  const isRejected = statusLower === "rejected";
  const isRemediation = statusLower === "remediation";
  const isFinalized = isApproved || isRejected || isRemediation;

  async function handleDecision(status: string, label: string) {
    setDecisionLoading(status);
    setDecisionError(null);

    try {
      await onDecision(status);
      setDecisionLabel(label);
    } catch (error) {
      setDecisionError(
        error instanceof Error
          ? error.message
          : "Failed to record committee decision"
      );
    } finally {
      setDecisionLoading(null);
    }
  }

  function handleExport() {
    const payload = {
      assessment,
      riskResults,
      documents: documents.map((doc) => ({
        id: doc.id,
        filename: doc.filename,
        file_type: doc.file_type,
      })),
      auditEvents,
      exportedAt: new Date().toISOString(),
    };

    const blob = new Blob(
      [JSON.stringify(payload, null, 2)],
      { type: "application/json" }
    );

    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");

    link.href = url;
    link.download = `assessment-${assessment.id}-audit-package.json`;

    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);

    URL.revokeObjectURL(url);
  }

  return (
    <div className="decision-layout">

      {/* LEFT */}

      <div className="decision-main">

        <section className="workflow-card">

          <div className="workflow-card-header">

            <div>
              <h2>
                Final Committee Board Disposition
              </h2>

              <p>
                Record the committee&apos;s binding decision on
                this assessment.
              </p>
            </div>

            <span
              className={`decision-status-badge ${
                isApproved
                  ? "approved"
                  : isRejected
                  ? "rejected"
                  : isRemediation
                  ? "remediation"
                  : ""
              }`}
            >
              {assessment.status
                ? assessment.status.toUpperCase()
                : "PENDING DECISION"}
            </span>

          </div>

          <div className="decision-actions">

            <button
              className="decision-button approve"
              type="button"
              disabled={
                decisionLoading !== null ||
                assessment.status !== "UNDER_REVIEW"
              }
              onClick={() =>
                handleDecision(
                  "APPROVED",
                  "Case approved by committee"
                )
              }
            >
              {decisionLoading === "APPROVED"
                ? "Recording…"
                : "Approve Case"}
            </button>

            <button
              className="decision-button conditional"
              type="button"
              disabled={
                decisionLoading !== null ||
                assessment.status !== "UNDER_REVIEW"
              }
              onClick={() =>
                handleDecision(
                  "REMEDIATION",
                  "Sent back for remediation with conditions"
                )
              }
            >
              {decisionLoading === "REMEDIATION"
                ? "Recording…"
                : "Approve With Conditions"}
            </button>

            <button
              className="decision-button reject"
              type="button"
              disabled={
                decisionLoading !== null ||
                assessment.status !== "UNDER_REVIEW"
              }
              onClick={() =>
                handleDecision(
                  "REJECTED",
                  "Case rejected & blocked"
                )
              }
            >
              {decisionLoading === "REJECTED"
                ? "Recording…"
                : "Reject & Block"}
            </button>

          </div>

          {assessment.status !== "UNDER_REVIEW" && (
            <p className="decision-conditions-note">
              These actions are only available while the
              assessment is UNDER_REVIEW (current status:{" "}
              {assessment.status}). Submit it for committee
              review from the FCRM Review step first.
            </p>
          )}

          <p className="decision-conditions-note">
            Your backend&apos;s allowed final statuses are
            APPROVED, REMEDIATION, and REJECTED. &quot;Approve
            With Conditions&quot; records REMEDIATION, since
            the backend doesn&apos;t have a separate
            conditional-approval status — the proposed
            conditions below are tracked in this UI.
          </p>

          <div className="decision-conditions-box">
            <strong>
              Proposed Binding Compliance Conditions ({conditions.length})
            </strong>

            {conditions.length === 0 ? (
              <p className="no-conditions">
                No open control gaps — no binding conditions are
                required.
              </p>
            ) : (
              <ol>
                {conditions.map((condition, index) => (
                  <li key={index}>
                    {condition}
                  </li>
                ))}
              </ol>
            )}
          </div>

          {decisionLabel && (
            <div className="decision-result-banner">
              ✓ {decisionLabel} — status updated to &quot;
              {assessment.status}&quot;.
            </div>
          )}

          {decisionError && (
            <div className="decision-error-banner">
              {decisionError}
            </div>
          )}

        </section>

        <section className="workflow-card">

          <div className="workflow-card-header">

            <div>
              <h2>
                Authorized Electronic & Cryptographic Signatures
              </h2>

              <p>
                Signature status for the assessment record.
              </p>
            </div>

          </div>

          <div className="signatures-grid">

            <div className="signature-panel">
              <span>
                FCRM Representative
              </span>

              <strong>
                {latestReviewEvent?.actor || "Unassigned"}
              </strong>

              <span className="signature-status signed">
                SIGNED · COMPLIANT
              </span>
            </div>

            <div className="signature-panel">
              <span>
                Board Designee
              </span>

              <strong>
                {isFinalized
                  ? "Signed"
                  : "Signature Pending"}
              </strong>

              <span
                className={`signature-status ${
                  isFinalized ? "signed" : "pending"
                }`}
              >
                {isFinalized
                  ? "SIGNATURE RECORDED"
                  : "AWAITING COMMITTEE DECISION"}
              </span>
            </div>

          </div>

        </section>

      </div>

      {/* RIGHT */}

      <aside className="decision-sidebar">

        <section className="ai-metadata-card">

          <span>
            ASSESSMENT METADATA
          </span>

          <div className="ai-metadata-row">
            <span>Risk Dimensions Evaluated</span>
            <strong>{riskResults.length}</strong>
          </div>

          <div className="ai-metadata-row">
            <span>Documents Analyzed</span>
            <strong>{documents.length}</strong>
          </div>

          <div className="ai-metadata-row">
            <span>Inherent → Residual</span>
            <strong>
              {inherentRisk.toFixed(1)} → {residualRisk.toFixed(1)}
            </strong>
          </div>

          <div className="ai-metadata-row">
            <span>Last Updated</span>
            <strong>{formatDateTime(assessment.updated_at)}</strong>
          </div>

          <button
            className="export-audit-button"
            type="button"
            onClick={handleExport}
          >
            Export Audit Package (JSON)
          </button>

        </section>

        <section className="decision-governance-card">

          <span>
            GOVERNANCE
          </span>

          <h3>
            Committee decision authority
          </h3>

          <p>
            This decision is logged to the assessment&apos;s audit
            trail and becomes the binding disposition for this
            case.
          </p>

        </section>

      </aside>

    </div>
  );
}

function AssessmentWorkflow({
  assessment,
  onBack,
}: AssessmentWorkflowProps) {
  const [assessmentState, setAssessmentState] =
    useState<Assessment>(assessment);

  const [intelligence, setIntelligence] =
    useState<AssessmentIntelligence | null>(null);

  const [documents, setDocuments] =
    useState<AssessmentDocument[]>([]);
    const [riskResults, setRiskResults] = useState<RiskResult[]>([]);
    const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([]);

  const [loading, setLoading] = useState(true);

const [currentStep, setCurrentStepState] = useState<number>(() =>
  readSavedStep(assessment.id) ?? inferStepFromStatus(assessment.status)
);

// Tracks the furthest step ever reached for this assessment, independent
// of currentStep. currentStep moves both forward and backward as the user
// navigates the stepper, but a step's "completed" checkmark must only ever
// reflect progress actually made — stepping back to review an earlier
// section should never make a later, already-completed step look
// unfinished again.
const [maxStepReached, setMaxStepReachedState] = useState<number>(() => {
  const initialStep =
    readSavedStep(assessment.id) ?? inferStepFromStatus(assessment.status);
  return Math.max(readSavedMaxStep(assessment.id) ?? 1, initialStep);
});

// Wrap the setter so every navigation persists immediately, and so
// maxStepReached only ever moves forward.
function setCurrentStep(
  update: number | ((step: number) => number)
) {
  setCurrentStepState((prev) => {
    const next =
      typeof update === "function"
        ? (update as (step: number) => number)(prev)
        : update;

    saveStep(assessment.id, next);

    setMaxStepReachedState((prevMax) => {
      const nextMax = Math.max(prevMax, next);
      saveMaxStep(assessment.id, nextMax);
      return nextMax;
    });

    return next;
  });
}


  const [confirmIntakeLoading, setConfirmIntakeLoading] = useState(false);
  const [confirmIntakeError, setConfirmIntakeError] = useState<
    string | null
  >(null);

useEffect(() => {
  setAssessmentState(assessment);
  const initialStep =
    readSavedStep(assessment.id) ?? inferStepFromStatus(assessment.status);
  setCurrentStepState(initialStep);
  setMaxStepReachedState(
    Math.max(readSavedMaxStep(assessment.id) ?? 1, initialStep)
  );
}, [assessment.id]);

  useEffect(() => {
    async function loadIntakeData() {
      try {
        setLoading(true);

        const [
            assessmentIntelligence,
            assessmentDocuments,
            assessmentRiskResults,
            assessmentAuditEvents,
            ] = await Promise.all([
            getAssessmentIntelligence(assessment.id),
            getAssessmentDocuments(assessment.id),
            getRiskResults(assessment.id),
            getAssessmentAudit(assessment.id),
            ]);
        setIntelligence(assessmentIntelligence);
        setDocuments(assessmentDocuments);
        setRiskResults(assessmentRiskResults);
        setAuditEvents(assessmentAuditEvents);
      } catch (error) {
        console.error(error);
      } finally {
        setLoading(false);
      }
    }

    loadIntakeData();
  }, [assessment.id]);

  async function handleStatusChange(status: string) {
    const updated = await updateAssessmentStatus(
      assessmentState.id,
      status
    );

    setAssessmentState(updated);

    try {
      const events = await getAssessmentAudit(assessmentState.id);
      setAuditEvents(events);
    } catch (error) {
      console.error(error);
    }
  }

  // Confirming intake calls POST /analyze — this runs the deterministic
  // risk engine (populating RiskResults, overall_score, risk_level) AND,
  // as a side effect on the backend, moves the status DRAFT →
  // READY_FOR_REVIEW. There is no direct PATCH .../status?status=
  // READY_FOR_REVIEW path — that value isn't even in the backend's
  // allowed_statuses set for that endpoint, so /analyze is the only way
  // to reach it. Immediately after, we also submit it for committee
  // review (READY_FOR_REVIEW → UNDER_REVIEW) so the assessment sits in
  // UNDER_REVIEW for the entire Evidence → Challenge stretch (steps 2–7),
  // not just from step 6 onward. Only valid while the assessment is DRAFT.
  async function handleConfirmIntake() {
    if (assessmentState.status !== "DRAFT") {
      setConfirmIntakeError(
        `Cannot confirm intake: assessment is already "${assessmentState.status}", not "DRAFT".`
      );
      return;
    }

    setConfirmIntakeLoading(true);
    setConfirmIntakeError(null);

    try {
      const analyzed = await analyzeAssessment(assessmentState.id);
      setAssessmentState(analyzed);

      const underReview = await updateAssessmentStatus(
        analyzed.id,
        "UNDER_REVIEW"
      );
      setAssessmentState(underReview);

      const [freshRiskResults, freshAuditEvents] = await Promise.all([
        getRiskResults(assessmentState.id),
        getAssessmentAudit(assessmentState.id),
      ]);
      setRiskResults(freshRiskResults);
      setAuditEvents(freshAuditEvents);

      setCurrentStep((step) => Math.min(step + 1, workflowSteps.length));
    } catch (error) {
      setConfirmIntakeError(
        error instanceof Error
          ? error.message
          : "Failed to confirm intake"
      );
    } finally {
      setConfirmIntakeLoading(false);
    }
  }

  // Kept for the FCRM Review step's submit button, in case the assessment
  // is ever in READY_FOR_REVIEW without having gone through the combined
  // Confirm Intake action above (e.g. re-analyzed after REMEDIATION).
  const [submitReviewLoading, setSubmitReviewLoading] = useState(false);
  const [submitReviewError, setSubmitReviewError] = useState<string | null>(
    null
  );

  async function handleSubmitForCommitteeReview() {
    if (assessmentState.status !== "READY_FOR_REVIEW") {
      setSubmitReviewError(
        `Cannot submit for review: assessment is "${assessmentState.status}", not "READY_FOR_REVIEW".`
      );
      return;
    }

    setSubmitReviewLoading(true);
    setSubmitReviewError(null);

    try {
      await handleStatusChange("UNDER_REVIEW");
      setCurrentStep((step) => Math.min(step + 1, workflowSteps.length));
    } catch (error) {
      setSubmitReviewError(
        error instanceof Error
          ? error.message
          : "Failed to submit for committee review"
      );
    } finally {
      setSubmitReviewLoading(false);
    }
  }

  return (
    <div className="assessment-workflow">

      {/* Breadcrumb */}

      <div className="assessment-breadcrumb">
        <button onClick={onBack}>
          Assessments
        </button>

        <span>/</span>

        <span>{assessmentState.change_type}</span>

        <span>/</span>

        <strong>
          ASSESSMENT-{assessmentState.id}
        </strong>
      </div>


      {/* Header */}

      <div className="assessment-workflow-header">

        <div>

          <h1>
            {assessmentState.title}
          </h1>

          <p>
            {assessmentState.description}
          </p>

        </div>

        <div className="assessment-owner">

          <span>
            ASSESSMENT STATUS
          </span>

          <strong>
            {assessmentState.status}
          </strong>

        </div>

      </div>


      {/* Workflow Stepper */}

      <div className="workflow-stepper">

        {workflowSteps.map((step, index) => {

          const stepNumber = index + 1;

          const isActive =
            stepNumber === currentStep;

          const isCompleted =
            stepNumber < maxStepReached;

          return (
            <div
              className={`workflow-step ${
                isActive ? "active" : ""
              } ${
                isCompleted
                  ? "completed"
                  : ""
              }`}
              key={step}
              onClick={() => setCurrentStep(stepNumber)}
            >

              <div
                className={`workflow-number ${
                  isActive ? "active" : ""
                } ${
                  isCompleted ? "completed" : ""
                }`}
              >
                {isCompleted ? "✓" : stepNumber}
              </div>

              <span>
                {step}
              </span>

              {stepNumber <
                workflowSteps.length && (
                <div className="workflow-line" />
              )}

            </div>
          );
        })}

      </div>


      {/* Workflow Content */}

      {loading ? (

        <div className="workflow-loading">
          Loading assessment information...
        </div>

      ) : currentStep === 1 ? (

        <div className="intake-layout">

          {/* LEFT SIDE */}

          <div className="intake-main">

            <section className="workflow-card">

              <div className="workflow-card-header">

                <div>
                  <h2>
                    Structured Business Intake Request
                  </h2>

                  <p>
                    Business information extracted from the
                    assessment submission.
                  </p>
                </div>

                <span className="source-badge">
                  ASSESSMENT-{assessmentState.id}
                </span>

              </div>

              <div className="intake-grid">

                <div className="intake-field">
                  <span>CHANGE TYPE</span>

                  <strong>
                    {assessmentState.change_type}
                  </strong>
                </div>

                <div className="intake-field">
                  <span>BUSINESS LINE</span>

                  <strong>
                    {intelligence?.business_line ||
                      "Not available"}
                  </strong>
                </div>

                <div className="intake-field">
                  <span>TRANSACTION VOLUME</span>

                  <strong>
                    {intelligence?.transaction_volume ||
                      "Not available"}
                  </strong>
                </div>

                <div className="intake-field">
                  <span>MAXIMUM TRANSACTION</span>

                  <strong>
                    {intelligence?.maximum_transaction_limit ||
                      "Not available"}
                  </strong>
                </div>

              </div>

            </section>


            <section className="workflow-card">

              <div className="workflow-card-header">

                <div>
                  <h2>
                    Business Intelligence
                  </h2>

                  <p>
                    Structured information identified during
                    assessment intake.
                  </p>
                </div>

                <span className="ai-badge">
                  AI EXTRACTION
                </span>

              </div>

              <div className="metadata-list">

                <MetadataRow
                  label="Channels"
                  values={intelligence?.channels || []}
                />

                <MetadataRow
                  label="Countries"
                  values={intelligence?.countries || []}
                />

                <MetadataRow
                  label="Customer Segments"
                  values={
                    intelligence?.customer_segments || []
                  }
                />

                <MetadataRow
                  label="Third-Party Vendors"
                  values={
                    intelligence?.third_party_vendors || []
                  }
                />

                <MetadataRow
                  label="Data Shared"
                  values={
                    intelligence?.data_shared || []
                  }
                />

                <MetadataRow
                  label="Technologies"
                  values={
                    intelligence?.technologies || []
                  }
                />

              </div>

            </section>

          </div>


          {/* RIGHT SIDE */}

                   {/* RIGHT SIDE */}

          <aside className="intake-sidebar">

            {/* Intake Review */}

            <section className="intake-alert-card">

              <div className="alert-title">
                ⚠ Intake Review
              </div>

              <h3>
                {intelligence?.additional_risk_factors
                  ?.length || 0} Risk Factors Identified
              </h3>

              <p>
                Review the extracted business information
                before proceeding to evidence analysis.
              </p>

            </section>


            {/* Submitted Evidence */}

            <section className="workflow-card">

              <div className="workflow-card-header">

                <div>
                  <h2>
                    Submitted Evidence
                  </h2>

                  <p>
                    {documents.length} document
                    {documents.length === 1
                      ? ""
                      : "s"} attached
                  </p>
                </div>

              </div>

              <div className="document-list">

                {documents.length === 0 ? (

                  <div className="workflow-empty">
                    No evidence documents attached.
                  </div>

                ) : (

                  documents.map((document) => (

                    <div
                      className="workflow-document"
                      key={document.id}
                    >

                      <div className="document-file-icon">
                        📄
                      </div>

                      <div className="document-file-info">

                        <strong>
                          {document.filename}
                        </strong>

                        <span>
                          {document.file_type.toUpperCase()}
                        </span>

                      </div>

                      <span className="parsed-badge">
                        PARSED
                      </span>

                      <div className="document-actions">

                        <button
                          type="button"
                          className="doc-action-button"
                          onClick={() =>
                            openDocumentInBrowser(
                              assessmentState.id,
                              document
                            )
                          }
                        >
                          View
                        </button>

                        <button
                          type="button"
                          className="doc-action-button"
                          onClick={() =>
                            downloadDocument(
                              assessmentState.id,
                              document
                            )
                          }
                        >
                          Download
                        </button>

                      </div>

                    </div>

                  ))

                )}

              </div>

            </section>


            {/* Human Review */}

            <section className="human-input-card">

              <span className="human-label">
                HUMAN INPUT REQUIRED
              </span>

              <h3>
                Validate Assessment Intake
              </h3>

              <p>
                Confirm that the extracted entities
                accurately represent the proposed
                business change before continuing to
                risk calculation.
              </p>

              <button
                className="commit-button"
                type="button"
                disabled={
                  confirmIntakeLoading ||
                  assessmentState.status !== "DRAFT"
                }
                onClick={handleConfirmIntake}
              >
                {confirmIntakeLoading
                  ? "Analyzing & submitting for review…"
                  : assessmentState.status !== "DRAFT"
                  ? `Already ${assessmentState.status}`
                  : "Analyze, Confirm & Submit for Review → Continue"}
              </button>

              {confirmIntakeError && (
                <p className="commit-error">
                  {confirmIntakeError}
                </p>
              )}

            </section>

          </aside>

        </div>

      ) : currentStep === 2 ? (

        <div className="evidence-layout">

          {/* Evidence Main */}

          <div className="evidence-main">

            <section className="workflow-card">

              <div className="workflow-card-header">

                <div>
                  <h2>
                    Evidence Analysis
                  </h2>

                  <p>
                    Review the source material and evidence
                    supporting this assessment.
                  </p>
                </div>

                <span className="ai-badge">
                  EVIDENCE ANALYSIS
                </span>

              </div>

              <div className="evidence-summary">

                <div className="evidence-stat">
                  <span>DOCUMENTS</span>

                  <strong>
                    {documents.length}
                  </strong>
                </div>

                <div className="evidence-stat">
                  <span>EXTRACTED CONTENT</span>

                  <strong>
                    {documents.length > 0
                      ? "AVAILABLE"
                      : "NOT AVAILABLE"}
                  </strong>
                </div>

                <div className="evidence-stat">
                  <span>ASSESSMENT EVIDENCE</span>

                  <strong>
                    {assessmentState.evidence
                      ? "PROVIDED"
                      : "MISSING"}
                  </strong>
                </div>

              </div>

            </section>


            {/* Assessment Evidence */}

            <section className="workflow-card">

              <div className="workflow-card-header">

                <div>
                  <h2>
                    Assessment Evidence
                  </h2>

                  <p>
                    Supporting information provided for the
                    proposed business change.
                  </p>
                </div>

              </div>

              <div className="assessment-evidence-text">

                {assessmentState.evidence || (
                  <span className="not-available">
                    No assessment evidence provided.
                  </span>
                )}

              </div>

            </section>


            {/* Evidence Files */}

            <section className="workflow-card">

              <div className="workflow-card-header">

                <div>
                  <h2>
                    Submitted Evidence Files
                  </h2>

                  <p>
                    Documents available for evidence analysis.
                  </p>
                </div>

                <span className="source-badge">
                  {documents.length} FILES
                </span>

              </div>

              <div className="evidence-file-list">

                {documents.length === 0 ? (

                  <div className="workflow-empty">
                    No source documents are attached to this
                    assessment.
                  </div>

                ) : (

                  documents.map((document) => (

                    <div
                      className="evidence-file"
                      key={document.id}
                    >

                      <div className="evidence-file-icon">
                        📄
                      </div>

                      <div className="evidence-file-info">

                        <strong>
                          {document.filename}
                        </strong>

                        <span>
                          {document.file_type.toUpperCase()}
                        </span>

                      </div>

                      <span className="parsed-badge">
                        PARSED
                      </span>

                      <div className="document-actions">

                        <button
                          type="button"
                          className="doc-action-button"
                          onClick={() =>
                            openDocumentInBrowser(
                              assessmentState.id,
                              document
                            )
                          }
                        >
                          View
                        </button>

                        <button
                          type="button"
                          className="doc-action-button"
                          onClick={() =>
                            downloadDocument(
                              assessmentState.id,
                              document
                            )
                          }
                        >
                          Download
                        </button>

                      </div>

                    </div>

                  ))

                )}

              </div>

            </section>

          </div>


          {/* Evidence Sidebar */}

          <aside className="evidence-sidebar">

            <section className="evidence-verification-card">

              <span className="verification-label">
                EVIDENCE VERIFICATION
              </span>

              <h3>
                Evidence Ready for Risk Analysis
              </h3>

              <p>
                The submitted evidence and extracted
                business information can now be used to
                calculate inherent risk.
              </p>

              <div className="verification-row">
                <span>Source documents</span>

                <strong>
                  {documents.length}
                </strong>
              </div>

              <div className="verification-row">
                <span>Business intelligence</span>

                <strong>
                  {intelligence
                    ? "Available"
                    : "Missing"}
                </strong>
              </div>

              <div className="verification-row">
                <span>Assessment evidence</span>

                <strong>
                  {assessmentState.evidence
                    ? "Available"
                    : "Missing"}
                </strong>
              </div>

            </section>


            {/* Regulatory evidence citations extracted from real intake data */}

            <section className="workflow-card">

              <div className="workflow-card-header">

                <div>
                  <h2>
                    Approved Regulatory Evidence Citations
                  </h2>

                  <p>
                    Regulatory considerations captured
                    during intake.
                  </p>
                </div>

              </div>

              {(intelligence?.regulatory_considerations?.length
                ? intelligence.regulatory_considerations
                : intelligence?.existing_controls ?? []
              ).length === 0 ? (

                <div className="workflow-empty">
                  No regulatory considerations captured yet.
                </div>

              ) : (

                <div className="evidence-citation-list">

                  {(intelligence?.regulatory_considerations?.length
                    ? intelligence.regulatory_considerations
                    : intelligence?.existing_controls ?? []
                  ).map((item, index) => (

                    <div
                      className="evidence-citation-card"
                      key={index}
                    >
                      <strong>
                        Consideration {index + 1}
                      </strong>

                      <p>
                        {item}
                      </p>
                    </div>

                  ))}

                </div>

              )}

            </section>


            <section className="workflow-card">

              <div className="workflow-card-header">

                <div>
                  <h2>
                    Evidence Controls
                  </h2>

                  <p>
                    Evidence quality indicators.
                  </p>
                </div>

              </div>

              <div className="evidence-check-list">

                <div className="evidence-check">
                  <span className="check-icon">
                    ✓
                  </span>

                  <span>
                    Source document extracted
                  </span>
                </div>

                <div className="evidence-check">
                  <span className="check-icon">
                    ✓
                  </span>

                  <span>
                    Business intelligence available
                  </span>
                </div>

                <div className="evidence-check">
                  <span className="check-icon">
                    {assessmentState.evidence
                      ? "✓"
                      : "!"}
                  </span>

                  <span>
                    Assessment evidence provided
                  </span>
                </div>

              </div>

            </section>

          </aside>

        </div>

      ) :  currentStep === 3 ? (
  <div className="risk-layout">

    {/* LEFT — RISK INDICATORS */}

    <div className="risk-main">

      <section className="workflow-card">

        <div className="workflow-card-header">

          <div>
            <h2>
              Active Risk Indicator Map
            </h2>

            <p>
              Deterministic risk indicators identified
              from the assessment evidence and business
              intelligence.
            </p>
          </div>

          <span className="risk-engine-badge">
            DETERMINISTIC ANALYSIS
          </span>

        </div>

        {riskResults.length === 0 ? (

          <div className="risk-empty">
            <h3>
              Risk analysis has not been run
            </h3>

            <p>
              Analyze this assessment to generate
              inherent risk results.
            </p>
          </div>

        ) : (

          <div className="risk-result-list">

            {riskResults.map((result) => (

              <div
                className="risk-result-card"
                key={result.id}
              >

                <div className="risk-result-header">

                  <div>
                    <h3>
                      {result.dimension}
                    </h3>

                    <span>
                      Inherent Risk
                    </span>
                  </div>

                  <div
                    className={`risk-score-badge ${result.severity.toLowerCase()}`}
                  >
                    {result.severity}
                  </div>

                </div>

                <div className="risk-score-row">

                  <div className="risk-progress">

                    <div
                      className={`risk-progress-fill ${result.severity.toLowerCase()}`}
                      style={{
                        width: `${Math.min(
                          result.score,
                          100
                        )}%`,
                      }}
                    />

                  </div>

                  <strong>
                    {result.score}
                  </strong>

                </div>

                <p className="risk-reason">
                  {result.reason}
                </p>

              </div>

            ))}

          </div>

        )}

      </section>

    </div>


    {/* RIGHT — SUMMARY */}

    <aside className="risk-sidebar">

      <section className="risk-summary-card">

        <span className="risk-summary-label">
          OVERALL INHERENT RISK
        </span>

        <div className="overall-risk-score">
          {assessmentState.overall_score ?? "—"}
        </div>

        <div
          className={`overall-risk-level ${
            assessmentState.risk_level?.toLowerCase() || ""
          }`}
        >
          {assessmentState.risk_level ||
            "NOT ANALYZED"}
        </div>

        <p>
          Weighted deterministic score across
          applicable risk dimensions.
        </p>

      </section>


      <section className="workflow-card">

        <div className="workflow-card-header">

          <div>
            <h2>
              Risk Dimensions
            </h2>

            <p>
              Current inherent risk by dimension.
            </p>
          </div>

        </div>

        <div className="risk-dimension-list">

          {riskResults.map((result) => (

            <div
              className="risk-dimension"
              key={result.id}
            >

              <span>
                {result.dimension}
              </span>

              <strong>
                {result.score}
              </strong>

            </div>

          ))}

        </div>

      </section>


      <section className="risk-warning-card">

        <span>
          RISK GOVERNANCE
        </span>

        <h3>
          Human review required
        </h3>

        <p>
          Deterministic calculations identify
          inherent risk. Final decisions remain
          subject to human review and challenge.
        </p>

      </section>

    </aside>

  </div>
) : currentStep === 4 ? (
  <ControlsStage
    assessment={assessmentState}
    riskResults={riskResults}
  />
) : currentStep === 5 ? (
  <ResidualRiskStage
    assessment={assessmentState}
    riskResults={riskResults}
  />
) : currentStep === 6 ? (
  <FcrmReviewStage
    assessment={assessmentState}
    riskResults={riskResults}
    onSubmitForReview={handleSubmitForCommitteeReview}
    submitLoading={submitReviewLoading}
    submitError={submitReviewError}
  />
) : currentStep === 7 ? (
  <ChallengeStage
    assessment={assessmentState}
    riskResults={riskResults}
    documents={documents}
    intelligence={intelligence}
    auditEvents={auditEvents}
    onDecision={handleStatusChange}
  />
) : currentStep === 8 ? (
  <DecisionStage
    assessment={assessmentState}
    riskResults={riskResults}
    documents={documents}
    auditEvents={auditEvents}
    onDecision={handleStatusChange}
  />
) :  (
  <div className="workflow-placeholder">
    <h2>
      {workflowSteps[currentStep - 1]}
    </h2>

    <p>
      This assessment stage will be implemented next.
    </p>
  </div>
)}

    </div>
  );
}


interface MetadataRowProps {
  label: string;
  values: string[];
}

function MetadataRow({
  label,
  values,
}: MetadataRowProps) {
  return (
    <div className="metadata-row">

      <div className="metadata-label">

        <strong>
          {label}
        </strong>

        <span>
          Extracted from assessment intelligence
        </span>

      </div>

      <div className="metadata-values">

        {values.length === 0 ? (

          <span className="not-available">
            Not available
          </span>

        ) : (

          values.map((value) => (

            <span
              className="metadata-tag"
              key={value}
            >
              {value}
            </span>

          ))

        )}

      </div>

    </div>
  );
}
interface ControlsStageProps {
  assessment: Assessment;
  riskResults: RiskResult[];
}

function ControlsStage({
  assessment,
  riskResults,
}: ControlsStageProps) {
  const controlReduction =
    calculateControlReduction(riskResults);

  const inherentRisk =
    assessment.overall_score ?? 0;

  const residualRisk = Math.max(
    inherentRisk - controlReduction,
    0
  );

  const residualRiskLevel =
    getRiskLevel(residualRisk);

  return (
    <div className="controls-layout">

      {/* LEFT SIDE */}

      <div className="controls-main">

        <section className="workflow-card">

          <div className="workflow-card-header">

            <div>
              <h2>
                Active Control Enforcement Mapping
              </h2>

              <p>
                Deterministic mappings between identified
                risks and applicable control safeguards.
              </p>
            </div>

            <span className="control-engine-badge">
              DETERMINISTIC MAPPING ACTIVE
            </span>

          </div>


          {riskResults.length === 0 ? (

            <div className="controls-empty">

              <h3>
                No risk results available
              </h3>

              <p>
                Run the risk analysis before evaluating
                control effectiveness.
              </p>

            </div>

          ) : (

            <div className="control-list">

              {riskResults.map((result) => {

                const mapping =
                  controlMappings[result.dimension] ??
                  {
                    name: `${result.dimension} Risk Control`,
                    adequacy: "Not assessed",
                    coverage: "Not mapped",
                    status: "NOT VALIDATED" as const,
                  };

                return (
                  <div
                    className="control-item"
                    key={result.id}
                  >

                    <div className="control-item-main">

                      <h3>
                        {mapping.name}
                      </h3>

                      <p>
                        Design Adequacy:{" "}
                        <strong>
                          {mapping.adequacy}
                        </strong>

                        {" • "}

                        {mapping.coverage}
                      </p>

                    </div>

                    <span
                      className={`control-status ${mapping.status
                        .toLowerCase()
                        .replace(/ /g, "-")}`}
                    >
                      {mapping.status}
                    </span>

                  </div>
                );
              })}

            </div>

          )}

        </section>


        {/* Control Governance */}

        <section className="workflow-card">

          <div className="workflow-card-header">

            <div>
              <h2>
                Control Coverage Summary
              </h2>

              <p>
                Summary of deterministic control
                effectiveness across risk dimensions.
              </p>
            </div>

          </div>

          <div className="control-summary-grid">

            <ControlSummary
              label="Effective"
              value={
                riskResults.filter(
                  (result) =>
                    controlMappings[
                      result.dimension
                    ]?.status === "EFFECTIVE"
                ).length
              }
            />

            <ControlSummary
              label="Minor Deviation"
              value={
                riskResults.filter(
                  (result) =>
                    controlMappings[
                      result.dimension
                    ]?.status === "MINOR DEVIATION"
                ).length
              }
            />

            <ControlSummary
              label="Not Validated"
              value={
                riskResults.filter(
                  (result) =>
                    controlMappings[
                      result.dimension
                    ]?.status === "NOT VALIDATED"
                ).length
              }
            />

          </div>

        </section>

      </div>


      {/* RIGHT SIDE */}

      <aside className="controls-sidebar">

        <section className="residual-risk-card">

          <h2>
            Deterministic Residual Risk Score
          </h2>


          <div className="residual-risk-content">

            <div className="residual-risk-row">

              <span>
                INHERENT RISK
              </span>

              <strong className="inherent-value">
                {inherentRisk.toFixed(1)}
              </strong>

            </div>


            <div className="residual-risk-row">

              <span>
                CONTROL REDUCTION FACTOR
              </span>

              <strong className="reduction-value">
                -{controlReduction.toFixed(1)}
              </strong>

            </div>


            <div className="residual-risk-result">

              <span>
                RESIDUAL RISK RESULT
              </span>

              <div>

                <strong>
                  {residualRisk.toFixed(1)}
                </strong>

                <span
                  className={`residual-level ${residualRiskLevel.toLowerCase()}`}
                >
                  {residualRiskLevel}
                </span>

              </div>

            </div>

          </div>

        </section>


        {/* Warning */}

        <section className="control-warning-card">

          <span>
            ⚠ CONTROL WARNING
          </span>

          <h3>
            Control gaps require attention
          </h3>

          <p>
            Controls marked as Minor Deviation or
            Not Validated may reduce the effectiveness
            of the overall risk treatment.
          </p>

        </section>


        {/* Governance */}

        <section className="workflow-card">

          <div className="workflow-card-header">

            <div>
              <h2>
                Control Governance
              </h2>

              <p>
                Deterministic control evaluation.
              </p>
            </div>

          </div>

          <div className="governance-list">

            <div>
              <span>
                Risk dimensions evaluated
              </span>

              <strong>
                {riskResults.length}
              </strong>
            </div>

            <div>
              <span>
                Control reduction
              </span>

              <strong>
                {controlReduction.toFixed(1)}
              </strong>
            </div>

            <div>
              <span>
                Residual risk
              </span>

              <strong>
                {residualRisk.toFixed(1)}
              </strong>
            </div>

          </div>

        </section>

      </aside>

    </div>
  );
}


interface ControlSummaryProps {
  label: string;
  value: number;
}

function ControlSummary({
  label,
  value,
}: ControlSummaryProps) {
  return (
    <div className="control-summary-item">

      <span>
        {label}
      </span>

      <strong>
        {value}
      </strong>

    </div>
  );
}
interface ResidualRiskStageProps {
  assessment: Assessment;
  riskResults: RiskResult[];
}

function ResidualRiskStage({
  assessment,
  riskResults,
}: ResidualRiskStageProps) {
  const controlReduction =
    calculateControlReduction(riskResults);

  const inherentRisk =
    assessment.overall_score ?? 0;

  const residualRisk = Math.max(
    inherentRisk - controlReduction,
    0
  );

  const residualRiskLevel =
    getRiskLevel(residualRisk);

  const riskMovement =
    inherentRisk > 0
      ? Math.round(
          (controlReduction / inherentRisk) * 100
        )
      : 0;

  return (
    <div className="residual-layout">

      {/* LEFT SIDE */}

      <div className="residual-main">

        <section className="workflow-card">

          <div className="workflow-card-header">

            <div>
              <h2>
                Deterministic Residual Risk Assessment
              </h2>

              <p>
                Residual risk after applying the
                identified control effectiveness.
              </p>
            </div>

            <span className="control-engine-badge">
              DETERMINISTIC CALCULATION
            </span>

          </div>


          {/* Risk Flow */}

          <div className="risk-flow">

            <div className="risk-flow-item">

              <span>
                INHERENT RISK
              </span>

              <strong>
                {inherentRisk.toFixed(1)}
              </strong>

              <small>
                {getRiskLevel(inherentRisk)}
              </small>

            </div>


            <div className="risk-flow-arrow">
              →
            </div>


            <div className="risk-flow-item reduction">

              <span>
                CONTROL REDUCTION
              </span>

              <strong>
                -{controlReduction.toFixed(1)}
              </strong>

              <small>
                {riskMovement}% reduction
              </small>

            </div>


            <div className="risk-flow-arrow">
              →
            </div>


            <div className="risk-flow-item residual">

              <span>
                RESIDUAL RISK
              </span>

              <strong>
                {residualRisk.toFixed(1)}
              </strong>

              <small>
                {residualRiskLevel}
              </small>

            </div>

          </div>

        </section>


        {/* Dimension Analysis */}

        <section className="workflow-card">

          <div className="workflow-card-header">

            <div>
              <h2>
                Residual Risk by Dimension
              </h2>

              <p>
                Remaining exposure after applying
                mapped controls.
              </p>
            </div>

          </div>


          <div className="residual-dimension-list">

            {riskResults.map((result) => {

              const mapping =
                controlMappings[result.dimension];

              let reduction = 0;

              if (
                mapping?.status ===
                "EFFECTIVE"
              ) {
                reduction = 4;
              } else if (
                mapping?.status ===
                "MINOR DEVIATION"
              ) {
                reduction = 2;
              }

              const dimensionResidual =
                Math.max(
                  result.score - reduction,
                  0
                );

              const dimensionLevel =
                getRiskLevel(
                  dimensionResidual
                );

              return (
                <div
                  className="residual-dimension-row"
                  key={result.id}
                >

                  <div className="dimension-name">

                    <strong>
                      {result.dimension}
                    </strong>

                    <span>
                      {mapping?.name ||
                        "Control mapping"}
                    </span>

                  </div>


                  <div className="dimension-score">

                    <div className="dimension-score-values">

                      <span>
                        {result.score.toFixed(1)}
                      </span>

                      <span className="dimension-arrow">
                        →
                      </span>

                      <strong>
                        {dimensionResidual.toFixed(1)}
                      </strong>

                    </div>

                    <span
                      className={`dimension-level ${dimensionLevel.toLowerCase()}`}
                    >
                      {dimensionLevel}
                    </span>

                  </div>

                </div>
              );
            })}

          </div>

        </section>


        {/* Treatment */}

        <section className="workflow-card">

          <div className="workflow-card-header">

            <div>
              <h2>
                Risk Treatment Recommendation
              </h2>

              <p>
                Deterministic recommendation based on
                remaining residual exposure.
              </p>
            </div>

          </div>


          <div className="treatment-content">

            {residualRisk >= 80 ? (

              <div className="treatment critical">
                <strong>
                  Additional controls required
                </strong>

                <p>
                  Residual exposure remains critical.
                  Additional mitigation should be
                  implemented before approval.
                </p>
              </div>

            ) : residualRisk >= 60 ? (

              <div className="treatment high">
                <strong>
                  Enhanced monitoring recommended
                </strong>

                <p>
                  Residual risk remains high after
                  current controls. Additional
                  monitoring and targeted remediation
                  should be considered.
                </p>
              </div>

            ) : residualRisk >= 30 ? (

              <div className="treatment medium">
                <strong>
                  Standard risk monitoring
                </strong>

                <p>
                  Residual risk is moderate. Existing
                  controls should remain subject to
                  periodic review.
                </p>
              </div>

            ) : (

              <div className="treatment low">
                <strong>
                  Existing controls sufficient
                </strong>

                <p>
                  Current control coverage provides a
                  sufficient reduction of identified
                  inherent risk.
                </p>
              </div>

            )}

          </div>

        </section>

      </div>


      {/* RIGHT SIDE */}

      <aside className="residual-sidebar">

        <section className="residual-result-card">

          <span>
            RESIDUAL RISK RESULT
          </span>

          <strong>
            {residualRisk.toFixed(1)}
          </strong>

          <div
            className={`residual-result-level ${residualRiskLevel.toLowerCase()}`}
          >
            {residualRiskLevel}
          </div>

          <p>
            Deterministic residual risk after
            control treatment.
          </p>

        </section>


        <section className="workflow-card">

          <div className="workflow-card-header">

            <div>
              <h2>
                Risk Movement
              </h2>

              <p>
                Impact of current controls.
              </p>
            </div>

          </div>


          <div className="risk-movement-content">

            <div className="movement-row">
              <span>
                Inherent Risk
              </span>

              <strong>
                {inherentRisk.toFixed(1)}
              </strong>
            </div>

            <div className="movement-row">
              <span>
                Control Reduction
              </span>

              <strong className="reduction-text">
                -{controlReduction.toFixed(1)}
              </strong>
            </div>

            <div className="movement-row final">
              <span>
                Residual Risk
              </span>

              <strong>
                {residualRisk.toFixed(1)}
              </strong>
            </div>

          </div>

        </section>


        <section className="residual-governance-card">

          <span>
            GOVERNANCE
          </span>

          <h3>
            Human review required
          </h3>

          <p>
            Residual risk is calculated
            deterministically. The final risk
            treatment and approval decision remain
            subject to FCRM review.
          </p>

        </section>

      </aside>

    </div>
  );
}
interface FcrmReviewStageProps {
  assessment: Assessment;
  riskResults: RiskResult[];
  onSubmitForReview: () => Promise<void>;
  submitLoading: boolean;
  submitError: string | null;
}

function FcrmReviewStage({
  assessment,
  riskResults,
  onSubmitForReview,
  submitLoading,
  submitError,
}: FcrmReviewStageProps) {
  const inherentRisk = assessment.overall_score ?? 0;

  const controlReduction =
    calculateControlReduction(riskResults);

  const residualRisk = Math.max(
    inherentRisk - controlReduction,
    0
  );

  const suggestedRating = getRiskLevel(residualRisk);

  // Per-dimension human override, seeded from the real (deterministic) severity per risk result.
  const [humanRatings, setHumanRatings] = useState<
    Record<number, string>
  >(() => {
    const initial: Record<number, string> = {};
    riskResults.forEach((result) => {
      initial[result.id] = result.severity;
    });
    return initial;
  });

  useEffect(() => {
    setHumanRatings((prev) => {
      const next = { ...prev };
      riskResults.forEach((result) => {
        if (!(result.id in next)) {
          next[result.id] = result.severity;
        }
      });
      return next;
    });
  }, [riskResults]);

  const [justification, setJustification] =
    useState("");

  // Load any previously saved FCRM review for this assessment. Runs once
  // per assessment id — a saved human rating overrides the value seeded
  // from the risk result above.
  useEffect(() => {
    let cancelled = false;

    getFcrmReview(assessment.id)
      .then((review) => {
        if (cancelled) {
          return;
        }

        setJustification(review.justification || "");

        if (Object.keys(review.human_ratings).length > 0) {
          setHumanRatings((prev) => ({
            ...prev,
            ...review.human_ratings,
          }));
        }
      })
      .catch((error) => {
        console.error("Failed to load saved FCRM review", error);
      });

    return () => {
      cancelled = true;
    };
  }, [assessment.id]);

  const [saveLoading, setSaveLoading] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedJustSaved, setSavedJustSaved] = useState(false);

  // Any further edits after a successful save should clear the
  // "Saved" confirmation so it doesn't linger and look stale.
  useEffect(() => {
    setSavedJustSaved(false);
  }, [justification, humanRatings]);

  async function handleSaveFcrmReview() {
    setSaveLoading(true);
    setSaveError(null);
    setSavedJustSaved(false);

    try {
      await saveFcrmReview(assessment.id, {
        justification,
        human_ratings: humanRatings,
      });
      setSavedJustSaved(true);
    } catch (error) {
      setSaveError(
        error instanceof Error
          ? error.message
          : "Failed to save FCRM review"
      );
    } finally {
      setSaveLoading(false);
    }
  }

  const disagreements = riskResults.filter(
    (result) =>
      (humanRatings[result.id] ?? result.severity) !==
      result.severity
  );

  const hasDisagreement = disagreements.length > 0;

  const findings: {
    key: string;
    title: string;
    detail: string;
    resolved: boolean;
  }[] = [
    ...riskResults
      .filter(
        (result) =>
          controlMappings[result.dimension]?.status ===
          "NOT VALIDATED"
      )
      .map((result) => ({
        key: `not-validated-${result.id}`,
        title: `${
          controlMappings[result.dimension]?.name ||
          result.dimension
        } not validated`,
        detail: `${result.dimension} risk relies on a control that has not been independently validated.`,
        resolved: false,
      })),
    ...riskResults
      .filter(
        (result) =>
          controlMappings[result.dimension]?.status ===
          "MINOR DEVIATION"
      )
      .map((result) => ({
        key: `deviation-${result.id}`,
        title: `${
          controlMappings[result.dimension]?.name ||
          result.dimension
        } — minor deviation`,
        detail:
          "Design adequacy for this control is rated Medium and should be reviewed before disposition.",
        resolved: false,
      })),
  ];

  if (findings.length === 0 && riskResults.length > 0) {
    findings.push({
      key: "clear",
      title: "No open control findings",
      detail: "All mapped controls are currently rated Effective.",
      resolved: true,
    });
  }

  return (
    <div className="fcrm-layout">

      {/* LEFT */}

      <div className="fcrm-main">

        <section className="workflow-card">

          <div className="workflow-card-header">

            <div>
              <h2>
                FCRM Rating Reconciliation Ledger
              </h2>

              <p>
                Compare the system risk recommendation
                with the human FCRM assessment, per risk
                dimension.
              </p>
            </div>

            <span className="fcrm-badge">
              HUMAN GOVERNANCE
            </span>

          </div>

          {riskResults.length === 0 ? (

            <div className="workflow-empty">
              Run inherent risk analysis before reconciling
              FCRM ratings.
            </div>

          ) : (

            <div className="fcrm-dimension-list">

              {riskResults.map((result) => {

                const humanValue =
                  humanRatings[result.id] ?? result.severity;

                const disagrees =
                  humanValue !== result.severity;

                return (
                  <div
                    className={`fcrm-dimension-row ${
                      disagrees ? "disagreement" : ""
                    }`}
                    key={result.id}
                  >

                    <div className="fcrm-dimension-info">
                      <strong>
                        {result.dimension}
                      </strong>

                      <span>
                        {result.reason}
                      </span>
                    </div>

                    <div className="fcrm-dimension-compare">

                      <div className="fcrm-ai-value">
                        <span>
                          AI Suggestion
                        </span>

                        <strong>
                          {result.severity} ({result.score.toFixed(1)})
                        </strong>
                      </div>

                      <select
                        value={humanValue}
                        onChange={(event) =>
                          setHumanRatings((prev) => ({
                            ...prev,
                            [result.id]: event.target.value,
                          }))
                        }
                      >
                        <option value="LOW">LOW</option>
                        <option value="MEDIUM">MEDIUM</option>
                        <option value="HIGH">HIGH</option>
                        <option value="CRITICAL">CRITICAL</option>
                      </select>

                    </div>

                  </div>
                );
              })}

            </div>

          )}


          {/* Disagreement */}

          {riskResults.length > 0 && (
            hasDisagreement ? (

              <div className="disagreement-banner">

                <div className="disagreement-icon">
                  !
                </div>

                <div>
                  <strong>
                    Rating Disagreement Detected
                  </strong>

                  <p>
                    {disagreements.length} of {riskResults.length}{" "}
                    dimensions differ from the system
                    recommendation. A justification is
                    required before continuing.
                  </p>
                </div>

              </div>

            ) : (

              <div className="agreement-banner">

                <div className="agreement-icon">
                  ✓
                </div>

                <div>
                  <strong>
                    Rating Reconciled
                  </strong>

                  <p>
                    The FCRM rating currently agrees with
                    the system recommendation across all
                    dimensions.
                  </p>
                </div>

              </div>

            )
          )}

        </section>


        {/* Justification */}

        <section className="workflow-card">

          <div className="workflow-card-header">

            <div>
              <h2>
                Human Justification & Override Reasoning
              </h2>

              <p>
                Document the rationale supporting the
                FCRM assessment.
              </p>

            </div>

          </div>


          <div className="justification-content">

            <label>
              Reviewer justification
            </label>

            <textarea
              value={justification}
              onChange={(event) =>
                setJustification(event.target.value)
              }
              placeholder={
                hasDisagreement
                  ? "Explain why the FCRM rating differs from the system recommendation..."
                  : "Add reviewer comments or supporting rationale..."
              }
              rows={6}
            />

            <div className="justification-footer">

              <span>
                {justification.length} characters
              </span>

              {saveError && (
                <span className="save-review-error">
                  {saveError}
                </span>
              )}

              {savedJustSaved && !saveError && (
                <span className="save-review-success">
                  Saved
                </span>
              )}

              <button
                type="button"
                className="save-review-button"
                disabled={
                  saveLoading ||
                  (hasDisagreement &&
                    justification.trim().length === 0)
                }
                onClick={handleSaveFcrmReview}
              >
                {saveLoading ? "Saving…" : "Save FCRM Review"}
              </button>

            </div>

          </div>

        </section>


        {/* Challenge findings */}

        <section className="workflow-card">

          <div className="workflow-card-header">

            <div>
              <h2>
                Compliance Challenge Agent Findings
              </h2>

              <p>
                Findings generated from the current control
                mapping and risk results.
              </p>

            </div>

            <span className="challenge-badge">
              CHALLENGE ANALYSIS
            </span>

          </div>


          <div className="challenge-list">

            {findings.length === 0 ? (

              <div className="workflow-empty">
                No control findings available yet.
              </div>

            ) : (

              findings.map((finding) => (

                <div className="challenge-item" key={finding.key}>

                  <div
                    className={`challenge-icon ${
                      finding.resolved ? "success" : "warning"
                    }`}
                  >
                    {finding.resolved ? "✓" : "!"}
                  </div>

                  <div>
                    <strong>
                      {finding.title}
                    </strong>

                    <p>
                      {finding.detail}
                    </p>
                  </div>

                  <span
                    className={`finding-status ${
                      finding.resolved ? "resolved" : ""
                    }`}
                  >
                    {finding.resolved ? "CLEAR" : "REVIEW"}
                  </span>

                </div>

              ))

            )}

          </div>

        </section>

      </div>


      {/* RIGHT */}

      <aside className="fcrm-sidebar">

        <section className="fcrm-summary-card">

          <span>
            FCRM REVIEW STATUS
          </span>

          <strong>
            {hasDisagreement
              ? "CHALLENGE REQUIRED"
              : "READY FOR REVIEW"}
          </strong>

          <p>
            Human review remains authoritative over
            the final assessment disposition.
          </p>

          <button
            className="submit-review-button"
            type="button"
            disabled={
              submitLoading ||
              assessment.status !== "READY_FOR_REVIEW"
            }
            onClick={onSubmitForReview}
          >
            {submitLoading
              ? "Submitting…"
              : assessment.status !== "READY_FOR_REVIEW"
              ? `Assessment is ${assessment.status}`
              : "Submit for Committee Review → UNDER_REVIEW"}
          </button>

          {submitError && (
            <p className="submit-review-error">
              {submitError}
            </p>
          )}

        </section>


        <section className="workflow-card">

          <div className="workflow-card-header">

            <div>
              <h2>
                Risk Reconciliation
              </h2>

              <p>
                Current assessment values.
              </p>

            </div>

          </div>


          <div className="fcrm-metrics">

            <div>
              <span>
                Inherent Risk
              </span>

              <strong>
                {inherentRisk.toFixed(1)}
              </strong>
            </div>

            <div>
              <span>
                Control Reduction
              </span>

              <strong className="reduction-text">
                -{controlReduction.toFixed(1)}
              </strong>
            </div>

            <div>
              <span>
                Residual Risk
              </span>

              <strong>
                {residualRisk.toFixed(1)}
              </strong>
            </div>

            <div>
              <span>
                System Rating
              </span>

              <strong>
                {suggestedRating}
              </strong>
            </div>

            <div>
              <span>
                Dimensions Reconciled
              </span>

              <strong>
                {riskResults.length - disagreements.length} / {riskResults.length}
              </strong>
            </div>

          </div>

        </section>


        <section className="fcrm-governance-card">

          <span>
            GOVERNANCE PRINCIPLE
          </span>

          <h3>
            Human decision authority
          </h3>

          <p>
            Automated recommendations support the
            reviewer but do not independently approve,
            reject, or determine the final business
            disposition.
          </p>

        </section>

      </aside>

    </div>
  );
}

export default AssessmentWorkflow;