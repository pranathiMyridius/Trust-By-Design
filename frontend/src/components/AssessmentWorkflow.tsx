import { useEffect, useRef, useState } from "react";
import {
  ChallengeSignoffPanel,
  CommitteeReadinessPanel,
  ControlChangePanel,
  OverrideLedgerPanel,
} from "./GovernanceRecordPanels";

import {
  openDocumentFile,
  getAssessmentDocuments,
  getAssessmentIntelligence,
  getRiskResults,
  getAssessmentAudit,
  getFcrmReview,
  saveFcrmReview,
  getAssessmentChallenge,
  updateAssessmentChallenge,
  uploadAssessmentDocument,
  getEvidenceGaps,
  updateAssessmentIntelligence,
  confirmAssessmentIntelligence,
  getRiskFactors,
  addRiskFactor,
  excludeRiskFactor,
  rateRiskFactor,
  suggestRiskFactorRatings,
  getInherentRiskCalculation,
  overrideInherentRisk,
  getAssessmentDraft,
  generateAssessmentDraft,
  updateAssessmentDraft,
  acceptAssessmentDraft,
  type AssessmentDraft,
  getCommitteeConditions,
  updateCommitteeCondition,
  amendAssessment,
  type CommitteeConditionRecord,
  DOCUMENT_TYPES,
  CONFIDENTIALITY_LEVELS,
  RISK_CATEGORIES,
  RISK_INDICATORS,
  getOccRiskProfile,
  type OccRiskProfile,
  submitToManager,
  getAssessmentComments,
  postAssessmentComment,
  resolveAssessmentComment,
  COMMENT_SECTIONS,
  type AssessmentComment,
  requestAssessmentInformation,
  provideAssessmentInformation,
  getControlSummary,
  addControl,
  assessControl,
  listEvidenceLinks,
  updateControlCondition,
  CONTROL_LIBRARY,
  DESIGN_ADEQUACY_VALUES,
  OPERATING_EFFECTIVENESS_VALUES,
  type AssessmentControlSummary,
  type Control,
  type ControlAssessmentRecord,
  type ControlGap,
  type ControlCondition,
  getChallengeReview,
  resolveChallengeFinding,
  acceptChallengeFinding,
  type ChallengeReview,
  type ChallengeFindingRecord,
  getActionItems,
  createActionItem,
  updateActionItem,
  requestActionItemClosure,
  decideActionItemClosure,
  syncActionItems,
  ACTION_ITEM_SOURCE_TYPES,
  ACTION_ITEM_PRIORITIES,
  type ActionItem,
  getAssessmentExplanation,
  downloadAuditExport,
  type AssessmentExplanation,
  getAssessment,
  acknowledgeDegradedResult,
} from "../api/assessments";
import {
  analyzeAssessmentAsync,
  listProcessingJobs,
  retryProcessingJob,
  startStageAdvance,
  waitForProcessingJob,
  type ProcessingJob,
} from "../api/processing";
import StageMoveModal from "./StageMoveModal";
import StageTracker from "./StageTracker";
import {
  DECIDED_STATUSES,
  MANAGER_PHASE_STATUSES,
  STAGE_STEPS,
  TRACKED_STAGES,
  stageIndexForStep,
} from "./stageTrackerStages";
import WorkflowPanel from "./WorkflowPanel";
import { RiskLevelIcon } from "./RiskLevelBadge";
import SectionNavigator from "./SectionNavigator";
import ProcessingStatus, { DocumentProcessingBadge } from "./ProcessingStatus";
import ExplainabilityPanel from "./ExplainabilityPanel";
import InherentRiskHistory from "./InherentRiskHistory";
import DraftVersionHistory from "./DraftVersionHistory";
import ControlConditionForm from "./ControlConditionForm";
import FactorSourceEvidencePanel from "./FactorSourceEvidencePanel";
import { RecommendedConditionsPanel, ResidualConfirmationPanel } from "./ResidualReviewPanels";

// R7.3: a control's operating status.
const CONTROL_OPERATING_STATUSES = ["ACTIVE", "PARTIALLY_IMPLEMENTED", "PLANNED", "INACTIVE"];
import { EvidenceStatusBadge, FactorEvidence } from "./FactorEvidence";
import {
  CalculationProvenance,
  DecisionReadinessPanel,
  DecisionRecordPanel,
  ResidualGridPanel,
} from "./GovernancePanels";
import { formatResidualScore, useResidualRisk } from "./governanceHooks";
import { friendlyError } from "../utils/errorMessages";
import RetentionHoldPanel from "./RetentionHoldPanel";
import ManagerDecisionPanel from "./ManagerDecisionPanel";
import ApprovalStage from "./ApprovalStage";
import ControlEvidencePanel from "./ControlEvidencePanel";
import AIChallengePanel from "./AIChallengePanel";
import RatingsPanel from "./RatingsPanel";
import ReturnedByManagerPanel, { ManagerFeedbackBanner } from "./ReturnedByManagerPanel";
import {
  ExpiredEvidenceDecision,
  FactorIndicatorEditor,
  FactorRuleTriggers,
  IntakeHistoryPanel,
  ProfileProvenanceTable,
} from "./TraceabilityPanels";
import ReassessmentPanel from "./ReassessmentPanel";

import type {
  Assessment,
  AssessmentDocument,
  AssessmentIntelligence,
  RiskResult,
  RiskFactor,
  InherentRiskCalculation,
  AuditEvent,
  EvidenceGaps,
} from "../api/assessments";


interface AssessmentWorkflowProps {
  assessment: Assessment;
  onBack: () => void;
  user: import("../api/auth").CurrentUser;
  /** R1.3: reopen this (draft) assessment in the intake form. */
  onEditDraft?: (assessment: Assessment) => void;
}

// The pipeline still has 7 internal steps (one per status gate); the tracker
// groups them into TRACKED_STAGES, and these are the names the page shows.
const TOTAL_STEPS = 7;
function stepLabel(step: number): string {
  return TRACKED_STAGES[stageIndexForStep(step)]?.label ?? "";
}
// Stage 7: a control's current assessment (if any) collapses to one of
// these display statuses, driven by real per-control data from
// getControlSummary() instead of a hardcoded per-dimension guess.
type ControlDisplayStatus =
  | "EFFECTIVE"
  | "PARTIALLY_EFFECTIVE"
  | "INEFFECTIVE"
  | "UNVERIFIED"
  | "NOT_ASSESSED";

function controlDisplayStatus(
  assessmentRecord: ControlAssessmentRecord | undefined
): ControlDisplayStatus {
  if (!assessmentRecord) {
    return "NOT_ASSESSED";
  }

  // R7.6/AC3: no evidence, incomplete coverage, or an unavailable-data
  // dependency all cap the displayed status below EFFECTIVE, whatever the
  // raw operating_effectiveness value says.
  if (
    !assessmentRecord.has_evidence ||
    !assessmentRecord.coverage_complete ||
    assessmentRecord.depends_on_unavailable_data
  ) {
    return assessmentRecord.operating_effectiveness === "EFFECTIVE"
      ? "PARTIALLY_EFFECTIVE"
      : (assessmentRecord.operating_effectiveness as ControlDisplayStatus) ||
          "UNVERIFIED";
  }

  return assessmentRecord.operating_effectiveness as ControlDisplayStatus;
}

function controlStatusLabel(status: ControlDisplayStatus): string {
  switch (status) {
    case "EFFECTIVE":
      return "Effective";
    case "PARTIALLY_EFFECTIVE":
      return "Partially Effective";
    case "INEFFECTIVE":
      return "Ineffective";
    case "UNVERIFIED":
      return "Unverified";
    default:
      return "Not Assessed";
  }
}

function controlTypeLabel(controlType: string): string {
  return (
    CONTROL_LIBRARY.find((entry) => entry.value === controlType)?.label ??
    controlType
  );
}

function riskCategoryLabel(category: string): string {
  return (
    RISK_CATEGORIES.find((entry) => entry.value === category)?.label ??
    category
  );
}

function gapTypeLabel(gapType: string): string {
  switch (gapType) {
    case "NO_CONTROL":
      return "No control mapped";
    case "NO_EVIDENCE":
      return "No supporting evidence";
    case "INEFFECTIVE":
      return "Ineffective control";
    case "PARTIAL":
      return "Partially effective control";
    case "INCOMPLETE_COVERAGE":
      return "Does not cover full risk";
    case "UNAVAILABLE_DATA_DEPENDENCY":
      return "Depends on unavailable data";
    default:
      return gapType;
  }
}


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
 * Backend: GET /api/assessments/documents/{document_id}/file returns the
 * original uploaded file. Stage 19: that endpoint now requires the bearer
 * token (and may refuse a RESTRICTED document), so files are fetched via
 * openDocumentFile() rather than a plain URL.
 */
function reportDocumentError(err: unknown) {
  window.alert(friendlyError(err, "The document could not be opened."));
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
  _assessmentId: number,
  doc: AssessmentDocument
) {
  openDocumentFile(doc.id, doc.filename, "download").catch(reportDocumentError);
}

function openDocumentInBrowser(
  _assessmentId: number,
  doc: AssessmentDocument
) {
  // R15.3: a classified original the viewer may not open is previewed
  // as its (masked) extracted text instead.
  if (isBrowserViewable(doc) && doc.can_open_original !== false) {
    openDocumentFile(doc.id, doc.filename, "view").catch(reportDocumentError);
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

function getRiskLevel(score: number) {
  if (score >= 80) {
    return "CRITICAL";
  }

  if (score >= 60) {
    return "HIGH";
  }

  // Default methodology bands: LOW 0-39, MEDIUM 40-59, HIGH 60-79, CRITICAL 80+.
  if (score >= 40) {
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
      parsed <= TOTAL_STEPS
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

// Maps the real backend pipeline status (see StageTracker.tsx /
// backend/app/api/assessments.py's STAGE_ORDER) onto this component's
// internal 8-step wizard. The wizard's step numbering predates the
// 9-stage server pipeline and combines RISK_IDENTIFICATION +
// INHERENT_RISK_ASSESSMENT into a single "Inherent Risk" display step
// (the same underlying risk_results/overall_score/risk_level are shown
// either way), and COMMITTEE_DECISION spans the "Challenge"/"Decision"
// pair. This function is the only source of truth for that mapping —
// nothing here is inferred from localStorage any more.
function inferStepFromStatus(status: string): number {
  switch (status) {
    case "INTAKE":
      return 1;
    case "EVIDENCE_COLLECTION":
      return 2;
    case "RISK_IDENTIFICATION":
    case "INHERENT_RISK_ASSESSMENT":
      return 3;
    case "CONTROL_ASSESSMENT":
      return 4;
    case "RESIDUAL_RISK":
      return 5;
    case "HUMAN_REVIEW":
    // Stage 10 (R10.5): a request for more information keeps the
    // assessment on the FCRM Review step while it's outstanding.
    case "INFORMATION_REQUESTED":
      return 6;
    // AW approval workflow (replaces the old COMMITTEE_DECISION step):
    // still in progress through the Manager/Committee chain, and the final
    // decisions: the same screen, with the committee stage decided.
    case "SUBMITTED_TO_MANAGER":
    case "RETURNED_BY_MANAGER":
    case "READY_FOR_COMMITTEE":
    case "COMMITTEE_REVIEW":
    case "DEFERRED":
    case "MANAGER_REJECTED":
    case "APPROVED":
    case "APPROVED_WITH_CONDITIONS":
    case "REJECTED":
    case "CLOSED":
      return 7;
    case "REMEDIATION":
      // Sent back for rework — the business owner returns to Intake.
      return 1;
    default:
      return 1;
  }
}

/* =========================================
   STEP 7/8 — AW APPROVAL WORKFLOW STATUS
   Read-only view of where this assessment stands in the Business User ->
   Manager -> Committee chain, plus the visibility-filtered comment
   thread. Manager/Committee decisions themselves are made from the
   Approvals page (see components/ApprovalsPage.tsx), not here -- this is
   the owning Business User's (and anyone else with view access's) window
   into that process.
   ========================================= */

const APPROVAL_STATUS_LABELS: Record<string, string> = {
  INFORMATION_REQUESTED: "Information Requested",
  SUBMITTED_TO_MANAGER: "Submitted to Manager",
  RETURNED_BY_MANAGER: "Returned by Manager",
  MANAGER_REJECTED: "Manager Rejected",
  READY_FOR_COMMITTEE: "Ready for Committee",
  COMMITTEE_REVIEW: "Committee Review",
  DEFERRED: "Deferred by Committee",
  CLOSED: "Closed",
  APPROVED: "Approved",
  APPROVED_WITH_CONDITIONS: "Approved with Conditions",
  REJECTED: "Rejected",
};

function ApprovalStatusStage({
  assessment,
  auditEvents,
  canAmend,
  currentUserEmail,
  currentUserRole,
  onAmended,
}: {
  assessment: Assessment;
  auditEvents: AuditEvent[];
  canAmend?: boolean;
  currentUserEmail?: string;
  currentUserRole?: string;
  onAmended?: (updated: Assessment) => void;
}) {
  const [comments, setComments] = useState<AssessmentComment[]>([]);
  const [commentsLoading, setCommentsLoading] = useState(true);
  const [newComment, setNewComment] = useState("");
  const [postingComment, setPostingComment] = useState(false);
  const [commentError, setCommentError] = useState<string | null>(null);

  // Stage 12 (R12.4/read-only): structured committee conditions tracked
  // to completion, and the controlled-amendment reopen path.
  const [committeeConditions, setCommitteeConditions] = useState<
    CommitteeConditionRecord[]
  >([]);
  const [amending, setAmending] = useState(false);
  const [amendReason, setAmendReason] = useState("");
  const [amendLoading, setAmendLoading] = useState(false);
  const [amendError, setAmendError] = useState<string | null>(null);

  const isFinalDecision = [
    "APPROVED",
    "APPROVED_WITH_CONDITIONS",
    "REJECTED",
    "MANAGER_REJECTED",
  ].includes(assessment.status);

  useEffect(() => {
    getCommitteeConditions(assessment.id).then(setCommitteeConditions).catch(() => setCommitteeConditions([]));
  }, [assessment.id, assessment.status]);

  async function handleUpdateConditionStatus(conditionId: number, status: string) {
    try {
      const updated = await updateCommitteeCondition(assessment.id, conditionId, { status });
      setCommitteeConditions((current) =>
        current.map((c) => (c.id === conditionId ? updated : c))
      );
    } catch (error) {
      console.error(error);
    }
  }

  async function handleAmend() {
    if (!amendReason.trim()) {
      setAmendError("A reason is required to open a controlled amendment.");
      return;
    }
    setAmendLoading(true);
    setAmendError(null);
    try {
      const updated = await amendAssessment(assessment.id, amendReason, currentUserEmail);
      onAmended?.(updated);
      setAmending(false);
      setAmendReason("");
    } catch (error) {
      setAmendError(error instanceof Error ? error.message : "Failed to open amendment");
    } finally {
      setAmendLoading(false);
    }
  }

  useEffect(() => {
    let cancelled = false;

    setCommentsLoading(true);
    getAssessmentComments(assessment.id)
      .then((data) => {
        if (!cancelled) setComments(data);
      })
      .catch((error) => console.error(error))
      .finally(() => {
        if (!cancelled) setCommentsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [assessment.id, assessment.status]);

  async function handlePostComment() {
    if (!newComment.trim()) return;

    setPostingComment(true);
    setCommentError(null);

    try {
      const comment = await postAssessmentComment(assessment.id, newComment.trim());
      setComments((current) => [...current, comment]);
      setNewComment("");
    } catch (error) {
      setCommentError(error instanceof Error ? error.message : "Failed to post comment");
    } finally {
      setPostingComment(false);
    }
  }

  const relevantAudit = auditEvents.filter((event) =>
    [
      "SUBMITTED_TO_MANAGER",
      "MANAGER_APPROVED",
      "MANAGER_RETURNED",
      "MANAGER_REJECTED",
      "COMMITTEE_APPROVED",
      "COMMITTEE_APPROVED_WITH_CONDITIONS",
      "COMMITTEE_DEFERRED",
      "COMMITTEE_REJECTED",
    ].includes(event.action)
  );

  return (
    <div className="workflow-placeholder">
      <h2>Approval Status</h2>

      <p>
        Current status:{" "}
        <strong>{APPROVAL_STATUS_LABELS[assessment.status] ?? assessment.status}</strong>
      </p>

      {assessment.manager_comment && (
        <p>
          <strong>Manager comment:</strong> {assessment.manager_comment}
        </p>
      )}

      {assessment.committee_rationale && (
        <p>
          <strong>Committee rationale:</strong> {assessment.committee_rationale}
        </p>
      )}

      {assessment.committee_conditions && (
        <p>
          <strong>Conditions:</strong> {assessment.committee_conditions}
        </p>
      )}

      {committeeConditions.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <h3>Conditions (Stage 12, tracked to completion)</h3>
          <table className="risk-table" style={{ width: "100%" }}>
            <thead>
              <tr>
                <th>Condition</th>
                <th>Owner</th>
                <th>Due</th>
                <th>Priority</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {committeeConditions.map((condition) => (
                <tr key={condition.id}>
                  <td>{condition.description}</td>
                  <td>{condition.owner}</td>
                  <td>{condition.due_date}</td>
                  <td>{condition.priority}</td>
                  <td>
                    {condition.status === "COMPLETED" ? (
                      "Completed"
                    ) : (
                      <>
                        <select aria-label="Condition status"
                          value={condition.status}
                          onChange={(event) =>
                            handleUpdateConditionStatus(condition.id, event.target.value)
                          }
                        >
                          <option value="OPEN">Open</option>
                          <option value="IN_PROGRESS">In Progress</option>
                          <option value="CANCELLED">Cancelled</option>
                        </select>
                        <div style={{ color: "#667085", fontSize: 12, marginTop: 4 }}>
                          Completed via its action item (evidence + reviewer approval).
                        </div>
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <ActionItemsPanel
        assessmentId={assessment.id}
        // Matches the backend: create/update/sync are FCRM Analyst,
        // Manager or Admin. Committee members decide closures instead
        // (canDecideClosure inside the panel).
        canManage={
          currentUserRole === "FCRM_ANALYST" ||
          currentUserRole === "MANAGER" ||
          currentUserRole === "ADMIN"
        }
        currentUserRole={currentUserRole}
      />

      {isFinalDecision && (
        <div style={{ marginTop: 16, border: "1px solid #e5e7eb", borderRadius: 6, padding: 12 }}>
          <p className="risk-reason">
            This assessment has a final decision and is read-only. The only way to change it
            is a controlled amendment.
          </p>
          {canAmend && (
            amending ? (
              <div className="form-group">
                <label>Reason for amendment (required)</label>
                <textarea aria-label="Reason for amendment (required)"
                  rows={2}
                  value={amendReason}
                  onChange={(event) => setAmendReason(event.target.value)}
                />
                {amendError && (
                  <p className="risk-reason" role="alert" style={{ color: "#b91c1c" }}>{amendError}</p>
                )}
                <div className="form-actions">
                  <button type="button" className="secondary-button" onClick={() => setAmending(false)} disabled={amendLoading}>
                    Cancel
                  </button>
                  <button type="button" className="primary-button" onClick={handleAmend} disabled={amendLoading}>
                    {amendLoading ? "Opening..." : "Open Amendment"}
                  </button>
                </div>
              </div>
            ) : (
              <button type="button" className="doc-action-button" onClick={() => setAmending(true)}>
                Open Controlled Amendment
              </button>
            )
          )}
        </div>
      )}

      <div style={{ marginTop: 24 }}>
        <h3>Timeline</h3>
        {relevantAudit.length === 0 ? (
          <p>No approval activity yet.</p>
        ) : (
          <ul>
            {relevantAudit.map((event) => (
              <li key={event.id}>
                <strong>{formatDateTime(event.created_at)}</strong> &mdash;{" "}
                {auditActionLabel(event)}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div style={{ marginTop: 24 }}>
        <h3>Comments</h3>
        {commentsLoading ? (
          <p>Loading...</p>
        ) : comments.length === 0 ? (
          <p>No comments yet.</p>
        ) : (
          <ul>
            {comments.map((comment) => (
              <li key={comment.id}>
                <strong>{comment.author_name ?? "Unknown"}</strong> ({comment.author_role}):{" "}
                {comment.body}
              </li>
            ))}
          </ul>
        )}

        <textarea aria-label="Add a comment"
          value={newComment}
          onChange={(event) => setNewComment(event.target.value)}
          placeholder="Add a comment..."
          rows={2}
          style={{ width: "100%", marginTop: 8 }}
        />
        {commentError && <p role="alert" style={{ color: "#b91c1c" }}>{commentError}</p>}
        <button
          className="primary-button"
          disabled={postingComment || !newComment.trim()}
          onClick={handlePostComment}
          style={{ marginTop: 8 }}
        >
          {postingComment ? "Posting..." : "Post Comment"}
        </button>
      </div>

      {/* P6: reassessment & change management moved to ReassessmentPanel (Decision step, and a reassessment's Intake). */}

      <div style={{ marginTop: 24 }}>
        <AuditCompliancePanel
          assessmentId={assessment.id}
          isFinalDecision={isFinalDecision}
          isAdmin={currentUserRole === "ADMIN"}
          canExport={currentUserRole === "MANAGER" || currentUserRole === "ADMIN" || currentUserRole === "AUDITOR"}
        />
      </div>
    </div>
  );
}

function AssessmentWorkflow({
  assessment,
  onBack,
  user,
  onEditDraft,
}: AssessmentWorkflowProps) {
  // AW stopgap: until a dedicated FCRM Analyst role exists, the backend
  // restricts the AI-analysis/scoring pipeline (analyze, risk-factor
  // edits, manual score override, challenge, FCRM review, and every
  // advance-stage transition from EVIDENCE_COLLECTION onward) to
  // Manager/Admin. Mirrored here so a Business User sees a clear
  // disabled state instead of a button that always 403s.
  const canRunPipeline =
    user.role === "FCRM_ANALYST" || user.role === "MANAGER" || user.role === "ADMIN";
  const [assessmentState, setAssessmentState] =
    useState<Assessment>(assessment);

  // The Business User owns their own intake and evidence collection:
  // completing intake (INTAKE -> EVIDENCE_COLLECTION), running risk
  // identification once evidence is in (EVIDENCE_COLLECTION ->
  // RISK_IDENTIFICATION), and acknowledging a remediation loop-back
  // (REMEDIATION -> INTAKE) are their own actions -- see
  // advance_assessment_stage's TRANSITIONS table in
  // backend/app/services/workflow.py.
  const isOwner = user.id === assessmentState.owner_id;
  const OWNER_ADVANCE_STATUSES = new Set(["INTAKE", "REMEDIATION", "EVIDENCE_COLLECTION"]);
  const canAdvanceStage = OWNER_ADVANCE_STATUSES.has(assessmentState.status)
    ? isOwner || canRunPipeline
    : canRunPipeline;

  const [intelligence, setIntelligence] =
    useState<AssessmentIntelligence | null>(null);

  const [documents, setDocuments] =
    useState<AssessmentDocument[]>([]);
    const [riskResults, setRiskResults] = useState<RiskResult[]>([]);
    const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([]);

  // Stage 4 (R4.1-R4.5): per-category risk-factor identification,
  // separate from riskResults' 6-dimension scoring.
  const [riskFactors, setRiskFactors] = useState<RiskFactor[]>([]);
  // A risk-result row mirrors the factor of the same category. Until an
  // analyst rates that factor its stored 0 / LOW is a placeholder, not a
  // finding, and must not be displayed as one.
  const unratedCategories = new Set(
    riskFactors
      .filter((factor) => factor.likelihood == null || factor.impact == null)
      .map((factor) => factor.category)
  );
  const isUnratedResult = (dimension: string) => unratedCategories.has(dimension);

  // Stage 6 (R6.1-R6.7): the deterministic inherent-risk calculation
  // (weights, thresholds, final score/band, provisional/escalation
  // flags) built from riskFactors' manual likelihood/impact ratings.
  // Null until risk identification has run at least once.
  const [inherentRiskCalc, setInherentRiskCalc] =
    useState<InherentRiskCalculation | null>(null);

  // R2.5: missing-evidence/information-request checklist for this
  // assessment, plus R2.6 document warnings (e.g. expired evidence).
  const [evidenceGaps, setEvidenceGaps] = useState<EvidenceGaps | null>(null);

  // Stage 7 (R7.1-R7.7): controls mapped to identified risks, their
  // design/effectiveness assessments, open gaps and conditions. Null
  // until Controls has loaded at least once.
  const [controlSummary, setControlSummary] =
    useState<AssessmentControlSummary | null>(null);

  const [loading, setLoading] = useState(true);
  // Stage 19: surfaced when the initial data load fails; loadAttempt
  // re-runs the load from the "Try again" button.
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadAttempt, setLoadAttempt] = useState(0);

// currentStep drives which internal panel is displayed; it can move
// backward freely so a reviewer can look at already-unlocked sections
// again, but it can never exceed maxStepReached (see below), which is
// derived from the assessment's REAL backend status rather than a client
// guess or free clicking — so the stepper can never be used to skip ahead
// of the assessment's actual server-side pipeline stage.
const [currentStep, setCurrentStepState] = useState<number>(() =>
  readSavedStep(assessment.id) ?? inferStepFromStatus(assessment.status)
);

// Stage 19: the current step's content, scanned by SectionNavigator.
const stepPanelRef = useRef<HTMLDivElement | null>(null);

// The furthest step the SERVER'S real status says this assessment has
// reached. Recomputed from assessmentState.status (see the effect below),
// never from localStorage — that is what makes "you cannot skip stages"
// hold in the UI too, not just in the backend.
const [maxStepReached, setMaxStepReachedState] = useState<number>(() =>
  inferStepFromStatus(assessment.status)
);

useEffect(() => {
  setMaxStepReachedState(inferStepFromStatus(assessmentState.status));
}, [assessmentState.status]);

function setCurrentStep(
  update: number | ((step: number) => number)
) {
  setCurrentStepState((prev) => {
    const requested =
      typeof update === "function"
        ? (update as (step: number) => number)(prev)
        : update;

    // Never allow navigating further than the server's real progress.
    const next = Math.min(requested, maxStepReached);

    saveStep(assessment.id, next);
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
  setMaxStepReachedState(inferStepFromStatus(assessment.status));
}, [assessment.id]);

// The parent opens an assessment with the copy it already has, then
// passes a freshly fetched one (same id). Adopt it when it is newer, so a
// re-opened assessment never shows a stale stage, score or status.
// (Adjusting state while rendering, React's pattern for props that
// change, rather than an effect.)
const [lastAssessmentProp, setLastAssessmentProp] = useState(assessment);
if (assessment !== lastAssessmentProp) {
  setLastAssessmentProp(assessment);
  if (
    assessment.id === assessmentState.id &&
    new Date(assessment.updated_at).getTime() > new Date(assessmentState.updated_at).getTime()
  ) {
    setAssessmentState(assessment);
  }
}

  // R2.1/R2.5/R2.3: reloads documents, evidence gaps, and intelligence
  // after an upload/correction/confirmation, without a full page
  // reload. Risk results/audit aren't included -- those already have
  // their own refresh paths elsewhere in this component.
  async function refreshEvidenceData() {
    try {
      const [assessmentDocuments, gaps, assessmentIntelligence] =
        await Promise.all([
          getAssessmentDocuments(assessment.id),
          getEvidenceGaps(assessment.id).catch(() => null),
          getAssessmentIntelligence(assessment.id),
        ]);
      setDocuments(assessmentDocuments);
      setEvidenceGaps(gaps);
      setIntelligence(assessmentIntelligence);
    } catch (error) {
      console.error(error);
    }
  }

  // Stage 4 (R4.1-R4.5): reloads risk factors after a manual
  // add/exclude, without needing a full re-analysis.
  async function refreshRiskFactors() {
    try {
      setRiskFactors(await getRiskFactors(assessment.id));
    } catch (error) {
      console.error(error);
    }
    await refreshInherentRiskCalc();
    // A rating, addition or exclusion recomputes the official overall
    // score/level server-side; keep the summary in step with it.
    try {
      setAssessmentState(await getAssessment(assessment.id));
    } catch (error) {
      console.error(error);
    }
  }

  // Stage 6: reloads the deterministic inherent-risk calculation after a
  // factor rating/add/exclude changes what it's computed from.
  async function refreshInherentRiskCalc() {
    try {
      setInherentRiskCalc(await getInherentRiskCalculation(assessment.id));
    } catch (error) {
      // No risk factors yet (e.g. before risk identification has ever
      // run) -- leave it null rather than surfacing an error.
      setInherentRiskCalc(null);
    }
  }

  // Stage 7: reloads the control summary (controls, assessments, gaps,
  // conditions, control_reduction) after any control-related edit.
  async function refreshControlSummary() {
    try {
      setControlSummary(await getControlSummary(assessment.id));
    } catch (error) {
      console.error(error);
      setControlSummary(null);
    }
  }

  useEffect(() => {
    async function loadIntakeData() {
      try {
        setLoading(true);

        const [
            assessmentIntelligence,
            assessmentDocuments,
            assessmentRiskResults,
            assessmentAuditEvents,
            gaps,
            assessmentRiskFactors,
            ] = await Promise.all([
            getAssessmentIntelligence(assessment.id),
            getAssessmentDocuments(assessment.id),
            getRiskResults(assessment.id),
            getAssessmentAudit(assessment.id),
            getEvidenceGaps(assessment.id).catch(() => null),
            getRiskFactors(assessment.id).catch(() => []),
            ]);
        setIntelligence(assessmentIntelligence);
        setDocuments(assessmentDocuments);
        setRiskResults(assessmentRiskResults);
        setAuditEvents(assessmentAuditEvents);
        setEvidenceGaps(gaps);
        setRiskFactors(assessmentRiskFactors);
        if (assessmentRiskFactors.length > 0) {
          await refreshInherentRiskCalc();
        }
        await refreshControlSummary();
        setLoadError(null);
      } catch (error) {
        console.error(error);
        // Stage 19: tell the reviewer instead of silently showing blanks.
        setLoadError(
          friendlyError(
            error,
            "Some of this assessment's information couldn't be loaded."
          )
        );
      } finally {
        setLoading(false);
      }
    }

    loadIntakeData();
  }, [assessment.id, loadAttempt]);

  // The one, explicit, server-validated way to move this assessment
  // forward exactly one AI-analysis pipeline stage, up through
  // HUMAN_REVIEW (see api/assessments.ts / backend/app/api/assessments.py's
  // advance-stage endpoint). From HUMAN_REVIEW onward, the AW approval
  // workflow takes over via handleSubmitToManager below.
  const [advanceLoading, setAdvanceLoading] = useState(false);
  const [advanceError, setAdvanceError] = useState<string | null>(null);

  // Every stage move runs as a background job whose per-step progress
  // StageMoveModal shows. The dialog can be closed while it runs
  // ("Continue in Background"); polling carries on here and the page
  // moves on when the job finishes.
  const [stageJob, setStageJob] = useState<ProcessingJob | null>(null);
  const [stageMoveFrom, setStageMoveFrom] = useState<string | null>(null);
  const [stageDialogOpen, setStageDialogOpen] = useState(false);
  const [stageMoveRunning, setStageMoveRunning] = useState(false);
  const [stageRetrying, setStageRetrying] = useState(false);
  const stageAbortRef = useRef<AbortController | null>(null);

  // How long the finished move stays on screen before the dialog closes.
  const STAGE_DIALOG_LINGER_MS = 900;

  /*
   * Resolves when the move succeeds, or fails in a way the dialog shows
   * (with Retry). Rejects when a stage gate refused it -- reported beside
   * the button, exactly as the synchronous call used to -- so callers'
   * own error handling still applies.
   */
  async function handleAdvanceStage() {
    if (!canAdvanceStage) {
      const message = "Only an FCRM Analyst, Manager, or Admin can advance the assessment pipeline.";
      setAdvanceError(message);
      throw new Error(message);
    }

    setAdvanceLoading(true);
    setAdvanceError(null);
    setStageJob(null);
    setStageMoveFrom(assessmentState.status);
    setStageDialogOpen(true);

    try {
      let job: ProcessingJob;
      try {
        job = await startStageAdvance(assessmentState.id);
      } catch (error) {
        setStageDialogOpen(false);
        reportRefusal(error);
        throw error;
      }
      await followStageAdvance(job);
    } finally {
      setAdvanceLoading(false);
    }
  }

  function reportRefusal(error: unknown) {
    setAdvanceError(
      friendlyError(error, "The assessment couldn't be moved to the next stage. Please try again.")
    );
    // A refused Evidence -> Risk Identification move still creates the
    // structured profile for the owner to confirm; show it now.
    void refreshEvidenceData();
  }

  async function followStageAdvance(job: ProcessingJob) {
    stageAbortRef.current?.abort();
    const controller = new AbortController();
    stageAbortRef.current = controller;

    setStageJob(job);
    if (job.from_status) setStageMoveFrom(job.from_status);
    setStageMoveRunning(true);
    try {
      const final = await waitForProcessingJob(job.id, setStageJob, { signal: controller.signal });
      if (controller.signal.aborted) return;

      if (final.status === "SUCCEEDED" || final.status === "PARTIAL") {
        await showAdvancedStage(await getAssessment(assessmentState.id));
        // Risk Identification ends on the AI prediction, which stays open
        // until the user dismisses it; other moves close themselves.
        if ((job.from_status ?? assessmentState.status) !== "EVIDENCE_COLLECTION") {
          window.setTimeout(() => setStageDialogOpen(false), STAGE_DIALOG_LINGER_MS);
        }
      } else if (final.refused) {
        setStageDialogOpen(false);
        const error = new Error(final.error_message ?? "This stage move was refused.");
        reportRefusal(error);
        throw error;
      } else if (final.status === "FAILED") {
        // Keep the dialog (reopened if it was closed) on the failure.
        setStageDialogOpen(true);
      }
    } finally {
      if (!controller.signal.aborted) setStageMoveRunning(false);
    }
  }

  async function handleRetryStageMove() {
    if (!stageJob) return;
    setStageRetrying(true);
    try {
      const job = await retryProcessingJob(stageJob.id);
      setStageRetrying(false);
      await followStageAdvance(job);
    } catch (error) {
      setStageJob((current) =>
        current
          ? { ...current, error_message: friendlyError(error, "The retry couldn't be started.") }
          : current
      );
    } finally {
      setStageRetrying(false);
    }
  }

  // Coming back to an assessment whose stage move is still going: pick it up.
  useEffect(() => {
    let cancelled = false;
    listProcessingJobs(assessmentState.id)
      .then((jobs) => {
        const active = jobs.find(
          (job) => (job.job_type === "STAGE_ADVANCE" || job.job_type === "RISK_IDENTIFICATION") && !job.is_finished
        );
        if (active && !cancelled) followStageAdvance(active).catch(() => undefined);
      })
      .catch(() => {
        // Non-fatal: the buttons still work.
      });
    return () => {
      cancelled = true;
      stageAbortRef.current?.abort();
    };
    // Only on opening an assessment, not on every status change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assessmentState.id]);

  // Reload everything a stage move can change and open the new stage's
  // page, once a stage move has finished.
  async function showAdvancedStage(updated: Assessment) {
    setAssessmentState(updated);

    const [freshRiskResults, freshAuditEvents, freshRiskFactors] =
      await Promise.all([
        getRiskResults(updated.id),
        getAssessmentAudit(updated.id),
        getRiskFactors(updated.id).catch(() => []),
      ]);
    setRiskResults(freshRiskResults);
    setAuditEvents(freshAuditEvents);
    await refreshControlSummary();
    setRiskFactors(freshRiskFactors);
    if (freshRiskFactors.length > 0) {
      await refreshInherentRiskCalc();
    }

    // Navigate the wizard to the new stage's page, keyed off the
    // SERVER'S actual returned status (updated.status) — never a blind
    // local increment. We set maxStepReached and currentStep directly
    // here instead of going through the setCurrentStep()
    // helper/maxStepReached effect below: those read maxStepReached
    // from this render's closure, which still holds the OLD value at
    // this point (the assessmentState.status effect that recomputes it
    // hasn't run yet), so routing a forward jump through that clamp
    // would silently cap it back to the previous step and leave the
    // view stuck on the old page even though the backend advanced.
    const nextStep = inferStepFromStatus(updated.status);
    setMaxStepReachedState(nextStep);
    setCurrentStepState(nextStep);
    saveStep(updated.id, nextStep);
  }

  // Re-runs risk identification from the Risk Identification step, e.g.
  // after the AI call timed out and every category fell back to "needs
  // manual review". Runs as a background job (analyze-async) so the page
  // stays usable; manually added factors are carried forward by the
  // backend, only the AI-sourced ones are superseded.
  const [rerunLoading, setRerunLoading] = useState(false);
  const [rerunProgress, setRerunProgress] = useState(0);
  const [rerunError, setRerunError] = useState<string | null>(null);

  async function handleRerunAnalysis() {
    if (!canRunPipeline) {
      setRerunError("Only an FCRM Analyst, Manager, or Admin can run risk analysis.");
      return;
    }

    setRerunLoading(true);
    setRerunProgress(0);
    setRerunError(null);

    try {
      const started = await analyzeAssessmentAsync(assessmentState.id);
      const job = await waitForProcessingJob(started.id, (update) =>
        setRerunProgress(update.progress)
      );

      if (job.status === "FAILED") {
        setRerunError(
          job.error_message ?? "The risk analysis failed. Please try again."
        );
      }

      const [updated, freshRiskResults, freshAuditEvents, freshRiskFactors] =
        await Promise.all([
          getAssessment(assessmentState.id),
          getRiskResults(assessmentState.id),
          getAssessmentAudit(assessmentState.id),
          getRiskFactors(assessmentState.id).catch(() => []),
        ]);
      setAssessmentState(updated);
      setRiskResults(freshRiskResults);
      setAuditEvents(freshAuditEvents);
      setRiskFactors(freshRiskFactors);
      if (freshRiskFactors.length > 0) {
        await refreshInherentRiskCalc();
      }
    } catch (error) {
      setRerunError(
        friendlyError(error, "The risk analysis couldn't be run. Please try again.")
      );
    } finally {
      setRerunLoading(false);
    }
  }

  // The AI fallback in backend/app/langgraph/nodes.py records every
  // category as not applicable with this rationale when the AI call fails
  // (timeout, bad key, provider error). Keep the two strings in sync.
  const aiAnalysisUnavailable =
    riskResults.length === 0 && assessmentState.assessment_mode === "unavailable";

  // Degraded-mode review (backend app/risk_engine/degraded.py): a
  // rules-only result cannot advance until a reviewer acknowledges it.
  const isDegradedResult =
    assessmentState.assessment_mode === "rules_only" ||
    assessmentState.assessment_mode === "unavailable";
  const needsDegradedAcknowledgement =
    assessmentState.assessment_mode === "rules_only" &&
    !!assessmentState.requires_human_review &&
    !assessmentState.degraded_acknowledged_at;
  const [acknowledgingDegraded, setAcknowledgingDegraded] = useState(false);
  const [acknowledgeError, setAcknowledgeError] = useState<string | null>(null);

  async function handleAcknowledgeDegraded() {
    setAcknowledgingDegraded(true);
    setAcknowledgeError(null);
    try {
      setAssessmentState(await acknowledgeDegradedResult(assessmentState.id));
    } catch (error) {
      setAcknowledgeError(
        friendlyError(error, "The provisional result couldn't be acknowledged. Please try again.")
      );
    } finally {
      setAcknowledgingDegraded(false);
    }
  }

  // Intake's "Complete Intake & Start Evidence Collection" button.
  // INTAKE -> EVIDENCE_COLLECTION. The backend rejects this (400) unless
  // title/description/evidence are all filled in — this is the new home
  // for what used to be /analyze's "This is still a draft" gate.
  async function handleConfirmIntake() {
    if (assessmentState.status !== "INTAKE") {
      setConfirmIntakeError(
        `Cannot complete intake: assessment is "${assessmentState.status}", not "INTAKE".`
      );
      return;
    }

    setConfirmIntakeLoading(true);
    setConfirmIntakeError(null);

    try {
      await handleAdvanceStage();
    } catch (error) {
      setConfirmIntakeError(
        friendlyError(error, "Intake couldn't be completed. Please try again.")
      );
    } finally {
      setConfirmIntakeLoading(false);
    }
  }

  // -----------------------------------------------------------------
  // R2.1/R2.2/R2.6: evidence document upload (with classification
  // metadata and optional versioning).
  // -----------------------------------------------------------------
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadDocumentType, setUploadDocumentType] = useState("OTHER");
  const [uploadOwner, setUploadOwner] = useState("");
  const [uploadSource, setUploadSource] = useState("");
  const [uploadEffectiveDate, setUploadEffectiveDate] = useState("");
  const [uploadExpiryDate, setUploadExpiryDate] = useState("");
  const [uploadConfidentiality, setUploadConfidentiality] = useState("");
  const [uploadSupersedesId, setUploadSupersedesId] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  async function handleUploadDocument() {
    if (!uploadFile) {
      setUploadError("Choose a file first.");
      return;
    }

    setUploading(true);
    setUploadError(null);

    try {
      await uploadAssessmentDocument(assessmentState.id, uploadFile, {
        document_type: uploadDocumentType,
        document_owner: uploadOwner || undefined,
        source: uploadSource || undefined,
        effective_date: uploadEffectiveDate || undefined,
        expiry_date: uploadExpiryDate || undefined,
        confidentiality: uploadConfidentiality || undefined,
        supersedes_id: uploadSupersedesId
          ? Number(uploadSupersedesId)
          : undefined,
      });

      setUploadFile(null);
      setUploadDocumentType("OTHER");
      setUploadOwner("");
      setUploadSource("");
      setUploadEffectiveDate("");
      setUploadExpiryDate("");
      setUploadConfidentiality("");
      setUploadSupersedesId("");

      // Stage 19: the upload returns as soon as the file is stored; text
      // extraction continues in the background and its progress shows on
      // the document in the list below.
      await refreshEvidenceData();
    } catch (error) {
      setUploadError(friendlyError(error, "The document couldn't be uploaded. Please try again."));
    } finally {
      setUploading(false);
    }
  }

  // -----------------------------------------------------------------
  // R2.3: correcting/confirming extracted business intelligence.
  // -----------------------------------------------------------------
  const [editingIntelligence, setEditingIntelligence] = useState(false);
  const [intelligenceDraft, setIntelligenceDraft] = useState({
    countries: "",
    customer_segments: "",
    third_party_vendors: "",
    // R3.1: additional structured profile fields.
    customer_type: "",
    transaction_origin: "",
    transaction_destination: "",
    transaction_frequency: "",
    payment_methods: "",
    onboarding_approach: "",
    ownership_entity_structure: "",
  });
  // R3.4: who is correcting the profile and why -- only required (and
  // only shown) when correcting a profile that was already confirmed.
  const [changeAttribution, setChangeAttribution] = useState({
    changed_by: "",
    change_reason: "",
  });
  const [savingIntelligence, setSavingIntelligence] = useState(false);
  const [confirmingIntelligence, setConfirmingIntelligence] = useState(false);
  const [intelligenceError, setIntelligenceError] = useState<string | null>(
    null
  );

  function startEditingIntelligence() {
    setIntelligenceDraft({
      countries: (intelligence?.countries ?? []).join(", "),
      customer_segments: (intelligence?.customer_segments ?? []).join(", "),
      third_party_vendors: (intelligence?.third_party_vendors ?? []).join(
        ", "
      ),
      customer_type: intelligence?.customer_type ?? "",
      transaction_origin: intelligence?.transaction_origin ?? "",
      transaction_destination: intelligence?.transaction_destination ?? "",
      transaction_frequency: intelligence?.transaction_frequency ?? "",
      payment_methods: (intelligence?.payment_methods ?? []).join(", "),
      onboarding_approach: intelligence?.onboarding_approach ?? "",
      ownership_entity_structure:
        intelligence?.ownership_entity_structure ?? "",
    });
    setChangeAttribution({
      changed_by: assessmentState.submitted_by ?? "",
      change_reason: "",
    });
    setIntelligenceError(null);
    setEditingIntelligence(true);
  }

  function splitCommaList(value: string): string[] {
    return value
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
  }

  async function handleSaveIntelligenceCorrection() {
    // R3.4: correcting an already-validated profile must be attributed
    // and justified.
    if (
      intelligence?.confirmed &&
      (!changeAttribution.changed_by.trim() ||
        !changeAttribution.change_reason.trim())
    ) {
      setIntelligenceError(
        "This profile was already confirmed. Enter your name and a reason for this change before saving."
      );
      return;
    }

    setSavingIntelligence(true);
    setIntelligenceError(null);

    try {
      const updated = await updateAssessmentIntelligence(assessmentState.id, {
        countries: splitCommaList(intelligenceDraft.countries),
        customer_segments: splitCommaList(
          intelligenceDraft.customer_segments
        ),
        third_party_vendors: splitCommaList(
          intelligenceDraft.third_party_vendors
        ),
        customer_type: intelligenceDraft.customer_type || null,
        transaction_origin: intelligenceDraft.transaction_origin || null,
        transaction_destination:
          intelligenceDraft.transaction_destination || null,
        transaction_frequency:
          intelligenceDraft.transaction_frequency || null,
        payment_methods: splitCommaList(intelligenceDraft.payment_methods),
        onboarding_approach: intelligenceDraft.onboarding_approach || null,
        ownership_entity_structure:
          intelligenceDraft.ownership_entity_structure || null,
        ...(intelligence?.confirmed
          ? {
              changed_by: changeAttribution.changed_by,
              change_reason: changeAttribution.change_reason,
            }
          : {}),
      });
      setIntelligence(updated);
      setEditingIntelligence(false);
      await refreshEvidenceData();
    } catch (error) {
      setIntelligenceError(
        error instanceof Error ? error.message : "Failed to save correction"
      );
    } finally {
      setSavingIntelligence(false);
    }
  }

  async function handleConfirmIntelligence() {
    setConfirmingIntelligence(true);
    setIntelligenceError(null);

    try {
      const updated = await confirmAssessmentIntelligence(
        assessmentState.id,
        assessmentState.submitted_by ?? "System"
      );
      setIntelligence(updated);
    } catch (error) {
      setIntelligenceError(
        error instanceof Error ? error.message : "Failed to confirm"
      );
    } finally {
      setConfirmingIntelligence(false);
    }
  }

  // -----------------------------------------------------------------
  // Stage 4 (R4.5): adding a risk factor manually / excluding one with
  // a reason.
  // -----------------------------------------------------------------
  const [addingRiskFactor, setAddingRiskFactor] = useState(false);
  const [newRiskFactor, setNewRiskFactor] = useState({
    category: RISK_CATEGORIES[0].value,
    indicators: [] as string[],
    rationale: "",
    misuse_scenario: "",
  });
  const [savingRiskFactor, setSavingRiskFactor] = useState(false);
  const [riskFactorError, setRiskFactorError] = useState<string | null>(null);

  const [excludingFactorId, setExcludingFactorId] = useState<number | null>(
    null
  );
  const [exclusionReason, setExclusionReason] = useState("");
  const [excludingLoading, setExcludingLoading] = useState(false);

  function toggleNewRiskFactorIndicator(indicator: string) {
    setNewRiskFactor((current) => ({
      ...current,
      indicators: current.indicators.includes(indicator)
        ? current.indicators.filter((value) => value !== indicator)
        : [...current.indicators, indicator],
    }));
  }

  async function handleAddRiskFactor() {
    if (!canRunPipeline) {
      setRiskFactorError("Only an FCRM Analyst, Manager or Admin can add risk factors.");
      return;
    }

    if (!newRiskFactor.rationale.trim()) {
      setRiskFactorError("A rationale is required.");
      return;
    }

    setSavingRiskFactor(true);
    setRiskFactorError(null);

    try {
      await addRiskFactor(assessmentState.id, {
        category: newRiskFactor.category,
        indicators: newRiskFactor.indicators,
        rationale: newRiskFactor.rationale,
        misuse_scenario: newRiskFactor.misuse_scenario || null,
        added_by: assessmentState.submitted_by ?? "System",
      });

      setNewRiskFactor({
        category: RISK_CATEGORIES[0].value,
        indicators: [],
        rationale: "",
        misuse_scenario: "",
      });
      setAddingRiskFactor(false);
      await refreshRiskFactors();
    } catch (error) {
      setRiskFactorError(
        error instanceof Error ? error.message : "Failed to add risk factor"
      );
    } finally {
      setSavingRiskFactor(false);
    }
  }

  async function handleExcludeRiskFactor(riskFactorId: number) {
    if (!canRunPipeline) {
      setRiskFactorError("Only an FCRM Analyst, Manager or Admin can exclude risk factors.");
      return;
    }

    if (!exclusionReason.trim()) {
      setRiskFactorError("A reason is required to exclude a risk factor.");
      return;
    }

    setExcludingLoading(true);
    setRiskFactorError(null);

    try {
      await excludeRiskFactor(
        assessmentState.id,
        riskFactorId,
        exclusionReason,
        assessmentState.submitted_by ?? "System"
      );
      setExcludingFactorId(null);
      setExclusionReason("");
      await refreshRiskFactors();
    } catch (error) {
      setRiskFactorError(
        error instanceof Error ? error.message : "Failed to exclude risk factor"
      );
    } finally {
      setExcludingLoading(false);
    }
  }

  // -----------------------------------------------------------------
  // Stage 6 (R6.2, R6.3): manual likelihood/impact rating per factor --
  // the only input to that factor's numeric score, which is then
  // computed deterministically by the backend (never by the AI).
  // -----------------------------------------------------------------
  const [ratingFactorId, setRatingFactorId] = useState<number | null>(null);
  const [ratingInputs, setRatingInputs] = useState({ likelihood: 3, impact: 3, reason: "" });
  const [ratingLoading, setRatingLoading] = useState(false);

  // The AI's suggested ratings, which pre-fill the form above. Asking for
  // them changes nothing about the calculation -- each one still has to
  // be confirmed or overridden by an analyst before it counts.
  const [suggestingRatings, setSuggestingRatings] = useState(false);
  const [suggestNotice, setSuggestNotice] = useState<string | null>(null);

  async function handleSuggestRatings() {
    if (!canRunPipeline) {
      setRiskFactorError("Only an FCRM Analyst, Manager or Admin can request AI ratings.");
      return;
    }

    setSuggestingRatings(true);
    setRiskFactorError(null);
    setSuggestNotice(null);

    try {
      const result = await suggestRiskFactorRatings(assessmentState.id, {
        requestedBy: assessmentState.submitted_by ?? "System",
      });

      setSuggestNotice(
        result.degraded
          ? `Suggested ratings for ${result.suggested_count} factor(s). ` +
            `${result.fallback_count} could not be estimated and were left ` +
            `blank — rate those manually.`
          : `Suggested ratings for ${result.suggested_count} factor(s). ` +
            `Each still needs your confirmation or override before the ` +
            `inherent risk calculation can be finalised.`
      );

      await refreshRiskFactors();
    } catch (error) {
      setRiskFactorError(
        error instanceof Error
          ? error.message
          : "Failed to suggest risk factor ratings"
      );
    } finally {
      setSuggestingRatings(false);
    }
  }

  async function handleRateRiskFactor(riskFactorId: number) {
    if (!canRunPipeline) {
      setRiskFactorError("Only an FCRM Analyst, Manager or Admin can rate risk factors.");
      return;
    }

    setRatingLoading(true);
    setRiskFactorError(null);

    try {
      await rateRiskFactor(
        assessmentState.id,
        riskFactorId,
        ratingInputs.likelihood,
        ratingInputs.impact,
        ratingInputs.reason.trim() || undefined
      );
      // Close this factor's form only -- the analyst may already have
      // opened the next one while this save was in flight.
      setRatingFactorId((current) => (current === riskFactorId ? null : current));
      await refreshRiskFactors();
    } catch (error) {
      setRiskFactorError(
        error instanceof Error ? error.message : "Failed to rate risk factor"
      );
    } finally {
      setRatingLoading(false);
    }
  }

  // R6.7: an authorized analyst overriding the calculated inherent-risk
  // rating. A reason is mandatory; the calculated value/user/timestamp
  // are captured server-side.
  const [overridingInherentRisk, setOverridingInherentRisk] = useState(false);
  const [overrideInputs, setOverrideInputs] = useState({
    value: 0,
    band: "",
    reason: "",
  });
  const [overrideLoading, setOverrideLoading] = useState(false);
  const [overrideError, setOverrideError] = useState<string | null>(null);

  async function handleOverrideInherentRisk() {
    if (!canRunPipeline) {
      setOverrideError("Only an FCRM Analyst, Manager or Admin can override the inherent risk rating.");
      return;
    }

    if (!overrideInputs.reason.trim()) {
      setOverrideError("A reason is required to override the calculated rating.");
      return;
    }

    setOverrideLoading(true);
    setOverrideError(null);

    try {
      const updated = await overrideInherentRisk(
        assessmentState.id,
        overrideInputs.value,
        overrideInputs.reason,
        overrideInputs.band || undefined,
        assessmentState.submitted_by ?? "System"
      );
      setInherentRiskCalc(updated);
      setOverridingInherentRisk(false);
    } catch (error) {
      setOverrideError(
        error instanceof Error ? error.message : "Failed to override inherent risk"
      );
    } finally {
      setOverrideLoading(false);
    }
  }

  // Human Review's submit button: HUMAN_REVIEW -> SUBMITTED_TO_MANAGER
  // (AW). Enters the hierarchical approval workflow instead of the old
  // direct advance into COMMITTEE_DECISION.
  const [submitReviewLoading, setSubmitReviewLoading] = useState(false);
  const [submitReviewError, setSubmitReviewError] = useState<string | null>(
    null
  );

  async function handleSubmitForCommitteeReview() {
    if (
      assessmentState.status !== "HUMAN_REVIEW" &&
      assessmentState.status !== "RETURNED_BY_MANAGER"
    ) {
      setSubmitReviewError(
        `Cannot submit to manager: assessment is "${assessmentState.status}".`
      );
      return;
    }

    setSubmitReviewLoading(true);
    setSubmitReviewError(null);

    try {
      const updated = await submitToManager(assessmentState.id);
      setAssessmentState(updated);

      const events = await getAssessmentAudit(updated.id);
      setAuditEvents(events);

      const nextStep = inferStepFromStatus(updated.status);
      setMaxStepReachedState(nextStep);
      setCurrentStepState(nextStep);
      saveStep(updated.id, nextStep);
    } catch (error) {
      setSubmitReviewError(
        friendlyError(error, "The assessment couldn't be submitted to your manager. Your work has been kept — please try again.")
      );
    } finally {
      setSubmitReviewLoading(false);
    }
  }

  function handleStageTrackerClick(stageIndex: number, stageKey: string) {
    const steps = STAGE_STEPS[stageKey] ?? STAGE_STEPS[TRACKED_STAGES[stageIndex]?.key ?? ""] ?? [];
    if (steps.length === 0 || steps[0] > maxStepReached) return;
    // A node that covers two steps shows both on one screen; land on the
    // furthest one reached so nothing is hidden.
    const newStep = Math.min(steps[steps.length - 1], maxStepReached);
    setCurrentStep(newStep);
    // Scroll to top of the content panel
    stepPanelRef.current?.scrollIntoView({ behavior: "smooth" });
  }

  // Retention and reassessment follow the final decision, on the Approval screen.
  const isDecided = currentStep === 7 && DECIDED_STATUSES.includes(assessmentState.status);

  return (
    <div className="assessment-workflow">

      {/* Stage move in progress: dialog, or a note while it runs in the background. */}

      {stageDialogOpen && stageMoveFrom && (
        <StageMoveModal
          assessment={assessmentState}
          user={user}
          fromStatus={stageMoveFrom}
          job={stageJob}
          documentCount={documents.length}
          riskResults={riskResults}
          retrying={stageRetrying}
          onRetry={handleRetryStageMove}
          onClose={() => setStageDialogOpen(false)}
        />
      )}

      {stageMoveRunning && !stageDialogOpen && (
        <p role="status" className="stage-move-note">
          A stage move is running in the background
          {stageJob ? ` (${stageJob.progress}%)` : ""}.{" "}
          <button type="button" className="link-button" onClick={() => setStageDialogOpen(true)}>
            View progress
          </button>
        </p>
      )}

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
            {assessmentState.workflow_status_label ?? assessmentState.status}
          </strong>

        </div>

      </div>

      {/* Stage 19: sticky "where am I" bar + "On this page" section jump
          list for long reviews; persists scroll/section per assessment
          and step in sessionStorage (see SectionNavigator). */}

      <SectionNavigator
        containerRef={stepPanelRef}
        title={assessmentState.title}
        referenceId={assessmentState.reference_id ?? `ASSESSMENT-${assessmentState.id}`}
        stageLabel={`${stepLabel(currentStep)} (step ${stageIndexForStep(currentStep) + 1} of ${TRACKED_STAGES.length}) · ${
          assessmentState.workflow_status_label ?? assessmentState.status
        }`}
        storageKey={`assessment-${assessmentState.id}-step-${currentStep}`}
        ready={!loading}
      />

      {/* 7-node pipeline tracker — the real, server-driven source of
          truth for where this assessment stands. Business Request ->
          Intake -> Evidence Collection -> Risk Identification ->
          Control Assessment -> Residual Risk -> Human Review ->
          Approval (manager review + committee decision). */}

      <section className="workflow-card">
        <StageTracker
          status={assessmentState.status}
          role={user.role}
          onStageClick={handleStageTrackerClick}
          disableNavigation={false}
        />
      </section>

      <ManagerFeedbackBanner assessment={assessmentState} />

      {/* Stage 14: lifecycle status, owner / next action, SLA, escalation,
          permitted next statuses and workflow history. */}

      <WorkflowPanel
        assessment={assessmentState}
        user={user}
        onAssessmentChanged={setAssessmentState}
        onEditDraft={
          onEditDraft && assessmentState.is_draft && user.id === assessmentState.owner_id
            ? () => onEditDraft(assessmentState)
            : undefined
        }
      />

      {/* Workflow Content */}

      {loadError && !loading && (
        <div className="wf-banner wf-banner-danger" role="alert" style={{ margin: "16px 28px 0" }}>
          {loadError}{" "}
          <button
            type="button"
            className="link-button"
            onClick={() => setLoadAttempt((attempt) => attempt + 1)}
          >
            Try again
          </button>
        </div>
      )}

      <div
        id="workflow-step-panel"
        ref={stepPanelRef}
      >
      {loading ? (

        <div className="workflow-loading" role="status" aria-live="polite">
          Loading assessment information...
        </div>

      ) : currentStep === 1 || currentStep === 2 ? (
<>

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

                <span
                  className="ai-badge"
                  title={
                    intelligence?.confirmed
                      ? `Confirmed by ${intelligence.confirmed_by ?? "a reviewer"}`
                      : "Not yet confirmed by a reviewer"
                  }
                >
                  {intelligence?.confirmed
                    ? `✓ CONFIRMED`
                    : "AI EXTRACTION — UNCONFIRMED"}
                </span>

              </div>

              {/* R3.2: contradictions between the intake form and this
                  structured profile, and between uploaded documents. */}
              {intelligence &&
                intelligence.conflicts &&
                intelligence.conflicts.length > 0 && (
                  <div className="form-warning" style={{ margin: 16 }} role="status">
                    <strong>
                      {intelligence.conflicts.length === 1
                        ? "1 inconsistency detected:"
                        : `${intelligence.conflicts.length} inconsistencies detected:`}
                    </strong>
                    <ul style={{ margin: "8px 0 0 20px", padding: 0 }}>
                      {intelligence.conflicts.map((conflict) => (
                        <li key={conflict.field}>{conflict.message}</li>
                      ))}
                    </ul>
                  </div>
                )}

              {intelligence && !editingIntelligence && (
                <div
                  className="workflow-card-header"
                  style={{ paddingTop: 0 }}
                >
                  <button
                    type="button"
                    className="doc-action-button"
                    onClick={startEditingIntelligence}
                  >
                    Correct values
                  </button>

                  {/* R3.3: only the business owner who raised the request confirms it. */}
                  {isOwner || intelligence.confirmed ? (
                    <button
                      type="button"
                      className="doc-action-button"
                      disabled={confirmingIntelligence || intelligence.confirmed}
                      onClick={handleConfirmIntelligence}
                    >
                      {confirmingIntelligence
                        ? "Confirming..."
                        : intelligence.confirmed
                        ? "Confirmed"
                        : "Confirm extracted information"}
                    </button>
                  ) : (
                    <span style={{ color: "#667085", fontSize: 13, alignSelf: "center" }}>
                      Awaiting confirmation by the business owner.
                    </span>
                  )}
                </div>
              )}

              {intelligenceError && (
                <div className="form-error" role="alert">{intelligenceError}</div>
              )}

              {editingIntelligence ? (
                <div className="metadata-list" style={{ padding: 16 }}>
                  {/* R2.3: only the fields most often wrong/incomplete are
                      correctable inline; the AI's original output is kept
                      intact in raw_extraction regardless of what's edited
                      here. */}
                  <div className="form-group">
                    <label>Countries (comma-separated)</label>
                    <input aria-label="Countries (comma-separated)"
                      type="text"
                      value={intelligenceDraft.countries}
                      onChange={(event) =>
                        setIntelligenceDraft((current) => ({
                          ...current,
                          countries: event.target.value,
                        }))
                      }
                    />
                  </div>

                  <div className="form-group">
                    <label>Customer Segments (comma-separated)</label>
                    <input aria-label="Customer Segments (comma-separated)"
                      type="text"
                      value={intelligenceDraft.customer_segments}
                      onChange={(event) =>
                        setIntelligenceDraft((current) => ({
                          ...current,
                          customer_segments: event.target.value,
                        }))
                      }
                    />
                  </div>

                  <div className="form-group">
                    <label>Third-Party Vendors (comma-separated)</label>
                    <input aria-label="Third-Party Vendors (comma-separated)"
                      type="text"
                      value={intelligenceDraft.third_party_vendors}
                      onChange={(event) =>
                        setIntelligenceDraft((current) => ({
                          ...current,
                          third_party_vendors: event.target.value,
                        }))
                      }
                    />
                  </div>

                  {/* R3.1: remaining structured profile fields. */}
                  <div className="form-row">
                    <div className="form-group">
                      <label>Customer Type</label>
                      <input aria-label="Customer Type"
                        type="text"
                        value={intelligenceDraft.customer_type}
                        onChange={(event) =>
                          setIntelligenceDraft((current) => ({
                            ...current,
                            customer_type: event.target.value,
                          }))
                        }
                        placeholder="e.g. Retail, SME, Institutional"
                      />
                    </div>

                    <div className="form-group">
                      <label>Onboarding Approach</label>
                      <input aria-label="Onboarding Approach"
                        type="text"
                        value={intelligenceDraft.onboarding_approach}
                        onChange={(event) =>
                          setIntelligenceDraft((current) => ({
                            ...current,
                            onboarding_approach: event.target.value,
                          }))
                        }
                        placeholder="e.g. Digital self-service, In-branch"
                      />
                    </div>
                  </div>

                  <div className="form-row">
                    <div className="form-group">
                      <label>Transaction Origin</label>
                      <input aria-label="Transaction Origin"
                        type="text"
                        value={intelligenceDraft.transaction_origin}
                        onChange={(event) =>
                          setIntelligenceDraft((current) => ({
                            ...current,
                            transaction_origin: event.target.value,
                          }))
                        }
                      />
                    </div>

                    <div className="form-group">
                      <label>Transaction Destination</label>
                      <input aria-label="Transaction Destination"
                        type="text"
                        value={intelligenceDraft.transaction_destination}
                        onChange={(event) =>
                          setIntelligenceDraft((current) => ({
                            ...current,
                            transaction_destination: event.target.value,
                          }))
                        }
                      />
                    </div>
                  </div>

                  <div className="form-row">
                    <div className="form-group">
                      <label>Transaction Frequency</label>
                      <input aria-label="Transaction Frequency"
                        type="text"
                        value={intelligenceDraft.transaction_frequency}
                        onChange={(event) =>
                          setIntelligenceDraft((current) => ({
                            ...current,
                            transaction_frequency: event.target.value,
                          }))
                        }
                        placeholder="e.g. Daily, Monthly"
                      />
                    </div>

                    <div className="form-group">
                      <label>Payment Methods (comma-separated)</label>
                      <input aria-label="Payment Methods (comma-separated)"
                        type="text"
                        value={intelligenceDraft.payment_methods}
                        onChange={(event) =>
                          setIntelligenceDraft((current) => ({
                            ...current,
                            payment_methods: event.target.value,
                          }))
                        }
                      />
                    </div>
                  </div>

                  <div className="form-group">
                    <label>Ownership / Entity Structure</label>
                    <input aria-label="Ownership / Entity Structure"
                      type="text"
                      value={intelligenceDraft.ownership_entity_structure}
                      onChange={(event) =>
                        setIntelligenceDraft((current) => ({
                          ...current,
                          ownership_entity_structure: event.target.value,
                        }))
                      }
                    />
                  </div>

                  {/* R3.4: required only when correcting an already
                      -confirmed/validated profile. */}
                  {intelligence?.confirmed && (
                    <div
                      className="form-warning"
                      style={{ marginBottom: 12 }}
                    >
                      This profile was already confirmed. Correcting it
                      requires your name and a reason for the change.
                    </div>
                  )}

                  {intelligence?.confirmed && (
                    <div className="form-row">
                      <div className="form-group">
                        <label>Your Name</label>
                        <input aria-label="Your Name"
                          type="text"
                          value={changeAttribution.changed_by}
                          onChange={(event) =>
                            setChangeAttribution((current) => ({
                              ...current,
                              changed_by: event.target.value,
                            }))
                          }
                          placeholder="Who is making this change"
                        />
                      </div>

                      <div className="form-group">
                        <label>Reason for Change</label>
                        <input aria-label="Reason for Change"
                          type="text"
                          value={changeAttribution.change_reason}
                          onChange={(event) =>
                            setChangeAttribution((current) => ({
                              ...current,
                              change_reason: event.target.value,
                            }))
                          }
                          placeholder="Why is the validated profile changing"
                        />
                      </div>
                    </div>
                  )}

                  <div className="form-actions">
                    <button
                      type="button"
                      className="secondary-button"
                      onClick={() => setEditingIntelligence(false)}
                      disabled={savingIntelligence}
                    >
                      Cancel
                    </button>

                    <button
                      type="button"
                      className="primary-button"
                      onClick={handleSaveIntelligenceCorrection}
                      disabled={savingIntelligence}
                    >
                      {savingIntelligence ? "Saving..." : "Save Correction"}
                    </button>
                  </div>
                </div>
              ) : (
              <div className="metadata-list">

                {/* R2.3: the AI's original value of each field a user has corrected. */}
                {intelligence && Object.keys(intelligence.original_values ?? {}).length > 0 && (
                  <div
                    role="note"
                    style={{ border: "1px solid #e5e7eb", borderRadius: 6, padding: "8px 12px", margin: "4px 0 8px" }}
                  >
                    <strong>Corrected after extraction</strong>
                    <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
                      {Object.entries(intelligence.original_values ?? {}).map(([field, original]) => {
                        const current = (intelligence as unknown as Record<string, unknown>)[field];
                        const show = (value: unknown) =>
                          Array.isArray(value) ? value.join(", ") || "—" : value == null || value === "" ? "—" : String(value);
                        return (
                          <li key={field}>
                            {field.replace(/_/g, " ")}: AI extracted <em>{show(original)}</em>, now{" "}
                            <strong>{show(current)}</strong>
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                )}

                {/* P4 (R2.4 / R3.4): provenance of each field, and every recorded version. */}
                <ProfileProvenanceTable intelligence={intelligence} />
                <IntakeHistoryPanel assessmentId={assessment.id} refreshKey={intelligence} />

                {/* R3.1: remaining structured profile fields. */}
                <MetadataRow
                  label="Customer Type"
                  values={
                    intelligence?.customer_type
                      ? [intelligence.customer_type]
                      : []
                  }
                />

                <MetadataRow
                  label="Onboarding Approach"
                  values={
                    intelligence?.onboarding_approach
                      ? [intelligence.onboarding_approach]
                      : []
                  }
                />

                <MetadataRow
                  label="Transaction Origin / Destination"
                  values={[
                    intelligence?.transaction_origin,
                    intelligence?.transaction_destination,
                  ].filter((value): value is string => Boolean(value))}
                />

                <MetadataRow
                  label="Transaction Frequency"
                  values={
                    intelligence?.transaction_frequency
                      ? [intelligence.transaction_frequency]
                      : []
                  }
                />

                <MetadataRow
                  label="Payment Methods"
                  values={intelligence?.payment_methods || []}
                />

                <MetadataRow
                  label="Ownership / Entity Structure"
                  values={
                    intelligence?.ownership_entity_structure
                      ? [intelligence.ownership_entity_structure]
                      : []
                  }
                />

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
              )}

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


            {/* Submitted Evidence: the short list, only until the detailed
                "Submitted Evidence Files" list opens with evidence collection. */}

            {maxStepReached < 2 && (
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

                        {/* Stage 19: progress / error + retry / incomplete marker. */}
                        {document.processing && document.processing.status !== "SUCCEEDED" && (
                          <ProcessingStatus
                            key={document.processing.id}
                            job={document.processing}
                            subject={document.filename}
                            onFinished={() => refreshEvidenceData()}
                          />
                        )}

                      </div>

                      <DocumentProcessingBadge job={document.processing} />

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
                          disabled={document.can_open_original === false}
                          title={
                            document.can_open_original === false
                              ? "This document is classified; only the assessment owner and reviewing analysts can download the original file."
                              : undefined
                          }
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
            )}


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
                  assessmentState.status !== "INTAKE"
                }
                onClick={handleConfirmIntake}
              >
                {confirmIntakeLoading
                  ? "Completing intake…"
                  : assessmentState.status !== "INTAKE"
                  ? `Already ${assessmentState.status}`
                  : "Complete Intake & Start Evidence Collection"}
              </button>

              {confirmIntakeError && (
                <p className="commit-error" role="alert">
                  {confirmIntakeError}
                </p>
              )}

            </section>

          </aside>

        </div>

{maxStepReached >= 2 ? (

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

              {assessmentState.status === "EVIDENCE_COLLECTION" && (
                <div style={{ marginTop: 16 }}>
                  {!canAdvanceStage && (
                    <p style={{ color: "#667085" }}>
                      Only this assessment's owner, an FCRM Analyst, Manager, or Admin
                      can run risk identification.
                    </p>
                  )}
                  {canAdvanceStage && advanceError && (
                    <p role="alert" style={{ color: "#b91c1c" }}>{advanceError}</p>
                  )}
                  {canAdvanceStage && (
                    <button
                      className="primary-button"
                      // A refusal is already shown beside the button.
                      onClick={() => handleAdvanceStage().catch(() => undefined)}
                      disabled={advanceLoading || stageMoveRunning}
                    >
                      {advanceLoading || stageMoveRunning
                        ? "Running Risk Identification..."
                        : "Run Risk Identification & Continue"}
                    </button>
                  )}
                </div>
              )}

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


            {/* R2.1/R2.2/R2.6: upload a new evidence document, with
                classification metadata and optional versioning. */}

            <section className="workflow-card">

              <div className="workflow-card-header">
                <div>
                  <h2>Upload Evidence Document</h2>
                  <p>
                    Classify the document as you upload it; supported types:
                    DOCX, DOC, XLSX, PDF, TXT, CSV.
                  </p>
                </div>
              </div>

              {uploadError && (
                <div className="form-error">{uploadError}</div>
              )}

              <div className="metadata-list" style={{ padding: 16 }}>
                <div className="form-group">
                  <label>File</label>
                  <input
                    type="file"
                    accept=".docx,.doc,.xlsx,.pdf,.csv,.txt"
                    onChange={(event) =>
                      setUploadFile(event.target.files?.[0] ?? null)
                    }
                  />
                </div>

                <div className="form-row">
                  <div className="form-group">
                    <label>Document Type</label>
                    <select
                      value={uploadDocumentType}
                      onChange={(event) =>
                        setUploadDocumentType(event.target.value)
                      }
                    >
                      {DOCUMENT_TYPES.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </div>

                  <div className="form-group">
                    <label>Confidentiality</label>
                    <select
                      value={uploadConfidentiality}
                      onChange={(event) =>
                        setUploadConfidentiality(event.target.value)
                      }
                    >
                      <option value="">Not specified</option>
                      {CONFIDENTIALITY_LEVELS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>

                <div className="form-row">
                  <div className="form-group">
                    <label>Document Owner</label>
                    <input
                      type="text"
                      value={uploadOwner}
                      onChange={(event) => setUploadOwner(event.target.value)}
                      placeholder="e.g. Compliance Team"
                    />
                  </div>

                  <div className="form-group">
                    <label>Source</label>
                    <input
                      type="text"
                      value={uploadSource}
                      onChange={(event) => setUploadSource(event.target.value)}
                      placeholder="e.g. Vendor portal"
                    />
                  </div>
                </div>

                <div className="form-row">
                  <div className="form-group">
                    <label>Effective Date</label>
                    <input
                      type="date"
                      value={uploadEffectiveDate}
                      onChange={(event) =>
                        setUploadEffectiveDate(event.target.value)
                      }
                    />
                  </div>

                  <div className="form-group">
                    <label>Expiry Date</label>
                    <input
                      type="date"
                      value={uploadExpiryDate}
                      onChange={(event) =>
                        setUploadExpiryDate(event.target.value)
                      }
                    />
                  </div>
                </div>

                {uploadSupersedesId && (
                  <p className="form-hint">
                    This upload will be saved as a new version, superseding
                    document #{uploadSupersedesId}.{" "}
                    <button
                      type="button"
                      className="doc-action-button"
                      onClick={() => setUploadSupersedesId("")}
                    >
                      Clear
                    </button>
                  </p>
                )}

                <div className="form-actions">
                  <button
                    type="button"
                    className="primary-button"
                    onClick={handleUploadDocument}
                    disabled={uploading}
                  >
                    {uploading ? "Uploading..." : "Upload Document"}
                  </button>
                </div>
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
                  {documents.filter((document) => document.is_current).length} FILES
                </span>

              </div>

              <div className="evidence-file-list">

                {documents.length === 0 ? (

                  <div className="workflow-empty">
                    No source documents are attached to this
                    assessment.
                  </div>

                ) : (

                  documents.map((document) => {
                    const isExpired = Boolean(
                      document.expiry_date &&
                        document.expiry_date < new Date().toISOString().slice(0, 10)
                    );
                    const documentTypeLabel =
                      DOCUMENT_TYPES.find(
                        (option) => option.value === document.document_type
                      )?.label ?? document.document_type;

                    return (
                    <div
                      className="evidence-file"
                      key={document.id}
                      style={
                        !document.is_current
                          ? { opacity: 0.6 }
                          : undefined
                      }
                    >

                      <div className="evidence-file-icon">
                        📄
                      </div>

                      <div className="evidence-file-info">

                        <strong>
                          {document.filename}
                        </strong>

                        <span>
                          {document.file_type.toUpperCase()} ·{" "}
                          {documentTypeLabel} · v{document.version}
                          {!document.is_current && " (superseded)"}
                        </span>

                        {/* R2.2: the document's full metadata. */}
                        <span>
                          Owner: {document.document_owner || "—"} · Source:{" "}
                          {document.source || "—"} · Confidentiality:{" "}
                          {(document.confidentiality || "—").replace(/_/g, " ").toLowerCase()}
                        </span>

                        <span>
                          Effective: {document.effective_date || "—"} · Expires:{" "}
                          {document.expiry_date || "none"} · Uploaded:{" "}
                          {new Date(document.created_at).toLocaleDateString()}
                        </span>

                        {document.is_masked && (
                          <span>
                            <span aria-hidden="true">🔒 </span>
                            Confidential — sensitive values are masked for your role
                            {document.can_open_original === false && "; the original file is not available to you"}.
                          </span>
                        )}

                        {/* Stage 19: progress / error + retry / incomplete marker. */}
                        {document.processing && document.processing.status !== "SUCCEEDED" && (
                          <ProcessingStatus
                            key={document.processing.id}
                            job={document.processing}
                            subject={document.filename}
                            onFinished={() => refreshEvidenceData()}
                          />
                        )}

                      </div>

                      {isExpired && document.is_current && (
                        <span
                          className="parsed-badge"
                          style={{ background: "#fee2e2", color: "#991b1b" }}
                          title={`Expired on ${document.expiry_date} — confirm this is still valid before relying on it as evidence.`}
                        >
                          EXPIRED
                        </span>
                      )}

                      <DocumentProcessingBadge job={document.processing} />

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
                          disabled={document.can_open_original === false}
                          title={
                            document.can_open_original === false
                              ? "This document is classified; only the assessment owner and reviewing analysts can download the original file."
                              : undefined
                          }
                          onClick={() =>
                            downloadDocument(
                              assessmentState.id,
                              document
                            )
                          }
                        >
                          Download
                        </button>

                        {document.is_current && (
                          <button
                            type="button"
                            className="doc-action-button"
                            onClick={() =>
                              setUploadSupersedesId(String(document.id))
                            }
                          >
                            Upload New Version
                          </button>
                        )}

                      </div>

                    </div>
                    );
                  })

                )}

              </div>

            </section>

          </div>


          {/* Evidence Sidebar */}

          <aside className="evidence-sidebar">

            {/* R2.5: missing-evidence / information-request checklist. */}
            <section className="workflow-card">

              <div className="workflow-card-header">
                <div>
                  <h2>Evidence Gaps</h2>
                  <p>Missing information requests for this assessment.</p>
                </div>

                {evidenceGaps && (
                  <span
                    className="source-badge"
                    style={
                      evidenceGaps.open_count > 0
                        ? { background: "#fee2e2", color: "#991b1b" }
                        : undefined
                    }
                  >
                    {evidenceGaps.open_count} OPEN
                  </span>
                )}
              </div>

              {!evidenceGaps ? (
                <div className="workflow-empty">Loading...</div>
              ) : (
                <div className="evidence-check-list">
                  {evidenceGaps.issues.map((issue) => (
                    <div className="evidence-check" key={issue.code}>
                      <span className="check-icon">
                        {issue.missing ? "⚠" : "✓"}
                      </span>
                      <span>
                        <strong>{issue.title}</strong>
                        {issue.missing && (
                          <>
                            {" — "}
                            {issue.description}
                          </>
                        )}
                      </span>
                    </div>
                  ))}

                  {evidenceGaps.document_warnings.map((warning) => (
                    <div key={`warning-${warning.document_id}`}>
                      <div className="evidence-check">
                        <span className="check-icon">⚠</span>
                        <span>
                          <strong>{warning.filename}</strong> — {warning.reason}
                        </span>
                      </div>
                      {/* P4 (R2.6): decide before an expired document is used. */}
                      <ExpiredEvidenceDecision
                        assessmentId={assessment.id}
                        warning={warning}
                        canDecide={(isOwner || canRunPipeline) && ["INTAKE", "EVIDENCE_COLLECTION", "REMEDIATION", "RISK_IDENTIFICATION"].includes(assessmentState.status)}
                        onChanged={refreshEvidenceData}
                      />
                    </div>
                  ))}
                </div>
              )}

            </section>

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


            {/*
              Stage 5: regulatory considerations are AI-extracted from the
              intake documents. They are not verified against any approved
              source, so they must never be presented as approved citations
              or regulatory requirements.
            */}

            <section className="workflow-card">

              <div className="workflow-card-header">

                <div>
                  <h2>
                    Regulatory Considerations (Unverified)
                  </h2>

                  <p>
                    Extracted by AI from the intake documents. These have
                    not been checked against approved policy or regulatory
                    sources and are not regulatory requirements. Confirm
                    each one against an approved source before relying on it.
                  </p>
                </div>

              </div>

              {(intelligence?.regulatory_considerations ?? []).length === 0 ? (

                <div className="workflow-empty">
                  No regulatory considerations extracted.
                </div>

              ) : (

                <div className="evidence-citation-list">

                  {(intelligence?.regulatory_considerations ?? []).map((item, index) => (

                    <div
                      className="evidence-citation-card"
                      key={index}
                    >
                      <strong>
                        Consideration {index + 1} — unverified, AI-extracted
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

                {[
                  {
                    label: "Source document text extracted",
                    met: documents.some(
                      (document) =>
                        document.is_current && Boolean(document.extracted_text?.trim())
                    ),
                  },
                  {
                    label: "Business intelligence available",
                    met: Boolean(intelligence),
                  },
                  {
                    label: "Profile confirmed by the business owner",
                    met: Boolean(intelligence?.confirmed),
                  },
                  {
                    label: "Assessment evidence provided",
                    met: Boolean(assessmentState.evidence),
                  },
                ].map((check) => (
                  <div className="evidence-check" key={check.label}>
                    <span className="check-icon" aria-hidden="true">
                      {check.met ? "✓" : "!"}
                    </span>

                    <span>
                      {check.label}: {check.met ? "yes" : "no"}
                    </span>
                  </div>
                ))}

              </div>

            </section>

          </aside>

        </div>

) : (
  <p className="step-locked">Evidence collection opens once intake is complete.</p>
)}
</>
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
              AI-identified risk categories from the assessment
              evidence and business intelligence — a dynamic set,
              not a fixed list.
            </p>
          </div>

          <div className="risk-header-actions">
            <span className="risk-engine-badge">
              AI ANALYSIS
            </span>

            {canRunPipeline &&
              assessmentState.status === "RISK_IDENTIFICATION" && (
                <button
                  className="secondary-button"
                  onClick={handleRerunAnalysis}
                  disabled={rerunLoading}
                >
                  {rerunLoading
                    ? `Re-running… ${rerunProgress}%`
                    : "Re-run analysis"}
                </button>
              )}
          </div>

        </div>

        {rerunError && (
          <p role="alert" className="risk-rerun-error">
            {rerunError}
          </p>
        )}

        {isDegradedResult && (
          <div
            className="degraded-result-banner"
            role="region"
            aria-label="Provisional analysis"
          >
            <strong>
              {assessmentState.assessment_mode === "rules_only"
                ? "Provisional — rules-only result"
                : "Analysis unavailable — no rating recorded"}
            </strong>
            <p>{assessmentState.degraded_reason}</p>

            {assessmentState.degraded_acknowledged_at ? (
              <p>
                Acknowledged by {assessmentState.degraded_acknowledged_by ?? "a reviewer"} on{" "}
                {new Date(assessmentState.degraded_acknowledged_at).toLocaleString()}.
              </p>
            ) : needsDegradedAcknowledgement && canRunPipeline &&
              assessmentState.status === "RISK_IDENTIFICATION" ? (
              <>
                <p>
                  Review the rule-engine factors below. Re-run the analysis to try the
                  AI again, or acknowledge this result to continue with it.
                </p>
                <button
                  type="button"
                  className="primary-button"
                  onClick={handleAcknowledgeDegraded}
                  disabled={acknowledgingDegraded}
                >
                  {acknowledgingDegraded ? "Acknowledging..." : "Acknowledge provisional result"}
                </button>
              </>
            ) : needsDegradedAcknowledgement ? (
              <p>An FCRM Analyst, Manager or Admin must review and acknowledge it.</p>
            ) : null}

            {acknowledgeError && <p role="alert">{acknowledgeError}</p>}
          </div>
        )}

        {riskResults.length === 0 ? (

          <div className="risk-empty" role="status">
            {aiAnalysisUnavailable ? (
              <>
                <h3>
                  AI analysis failed, review manually
                </h3>

                <p>
                  The AI service didn't respond, so every risk
                  category was saved as needing manual review.
                  {canRunPipeline
                    ? " Click Re-run analysis to try again, or add the relevant risk factors yourself."
                    : " An FCRM Analyst needs to re-run the analysis or add the risk factors manually."}
                </p>
              </>
            ) : (
              <>
                <h3>
                  Risk analysis has not been run
                </h3>

                <p>
                  {assessmentState.status === "EVIDENCE_COLLECTION"
                    ? "Confirm the extracted information on the Evidence step, then click Run Risk Identification & Continue."
                    : "No applicable risk factors were identified. Re-run the analysis or add risk factors manually."}
                </p>
              </>
            )}
          </div>

        ) : (

          <div className="risk-result-list">

            {riskResults.map((result) => {
              const unrated = isUnratedResult(result.dimension);

              return (
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

                  {unrated ? (
                    <div
                      className="risk-score-badge unrated"
                      title="Not yet rated by an analyst"
                    >
                      UNRATED
                    </div>
                  ) : (
                  <div
                    className={`risk-score-badge ${result.severity.toLowerCase()}`}
                    title={`Severity: ${result.severity}`}
                  >
                    <RiskLevelIcon level={result.severity} />{" "}
                    {result.severity}
                  </div>
                  )}

                </div>

                <div className="risk-score-row">

                  <div className="risk-progress">

                    <div
                      className={`risk-progress-fill ${unrated ? "unrated" : result.severity.toLowerCase()}`}
                      style={{
                        width: unrated ? "0%" : `${Math.min(
                          result.score,
                          100
                        )}%`,
                      }}
                    />

                  </div>

                  <strong aria-label={unrated ? "Not yet rated" : undefined}>
                    {unrated ? "—" : result.score}
                  </strong>

                </div>

                <p className="risk-reason">
                  {result.reason}
                </p>

              </div>
              );
            })}

          </div>

        )}

      </section>

      {/* Stage 4 (R4.1-R4.5): risk factor categories, separate from the
          6-dimension scoring above -- this never affects overall_score. */}
      <section className="workflow-card">

        <div className="workflow-card-header">
          <div>
            <h2>Risk Factor Categories</h2>
            <p>
              Which of the 10 canonical risk categories apply to this
              change, why, and how they could be misused.
            </p>
          </div>

          <button
            type="button"
            className="doc-action-button"
            onClick={() => {
              setAddingRiskFactor((current) => !current);
              setRiskFactorError(null);
            }}
          >
            {addingRiskFactor ? "Cancel" : "+ Add Risk Factor"}
          </button>
        </div>

        {riskFactorError && (
          <div className="form-error" role="alert">{riskFactorError}</div>
        )}

        {addingRiskFactor && (
          <div className="metadata-list" style={{ padding: 16 }}>
            <div className="form-group">
              <label>Category</label>
              <select aria-label="Category"
                value={newRiskFactor.category}
                onChange={(event) =>
                  setNewRiskFactor((current) => ({
                    ...current,
                    category: event.target.value,
                  }))
                }
              >
                {RISK_CATEGORIES.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>

            <div className="form-group">
              <label>Indicators</label>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                {RISK_INDICATORS.map((option) => (
                  <label
                    key={option.value}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 4,
                      fontSize: 13,
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={newRiskFactor.indicators.includes(
                        option.value
                      )}
                      onChange={() =>
                        toggleNewRiskFactorIndicator(option.value)
                      }
                    />
                    {option.label}
                  </label>
                ))}
              </div>
            </div>

            <div className="form-group">
              <label>Rationale (required)</label>
              <textarea aria-label="Rationale (required)"
                rows={3}
                value={newRiskFactor.rationale}
                onChange={(event) =>
                  setNewRiskFactor((current) => ({
                    ...current,
                    rationale: event.target.value,
                  }))
                }
                placeholder="Why does this category/these indicators apply?"
              />
            </div>

            <div className="form-group">
              <label>Misuse Scenario (optional)</label>
              <textarea aria-label="Misuse Scenario (optional)"
                rows={3}
                value={newRiskFactor.misuse_scenario}
                onChange={(event) =>
                  setNewRiskFactor((current) => ({
                    ...current,
                    misuse_scenario: event.target.value,
                  }))
                }
                placeholder="How could this be misused for financial crime?"
              />
            </div>

            <div className="form-actions">
              <button
                type="button"
                className="primary-button"
                onClick={handleAddRiskFactor}
                disabled={savingRiskFactor}
              >
                {savingRiskFactor ? "Saving..." : "Save Risk Factor"}
              </button>
            </div>
          </div>
        )}

        {riskFactors.length > 0 && canRunPipeline && (
          <div className="form-actions" style={{ justifyContent: "flex-start" }}>
            <button
              type="button"
              className="doc-action-button"
              onClick={handleSuggestRatings}
              disabled={suggestingRatings}
            >
              {suggestingRatings
                ? "Asking AI..."
                : "Suggest Ratings with AI"}
            </button>
          </div>
        )}

        {suggestNotice && (
          <p className="risk-reason" style={{ color: "#1d4ed8" }}>
            {suggestNotice}
          </p>
        )}

        {/* Rate every factor and save once; also shows the indicative score. */}
        <RatingsPanel
          assessmentId={assessmentState.id}
          factors={riskFactors}
          categoryLabel={(category) =>
            RISK_CATEGORIES.find((option) => option.value === category)?.label ?? category
          }
          canEdit={canRunPipeline}
          onSaved={refreshRiskFactors}
        />

        {riskFactors.length === 0 ? (
          <div className="risk-empty">
            <h3>No risk factors identified yet</h3>
            <p>
              Risk factors are identified automatically when inherent risk
              analysis runs, or can be added manually above.
            </p>
          </div>
        ) : (
          <div className="risk-result-list">
            {riskFactors.map((factor) => {
              const categoryLabel =
                RISK_CATEGORIES.find(
                  (option) => option.value === factor.category
                )?.label ?? factor.category;

              return (
                <div
                  className="risk-result-card"
                  key={factor.id}
                  style={factor.excluded ? { opacity: 0.6 } : undefined}
                >
                  <div className="risk-result-header">
                    <div>
                      <h3>{categoryLabel}</h3>
                      <span>
                        {factor.source === "MANUAL"
                          ? `Added manually${
                              factor.added_by ? ` by ${factor.added_by}` : ""
                            }`
                          : factor.source === "RULES"
                          ? "Rule engine (AI unavailable)"
                          : "AI Identified"}
                      </span>{" "}
                      <EvidenceStatusBadge status={factor.evidence_status} />
                      {factor.category === "CONTROL_ENVIRONMENT_RISK" && (
                        <span
                          className="risk-reason"
                          style={{ display: "block", margin: "4px 0 0" }}
                        >
                          Assessed as a control mitigant: it informs the
                          controls stage and is not part of the inherent score.
                        </span>
                      )}
                    </div>

                    <div
                      className={`risk-score-badge ${
                        factor.excluded
                          ? "low"
                          : factor.applicable
                          ? "high"
                          : "low"
                      }`}
                    >
                      <span aria-hidden="true">
                        {factor.excluded ? "⊘ " : factor.applicable ? "▲ " : "○ "}
                      </span>
                      {factor.excluded
                        ? "EXCLUDED"
                        : factor.applicable
                        ? "APPLIES"
                        : "NOT APPLICABLE"}
                    </div>
                  </div>

                  {factor.applicable && !factor.excluded && (
                    <p className="risk-reason">
                      {factor.likelihood != null && factor.impact != null ? (
                        <>
                          <strong>Rating: </strong>
                          Likelihood {factor.likelihood} x Impact {factor.impact}
                          {" -> "}score {factor.score.toFixed(0)} ({factor.severity})
                          {factor.rated_by && ` — rated by ${factor.rated_by}`}
                          {factor.rating_source === "ANALYST_OVERRIDE" && (
                            <>
                              {" "}
                              <em>
                                (overrode the AI suggestion of{" "}
                                {factor.ai_suggested_likelihood} x{" "}
                                {factor.ai_suggested_impact})
                              </em>
                            </>
                          )}
                          {factor.rating_source === "ANALYST_CONFIRMED" && (
                            <> <em>(confirmed the AI's suggestion)</em></>
                          )}
                        </>
                      ) : (
                        <>
                          {factor.ai_suggested_likelihood != null &&
                            factor.ai_suggested_impact != null && (
                              <>
                                <strong>AI suggests: </strong>
                                Likelihood {factor.ai_suggested_likelihood} x
                                Impact {factor.ai_suggested_impact}
                                {factor.ai_suggestion_rationale &&
                                  ` — ${factor.ai_suggestion_rationale}`}
                                <br />
                              </>
                            )}
                          <strong style={{ color: "#b45309" }}>
                            Not yet rated — a suggestion is not a rating. An
                            analyst must confirm or override it before the
                            inherent risk calculation can be finalised.
                          </strong>
                        </>
                      )}
                    </p>
                  )}

                  {factor.indicators.length > 0 && (
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 6, margin: "8px 0" }}>
                      {factor.indicators.map((indicator) => (
                        <span key={indicator} className="parsed-badge">
                          {RISK_INDICATORS.find(
                            (option) => option.value === indicator
                          )?.label ?? indicator}
                        </span>
                      ))}
                    </div>
                  )}

                  <p className="risk-reason">
                    <strong>Rationale: </strong>
                    {factor.rationale}
                  </p>

                  {/* P4: fixed Stage 4 rules, and the analyst's own indicator assessment (R4.2). */}
                  <FactorRuleTriggers factor={factor} />
                  <FactorIndicatorEditor
                    assessmentId={assessmentState.id}
                    factor={factor}
                    canEdit={canRunPipeline}
                    onSaved={refreshRiskFactors}
                  />

                  <FactorEvidence
                    factor={factor}
                    indicatorLabel={(code) =>
                      RISK_INDICATORS.find((option) => option.value === code)
                        ?.label ?? code
                    }
                  />

                  {/* R5.1/R5.3: approved policy and regulatory references. */}
                  <FactorSourceEvidencePanel
                    assessmentId={assessmentState.id}
                    riskFactorId={factor.id}
                    canAttach={canRunPipeline}
                  />

                  {factor.misuse_scenario && (
                    <p className="risk-reason">
                      <strong>Potential misuse: </strong>
                      {factor.misuse_scenario}
                    </p>
                  )}

                  {factor.applicable && !factor.excluded && (
                    ratingFactorId === factor.id ? (
                      <div className="form-group">
                        <label>
                          Rate this factor — your rating is what counts
                          {factor.ai_suggested_likelihood != null &&
                            factor.likelihood == null &&
                            " (pre-filled from the AI's suggestion)"}
                        </label>
                        <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
                          <label style={{ display: "flex", flexDirection: "column" }}>
                            Likelihood
                            <select
                              value={ratingInputs.likelihood}
                              onChange={(event) =>
                                setRatingInputs((current) => ({
                                  ...current,
                                  likelihood: Number(event.target.value),
                                }))
                              }
                            >
                              {[1, 2, 3, 4, 5].map((value) => (
                                <option key={value} value={value}>
                                  {value}
                                </option>
                              ))}
                            </select>
                          </label>
                          <label style={{ display: "flex", flexDirection: "column" }}>
                            Impact
                            <select
                              value={ratingInputs.impact}
                              onChange={(event) =>
                                setRatingInputs((current) => ({
                                  ...current,
                                  impact: Number(event.target.value),
                                }))
                              }
                            >
                              {[1, 2, 3, 4, 5].map((value) => (
                                <option key={value} value={value}>
                                  {value}
                                </option>
                              ))}
                            </select>
                          </label>
                        </div>
                        {/* R10.3: departing from the AI's suggestion needs a reason. */}
                        {factor.ai_suggested_likelihood != null &&
                          factor.ai_suggested_impact != null &&
                          (ratingInputs.likelihood !== factor.ai_suggested_likelihood ||
                            ratingInputs.impact !== factor.ai_suggested_impact) && (
                            <label style={{ display: "flex", flexDirection: "column", marginTop: 8 }}>
                              Reason for differing from the AI suggestion (likelihood{" "}
                              {factor.ai_suggested_likelihood}, impact {factor.ai_suggested_impact}) — required
                              <textarea
                                rows={2}
                                value={ratingInputs.reason}
                                onChange={(event) =>
                                  setRatingInputs((current) => ({
                                    ...current,
                                    reason: event.target.value,
                                  }))
                                }
                              />
                            </label>
                          )}
                        <div className="form-actions">
                          <button
                            type="button"
                            className="secondary-button"
                            onClick={() => setRatingFactorId(null)}
                            disabled={ratingLoading}
                          >
                            Cancel
                          </button>
                          <button
                            type="button"
                            className="primary-button"
                            onClick={() => handleRateRiskFactor(factor.id)}
                            disabled={ratingLoading}
                          >
                            {ratingLoading ? "Saving..." : "Save Rating"}
                          </button>
                        </div>
                      </div>
                    ) : (
                      <button
                        type="button"
                        className="doc-action-button"
                        onClick={() => {
                          setRatingFactorId(factor.id);
                          setRatingInputs({
                            likelihood:
                              factor.likelihood ??
                              factor.ai_suggested_likelihood ??
                              3,
                            impact:
                              factor.impact ??
                              factor.ai_suggested_impact ??
                              3,
                            reason: "",
                          });
                          setRiskFactorError(null);
                        }}
                      >
                        {factor.likelihood != null ? "Re-rate" : "Rate Factor"}
                      </button>
                    )
                  )}

                  {factor.excluded ? (
                    <p className="risk-reason">
                      <strong>Excluded — </strong>
                      {factor.exclusion_reason}
                      {factor.excluded_by && ` (by ${factor.excluded_by})`}
                    </p>
                  ) : excludingFactorId === factor.id ? (
                    <div className="form-group">
                      <label>Reason for exclusion (required)</label>
                      <textarea aria-label="Reason for exclusion (required)"
                        rows={2}
                        value={exclusionReason}
                        onChange={(event) =>
                          setExclusionReason(event.target.value)
                        }
                      />
                      <div className="form-actions">
                        <button
                          type="button"
                          className="secondary-button"
                          onClick={() => {
                            setExcludingFactorId(null);
                            setExclusionReason("");
                          }}
                          disabled={excludingLoading}
                        >
                          Cancel
                        </button>
                        <button
                          type="button"
                          className="primary-button"
                          onClick={() => handleExcludeRiskFactor(factor.id)}
                          disabled={excludingLoading}
                        >
                          {excludingLoading ? "Saving..." : "Confirm Exclude"}
                        </button>
                      </div>
                    </div>
                  ) : (
                    <button
                      type="button"
                      className="doc-action-button"
                      onClick={() => {
                        setExcludingFactorId(factor.id);
                        setExclusionReason("");
                        setRiskFactorError(null);
                      }}
                    >
                      Exclude
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        )}

      </section>

      <OccRiskProfilePanel
        assessmentId={assessmentState.id}
        riskFactors={riskFactors}
      />

      {inherentRiskCalc && (
        <section className="risk-results-section">
          <h2>Inherent Risk Calculation (Stage 6)</h2>
          <p className="risk-reason">
            Deterministic, system-calculated result — configured factor
            weights applied to each factor's score, which comes solely from
            the analyst's own likelihood x impact rating. The AI may suggest
            a rating to start from, but a suggestion never feeds this
            calculation until an analyst confirms or overrides it, and every
            calculation here is done by the system.
          </p>

          {inherentRiskCalc.is_provisional && (
            <p className="risk-reason" style={{ color: "#b45309" }}>
              <strong>Provisional — </strong>
              one or more applicable, non-excluded factors still need a
              manual likelihood/impact rating before this result can be
              finalized.
            </p>
          )}

          {inherentRiskCalc.final_score === null && (
            <p className="risk-reason" style={{ color: "#b45309" }}>
              <strong>No score yet — </strong>
              none of the {inherentRiskCalc.applicable_factor_count} applicable
              factors has been rated. Unrated factors are left out of the
              score, never counted as zero.
            </p>
          )}

          {inherentRiskCalc.escalated &&
            (inherentRiskCalc.triggered_rules?.length ? (
              <div role="alert" style={{ color: "#b91c1c", margin: "8px 0" }}>
                <p className="risk-reason" style={{ color: "#b91c1c", margin: "0 0 4px" }}>
                  <strong>Policy rules triggered</strong>
                  {inherentRiskCalc.mandatory_review && " — mandatory human review required"}
                </p>
                <ul style={{ margin: 0, paddingLeft: 18 }}>
                  {inherentRiskCalc.triggered_rules.map((rule) => (
                    <li key={rule.rule_code} className="risk-reason" style={{ color: "#b91c1c" }}>
                      <strong>{rule.rule_code}</strong> (v{rule.version},{" "}
                      {rule.rule_type === "override" ? "override" : "minimum band"}):{" "}
                      {rule.description} Triggered by{" "}
                      {rule.triggered_by
                        .map(
                          (category) =>
                            RISK_CATEGORIES.find((option) => option.value === category)
                              ?.label ?? category
                        )
                        .join(", ")}
                      .{" "}
                      {rule.raised_band
                        ? `Band ${rule.band_before ?? "unrated"} → ${rule.band_after}.`
                        : `Band already ${rule.band_after}.`}
                    </li>
                  ))}
                </ul>
              </div>
            ) : (
              <p className="risk-reason" role="alert" style={{ color: "#b91c1c" }}>
                <strong>Mandatory escalation triggered — </strong>
                {inherentRiskCalc.escalation_reasons.join(" ")}
              </p>
            ))}

          <div style={{ display: "flex", gap: 24, flexWrap: "wrap", margin: "12px 0" }}>
            <div>
              <div style={{ fontSize: 13, color: "#6b7280" }}>Final Score</div>
              <div style={{ fontSize: 24, fontWeight: 700 }}>
                {inherentRiskCalc.final_score === null
                  ? "—"
                  : inherentRiskCalc.final_score.toFixed(1)}
              </div>
            </div>
            <div>
              <div style={{ fontSize: 13, color: "#6b7280" }}>Risk Band</div>
              <div style={{ fontSize: 24, fontWeight: 700 }}>
                <RiskLevelIcon level={inherentRiskCalc.risk_band} />{" "}
                {inherentRiskCalc.risk_band}
              </div>
            </div>
            {inherentRiskCalc.overridden && (
              <div>
                <div style={{ fontSize: 13, color: "#6b7280" }}>Overridden To</div>
                <div style={{ fontSize: 24, fontWeight: 700 }}>
                  {inherentRiskCalc.override_value?.toFixed(1)} (
                  {inherentRiskCalc.override_band})
                </div>
              </div>
            )}
          </div>

          <table className="risk-table" style={{ width: "100%", marginBottom: 12 }}>
            <thead>
              <tr>
                <th>Category</th>
                <th>Weight</th>
                <th>Likelihood</th>
                <th>Impact</th>
                <th>Score</th>
                <th>Rated</th>
                <th>Evidence</th>
              </tr>
            </thead>
            <tbody>
              {inherentRiskCalc.inputs.map((row) => (
                <tr key={row.category}>
                  <td>
                    {RISK_CATEGORIES.find((option) => option.value === row.category)
                      ?.label ?? row.category}
                  </td>
                  <td>{(row.weight * 100).toFixed(0)}%</td>
                  <td>{row.likelihood ?? "—"}</td>
                  <td>{row.impact ?? "—"}</td>
                  <td>{row.rated ? row.score.toFixed(1) : "— (not counted)"}</td>
                  <td>{row.rated ? "Yes" : "No"}</td>
                  <td>
                    {row.evidence_status ? (
                      <EvidenceStatusBadge status={row.evidence_status} />
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <p className="risk-reason">
            Method: {inherentRiskCalc.calculation_method} · Methodology:{" "}
            {inherentRiskCalc.methodology_name ?? "Default"} · Calculated by{" "}
            {inherentRiskCalc.calculated_by ?? "System"} on{" "}
            {new Date(inherentRiskCalc.calculated_at).toLocaleString()}
          </p>

          <CalculationProvenance calc={inherentRiskCalc} />

          <InherentRiskHistory calc={inherentRiskCalc} />

          {inherentRiskCalc.overridden && (
            <p className="risk-reason">
              <strong>Override reason — </strong>
              {inherentRiskCalc.override_reason} (by{" "}
              {inherentRiskCalc.override_by}, on{" "}
              {inherentRiskCalc.override_at &&
                new Date(inherentRiskCalc.override_at).toLocaleString()}
              )
            </p>
          )}

          {canRunPipeline && (
            overridingInherentRisk ? (
              <div className="form-group">
                <label>Override the calculated rating (reason required)</label>
                <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
                  <label style={{ display: "flex", flexDirection: "column" }}>
                    Score (0-100)
                    <input
                      type="number"
                      min={0}
                      max={100}
                      value={overrideInputs.value}
                      onChange={(event) =>
                        setOverrideInputs((current) => ({
                          ...current,
                          value: Number(event.target.value),
                        }))
                      }
                    />
                  </label>
                  <label style={{ display: "flex", flexDirection: "column" }}>
                    Band (optional)
                    <select
                      value={overrideInputs.band}
                      onChange={(event) =>
                        setOverrideInputs((current) => ({
                          ...current,
                          band: event.target.value,
                        }))
                      }
                    >
                      <option value="">Auto (from score)</option>
                      {inherentRiskCalc.risk_bands.map((band) => (
                        <option key={band.name} value={band.name}>
                          {band.name}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                <textarea aria-label="Reason for overriding the calculated rating"
                  rows={2}
                  placeholder="Reason for overriding the calculated rating"
                  value={overrideInputs.reason}
                  onChange={(event) =>
                    setOverrideInputs((current) => ({
                      ...current,
                      reason: event.target.value,
                    }))
                  }
                />
                {overrideError && (
                  <p className="risk-reason" role="alert" style={{ color: "#b91c1c" }}>
                    {overrideError}
                  </p>
                )}
                <div className="form-actions">
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={() => setOverridingInherentRisk(false)}
                    disabled={overrideLoading}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    className="primary-button"
                    onClick={handleOverrideInherentRisk}
                    disabled={overrideLoading}
                  >
                    {overrideLoading ? "Saving..." : "Apply Override"}
                  </button>
                </div>
              </div>
            ) : (
              <button
                type="button"
                className="doc-action-button"
                onClick={() => {
                  setOverridingInherentRisk(true);
                  setOverrideError(null);
                  setOverrideInputs({
                    value: inherentRiskCalc.final_score ?? 0,
                    band: "",
                    reason: "",
                  });
                }}
              >
                Override Calculated Rating
              </button>
            )
          )}
        </section>
      )}

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
          <RiskLevelIcon level={assessmentState.risk_level} />{" "}
          {assessmentState.risk_level ||
            "NOT ANALYZED"}
        </div>

        <p>
          System-calculated from analyst likelihood x impact ratings of
          the applicable risk factors. Unrated factors are not counted.
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

              <strong
                aria-label={isUnratedResult(result.dimension) ? "Not yet rated" : undefined}
                title={isUnratedResult(result.dimension) ? "Not yet rated by an analyst" : undefined}
              >
                {isUnratedResult(result.dimension) ? "—" : result.score}
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

        {(assessmentState.status === "RISK_IDENTIFICATION" ||
          assessmentState.status === "INHERENT_RISK_ASSESSMENT") && (
          <div style={{ marginTop: 16 }}>
            {advanceError && (
              <p role="alert" style={{ color: "#b91c1c" }}>{advanceError}</p>
            )}
            <button
              className="primary-button"
              onClick={() => handleAdvanceStage().catch(() => undefined)}
              disabled={advanceLoading}
            >
              {advanceLoading
                ? "Advancing..."
                : assessmentState.status === "RISK_IDENTIFICATION"
                ? "Confirm Risk Identification & Continue"
                : "Approve Inherent Risk & Continue to Controls"}
            </button>
          </div>
        )}

      </section>

    </aside>

  </div>
) : currentStep === 4 || currentStep === 5 ? (
<>
  <ControlsStage
    assessment={assessmentState}
    riskFactors={riskFactors}
    controlSummary={controlSummary}
    onControlsChanged={refreshControlSummary}
    onAdvance={() => handleAdvanceStage().catch(() => undefined)}
    advanceLoading={advanceLoading}
    advanceError={advanceError}
    canRunPipeline={canRunPipeline}
  />
{maxStepReached >= 5 ? (
  <ResidualRiskStage
    assessment={assessmentState}
    riskFactors={riskFactors}
    controlSummary={controlSummary}
    onAdvance={() => handleAdvanceStage().catch(() => undefined)}
    advanceLoading={advanceLoading}
    advanceError={advanceError}
    canRunPipeline={canRunPipeline}
  />
) : (
  <p className="step-locked">Residual risk opens once the control assessment is complete.</p>
)}
</>
) : currentStep === 6 ? (
  <>
  <AssessmentDraftPanel
    key={assessmentState.id}
    assessmentId={assessmentState.id}
    canEdit={canRunPipeline}
    currentUserEmail={user.email}
  />
  <FcrmReviewStage
    assessment={assessmentState}
    riskResults={riskResults}
    controlSummary={controlSummary}
    onSubmitForReview={handleSubmitForCommitteeReview}
    submitLoading={submitReviewLoading}
    submitError={submitReviewError}
    canRunPipeline={canRunPipeline}
    onAssessmentUpdated={(updated) => {
      setAssessmentState(updated);
      getAssessmentAudit(updated.id).then(setAuditEvents).catch(console.error);
    }}
    currentUserId={user.id}
    currentUserRole={user.role}
  />
  </>
) : currentStep === 7 ? (
  <ApprovalStage
    assessment={assessmentState}
    user={user}
    onDecisionRecorded={() => {
      getAssessment(assessmentState.id)
        .then((updated) => {
          setAssessmentState(updated);
          getAssessmentAudit(updated.id).then(setAuditEvents).catch(console.error);
        })
        .catch(console.error);
    }}
    managerContent={
      <>
  {/* The manager returned it: the owner reads the feedback and resubmits. */}
  <ReturnedByManagerPanel
    assessment={assessmentState}
    user={user}
    onResubmit={handleSubmitForCommitteeReview}
    onEditRequest={onEditDraft ? () => onEditDraft(assessmentState) : undefined}
    submitting={submitReviewLoading}
    error={submitReviewError}
  />
  {/* The assigned manager (or a delegate) decides here, next to the findings. */}
  <ManagerDecisionPanel
    assessment={assessmentState}
    user={user}
    onDecisionRecorded={() => {
      getAssessment(assessmentState.id)
        .then((updated) => {
          setAssessmentState(updated);
          getAssessmentAudit(updated.id).then(setAuditEvents).catch(console.error);
        })
        .catch(console.error);
    }}
    afterChecklist={
      <details
        className="apv-details"
        key={MANAGER_PHASE_STATUSES.includes(assessmentState.status) ? "open" : "closed"}
        open={MANAGER_PHASE_STATUSES.includes(assessmentState.status)}
      >
        <summary>Challenge review findings</summary>
        <ChallengeReviewStage
          assessmentId={assessmentState.id}
          canEdit={canRunPipeline}
          // G-4: accepting (MEDIUM only) is a documented Committee exception;
          // the server checks the member's eligibility and the case's stage.
          canAccept={user.role === "COMMITTEE_MEMBER"}
        />
      </details>
    }
  />
      </>
    }
    statusContent={
  <ApprovalStatusStage
    assessment={assessmentState}
    auditEvents={auditEvents}
    canAmend={user.role === "COMMITTEE_MEMBER" || user.role === "ADMIN"}
    currentUserEmail={user.email}
    currentUserRole={user.role}
    onAmended={(updated) => {
      setAssessmentState(updated);
      getAssessmentAudit(updated.id).then(setAuditEvents).catch(console.error);
    }}
  />
    }
  />
) :  (
  <div className="workflow-placeholder">
    <h2>
      {stepLabel(currentStep)}
    </h2>

    <p>
      This assessment stage will be implemented next.
    </p>
  </div>
)}
        {/* P5: retention runs from the final decision, so it sits on the Decision step. */}
        {isDecided && <RetentionHoldPanel assessmentId={assessmentState.id} />}
        {/* P6 (Stage 18): a reassessment is proposed once an assessment is decided (Decision step),
            and a reassessment's own Intake shows its carried-over fields and the comparison with its parent. */}
        {(isDecided || (currentStep <= 2 && assessmentState.parent_assessment_id != null)) && (
          <ReassessmentPanel assessmentId={assessmentState.id} />
        )}
      </div>

    </div>
  );
}


interface MetadataRowProps {
  label: string;
  values: string[];
}

// ---------------------------------------------------------------------------
// The nine OCC supervisory risk categories (OCC NR 96-2a, "Categories of
// Risk") as a read-only lens over the factors rated above. Nothing here is
// editable and nothing here feeds the assessment's score -- it re-expresses
// the same rated factors in the supervisory taxonomy, so a reader can ask
// "what does this change do to our reputation risk?" without re-rating
// anything. Refetches whenever the factor list changes.
// ---------------------------------------------------------------------------

function OccRiskProfilePanel({
  assessmentId,
  riskFactors,
}: {
  assessmentId: number;
  riskFactors: RiskFactor[];
}) {
  const [profile, setProfile] = useState<OccRiskProfile | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    let cancelled = false;

    getOccRiskProfile(assessmentId)
      .then((result) => {
        if (!cancelled) {
          setProfile(result);
          setError(null);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setError("Could not load the OCC risk profile.");
        }
      });

    return () => {
      cancelled = true;
    };
  }, [assessmentId, riskFactors]);

  if (error) {
    return (
      <section className="risk-results-section">
        <h2>OCC Supervisory Risk Categories</h2>
        <p className="risk-reason">{error}</p>
      </section>
    );
  }

  if (!profile) {
    return null;
  }

  const rated = profile.categories.filter((entry) => entry.score !== null);

  return (
    <section className="risk-results-section">
      <h2>OCC Supervisory Risk Categories</h2>

      <p className="risk-reason">
        The same risk factors, re-expressed in the nine supervisory
        categories the OCC defines. Derived automatically from the ratings
        above — nothing here is rated separately, and none of it changes
        the assessment's score. The OCC notes these categories are not
        mutually exclusive, so one factor can appear under several of them
        and the scores are not meant to sum.
      </p>

      {profile.is_provisional && (
        <p className="risk-reason" style={{ color: "#b45309" }}>
          <strong>Provisional — </strong>
          some contributing factors are not yet rated by an analyst, so the
          categories they feed are incomplete.
        </p>
      )}

      {rated.length === 0 ? (
        <div className="risk-empty">
          <h3>Nothing to roll up yet</h3>
          <p>
            Once applicable risk factors are identified and rated, they will
            appear here grouped by OCC category.
          </p>
        </div>
      ) : (
        <table className="risk-table" style={{ width: "100%", marginBottom: 12 }}>
          <thead>
            <tr>
              <th>OCC Category</th>
              <th>Band</th>
              <th>Score</th>
              <th>Highest Factor</th>
              <th>Contributing Factors</th>
            </tr>
          </thead>
          <tbody>
            {profile.categories.map((entry) => (
              <tr
                key={entry.occ_category}
                style={entry.covered ? undefined : { opacity: 0.55 }}
              >
                <td title={entry.definition}>
                  {entry.label}
                  {entry.is_provisional && (
                    <span style={{ color: "#b45309" }}> *</span>
                  )}
                </td>
                <td>
                  {entry.risk_band ? (
                    <>
                      <RiskLevelIcon level={entry.risk_band} />{" "}
                      {entry.risk_band}
                    </>
                  ) : (
                    "—"
                  )}
                </td>
                <td>{entry.score !== null ? entry.score.toFixed(1) : "—"}</td>
                <td>
                  {entry.max_factor_score !== null
                    ? entry.max_factor_score.toFixed(1)
                    : "—"}
                </td>
                <td>
                  {/* An uncovered category and a covered-but-empty one mean
                      different things, and both differ from a score of 0. */}
                  {!entry.covered
                    ? "Not measured by this assessment's taxonomy"
                    : entry.factor_count === 0
                    ? "None applicable"
                    : expanded
                    ? entry.contributing_factors
                        .map((factor) => riskCategoryLabel(factor.category))
                        .join(", ")
                    : `${entry.factor_count} factor${
                        entry.factor_count === 1 ? "" : "s"
                      }`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {rated.length > 0 && (
        <button
          type="button"
          className="doc-action-button"
          onClick={() => setExpanded((value) => !value)}
        >
          {expanded ? "Hide contributing factors" : "Show contributing factors"}
        </button>
      )}

      {profile.uncovered_categories.length > 0 && (
        <p className="risk-reason">
          <strong>Not covered: </strong>
          {profile.uncovered_categories
            .map(
              (category) =>
                profile.categories.find(
                  (entry) => entry.occ_category === category
                )?.label ?? category
            )
            .join(", ")}
          . No category in this assessment's taxonomy measures repricing or
          mark-to-market exposure, so these are shown as unmeasured rather
          than as a score of zero.
        </p>
      )}

      <p className="risk-reason">{profile.source}</p>
    </section>
  );
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
  riskFactors: RiskFactor[];
  controlSummary: AssessmentControlSummary | null;
  onControlsChanged: () => Promise<void>;
  onAdvance: () => Promise<void>;
  advanceLoading: boolean;
  advanceError: string | null;
  canRunPipeline: boolean;
}

function ControlsStage({
  assessment,
  riskFactors,
  controlSummary,
  onControlsChanged,
  onAdvance,
  advanceLoading,
  advanceError,
  canRunPipeline,
}: ControlsStageProps) {
  const controlReduction = controlSummary?.control_reduction ?? 0;

  const inherentRisk =
    assessment.inherent_score ?? assessment.overall_score ?? 0;

  // The backend's residual-grid result (inherent band x control rating,
  // held up by any non-mitigable rule), not a client-side subtraction.
  const residualState = useResidualRisk(assessment.id, controlSummary);
  const residualCalc = residualState.residual;
  const residualRisk = residualCalc?.residual_score ?? null;

  // The AI's view of each control's effectiveness, taken from evidence the
  // analyst has accepted. Used only to pre-fill the "Assess control" form;
  // the analyst still confirms the rating, so nothing scores without them.
  const [aiEffectiveness, setAiEffectiveness] = useState<Record<number, string>>({});
  useEffect(() => {
    let cancelled = false;
    listEvidenceLinks(assessment.id)
      .then((links) => {
        if (cancelled) return;
        const next: Record<number, string> = {};
        links.forEach((link) => {
          if (link.status === "ACCEPTED" && link.suggested_effectiveness) {
            next[link.control_id] = link.suggested_effectiveness;
          }
        });
        setAiEffectiveness(next);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [assessment.id, controlSummary]);

  const applicableRiskFactors = riskFactors.filter(
    (factor) => factor.applicable && !factor.excluded
  );

  const controlsByRiskFactor = new Map<number, Control[]>();
  (controlSummary?.controls ?? []).forEach((control) => {
    const list = controlsByRiskFactor.get(control.risk_factor_id) ?? [];
    list.push(control);
    controlsByRiskFactor.set(control.risk_factor_id, list);
  });

  const gapsByRiskFactor = new Map<number, ControlGap[]>();
  (controlSummary?.gaps ?? []).forEach((gap) => {
    if (gap.risk_factor_id == null) {
      return;
    }
    const list = gapsByRiskFactor.get(gap.risk_factor_id) ?? [];
    list.push(gap);
    gapsByRiskFactor.set(gap.risk_factor_id, list);
  });

  const statusCounts: Record<ControlDisplayStatus, number> = {
    EFFECTIVE: 0,
    PARTIALLY_EFFECTIVE: 0,
    INEFFECTIVE: 0,
    UNVERIFIED: 0,
    NOT_ASSESSED: 0,
  };
  (controlSummary?.controls ?? []).forEach((control) => {
    const status = controlDisplayStatus(
      controlSummary?.control_assessments[control.id]
    );
    statusCounts[status] += 1;
  });

  const totalControls = controlSummary?.controls.length ?? 0;
  const controlsNotCredited =
    statusCounts.UNVERIFIED + statusCounts.NOT_ASSESSED + statusCounts.INEFFECTIVE;
  // The residual grid's weakest-risk control rating when it has loaded;
  // otherwise "no mapped control earns any credit".
  const controlsRatedWeak = residualCalc
    ? residualCalc.control_rating === "WEAK"
    : totalControls > 0 && controlsNotCredited === totalControls;

  const openConditions = (controlSummary?.conditions ?? []).filter(
    (condition) => condition.status !== "COMPLETED" && condition.status !== "CANCELLED"
  );

  const residualRiskLevel = residualCalc?.residual_band ?? "PENDING";

  // Recording an outcome/comment for this control assessment via
  // PATCH /{assessment_id}/challenge. The backend requires this to be
  // saved before advance-stage will move CONTROL_ASSESSMENT ->
  // RESIDUAL_RISK, so this small form is what unblocks that button.
  const [outcome, setOutcome] = useState("ACCEPTED");
  const [comment, setComment] = useState("");
  const [savingOutcome, setSavingOutcome] = useState(false);
  const [outcomeSaved, setOutcomeSaved] = useState(false);
  const [outcomeError, setOutcomeError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    getAssessmentChallenge(assessment.id)
      .then((challenge) => {
        if (cancelled) {
          return;
        }
        if (challenge.outcome) {
          setOutcome(challenge.outcome);
        }
        if (challenge.comment) {
          setComment(challenge.comment);
        }
        setOutcomeSaved(Boolean(challenge.outcome && challenge.comment));
      })
      .catch(() => {
        // No prior outcome saved yet; keep the form defaults.
      });

    return () => {
      cancelled = true;
    };
  }, [assessment.id]);

  async function handleSaveOutcome() {
    if (!canRunPipeline) {
      setOutcomeError("Only an FCRM Analyst, Manager or Admin can record a control assessment outcome.");
      return;
    }

    if (!comment.trim()) {
      setOutcomeError("Commentary is required to record a control assessment outcome.");
      return;
    }

    if (outcome === "ACCEPTED" && controlsRatedWeak) {
      setOutcomeError(
        "Controls can't be accepted as adequate while the control rating is Weak. Rate the controls first, or choose Remediation required or Escalate."
      );
      return;
    }

    setSavingOutcome(true);
    setOutcomeError(null);

    try {
      await updateAssessmentChallenge(assessment.id, { outcome, comment });
      setOutcomeSaved(true);
    } catch (error) {
      setOutcomeError(
        error instanceof Error ? error.message : "Failed to save outcome"
      );
    } finally {
      setSavingOutcome(false);
    }
  }

  return (
    <div className="controls-layout">

      {/* LEFT SIDE */}

      <div className="controls-main">

        <section className="workflow-card">

          <div className="workflow-card-header">

            <div>
              <h2>
                Control Identification &amp; Assessment
              </h2>

              <p>
                Map controls from the library to each identified risk,
                then assess design adequacy and operating effectiveness.
              </p>
            </div>

            <span className="control-engine-badge">
              R7 CONTROL LIBRARY
            </span>

          </div>


          {applicableRiskFactors.length === 0 ? (

            <div className="controls-empty">

              <h3>
                No applicable risks available
              </h3>

              <p>
                Complete Risk Identification before mapping controls.
              </p>

            </div>

          ) : (

            <div className="control-list">

              {applicableRiskFactors.map((factor) => (
                <RiskFactorControlGroup
                  key={factor.id}
                  assessmentId={assessment.id}
                  riskFactor={factor}
                  controls={controlsByRiskFactor.get(factor.id) ?? []}
                  controlAssessments={controlSummary?.control_assessments ?? {}}
                  gaps={gapsByRiskFactor.get(factor.id) ?? []}
                  canEdit={canRunPipeline}
                  onChanged={onControlsChanged}
                  mappableFactors={applicableRiskFactors}
                  allControls={controlSummary?.controls ?? []}
                  aiEffectiveness={aiEffectiveness}
                />
              ))}

            </div>

          )}

        </section>

        <ControlEvidencePanel
          assessmentId={assessment.id}
          controls={(controlSummary?.controls ?? []).map((control) => {
            const riskFactor = riskFactors.find((factor) => factor.id === control.risk_factor_id);
            return {
              id: control.id,
              label: controlTypeLabel(control.control_type),
              riskLabel: riskFactor ? riskCategoryLabel(riskFactor.category) : undefined,
            };
          })}
          canEdit={canRunPipeline}
          onChanged={onControlsChanged}
        />

        <section className="workflow-card" style={{ marginTop: 16 }}>
          <div className="workflow-card-header">
            <div>
              <h2>Record Control Assessment Outcome</h2>
              <p>
                Confirm whether the mapped controls above adequately address
                the identified risks before moving to Residual Risk.
              </p>
            </div>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {!canRunPipeline && (
              <p style={{ color: "#667085" }}>
                Only an FCRM Analyst, Manager or Admin can record a control assessment outcome.
              </p>
            )}

            <select aria-label="Control assessment outcome"
              value={outcome}
              onChange={(event) => setOutcome(event.target.value)}
              disabled={savingOutcome || !canRunPipeline}
            >
              <option value="ACCEPTED">Accepted — controls are adequate</option>
              <option value="REMEDIATION_REQUIRED">
                Remediation required — controls are insufficient
              </option>
              <option value="ESCALATE">Escalate for further review</option>
            </select>

            {outcome === "ACCEPTED" && controlsRatedWeak && (
              <p role="status" style={{ margin: 0, fontSize: 13, color: "#b45309" }}>
                The control rating is Weak, so “controls are adequate” can't be recorded yet. Rate the controls above,
                or choose Remediation required.
              </p>
            )}

            <textarea aria-label="Commentary supporting this control assessment outcome"
              placeholder="Commentary supporting this control assessment outcome..."
              value={comment}
              onChange={(event) => setComment(event.target.value)}
              disabled={savingOutcome || !canRunPipeline}
              rows={3}
            />

            {outcomeError && <p role="alert" style={{ color: "#b91c1c" }}>{outcomeError}</p>}

            {canRunPipeline && (
              <button
                className="primary-button"
                onClick={handleSaveOutcome}
                disabled={savingOutcome}
              >
                {savingOutcome ? "Saving..." : "Save Control Assessment Outcome"}
              </button>
            )}

            {canRunPipeline && outcomeSaved && (
              <button
                className="primary-button"
                onClick={onAdvance}
                disabled={advanceLoading}
              >
                {advanceLoading ? "Advancing..." : "Approve & Continue to Residual Risk"}
              </button>
            )}

            {canRunPipeline && advanceError && <p role="alert" style={{ color: "#b91c1c" }}>{advanceError}</p>}
          </div>
        </section>

        {/* Control Conditions (R7.7) */}

        <section className="workflow-card">

          <div className="workflow-card-header">

            <div>
              <h2>
                Control Conditions
              </h2>

              <p>
                Required enhancements tracked to completion.
              </p>
            </div>

          </div>

          {openConditions.length === 0 ? (
            <p style={{ color: "#667085" }}>No open control conditions.</p>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {openConditions.map((condition) => (
                <ControlConditionRow
                  key={condition.id}
                  assessmentId={assessment.id}
                  condition={condition}
                  canEdit={canRunPipeline}
                  onChanged={onControlsChanged}
                />
              ))}
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
                Summary of control effectiveness across
                every mapped control (R7.6).
              </p>
            </div>

          </div>

          <div className="control-summary-grid">

            <ControlSummary label="Effective" value={statusCounts.EFFECTIVE} />

            <ControlSummary
              label="Partially Effective"
              value={statusCounts.PARTIALLY_EFFECTIVE}
            />

            <ControlSummary label="Ineffective" value={statusCounts.INEFFECTIVE} />

            <ControlSummary label="Unverified" value={statusCounts.UNVERIFIED} />

            <ControlSummary
              label="Not Assessed"
              value={statusCounts.NOT_ASSESSED}
            />

          </div>

        </section>

      </div>


      {/* RIGHT SIDE */}

      <aside className="controls-sidebar">

        <section className="residual-risk-card residual-risk-card-light">

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
                {controlReduction > 0 ? "-" : ""}{controlReduction.toFixed(1)}
              </strong>

            </div>


            <div className="residual-risk-result">

              <span>
                RESIDUAL RISK RESULT
              </span>

              <div>

                <strong>
                  {formatResidualScore(residualRisk)}
                </strong>

                <span
                  className={`residual-level ${residualRiskLevel.toLowerCase()}`}
                >
                  <RiskLevelIcon level={residualRiskLevel} />{" "}
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
            {(controlSummary?.gaps.length ?? 0)} open control gap
            {(controlSummary?.gaps.length ?? 0) === 1 ? "" : "s"}
          </h3>

          {controlsNotCredited > 0 && (
            <p>
              <strong>
                {controlsNotCredited} of {totalControls} mapped controls
              </strong>{" "}
              are unverified, not assessed or ineffective, so they currently
              reduce no risk. Use “Assess control” to rate them.
            </p>
          )}

          <p>
            Risks with no mapped control, unverified/ineffective controls,
            incomplete coverage, or a dependency on unavailable data all
            reduce the effectiveness of the overall risk treatment (R7.6).
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
                Risks evaluated
              </span>

              <strong>
                {controlSummary?.risk_factor_count ?? applicableRiskFactors.length}
              </strong>
            </div>

            <div>
              <span>
                Open control gaps
              </span>

              <strong>
                {controlSummary?.gaps.length ?? 0}
              </strong>
            </div>

            <div>
              <span>
                Open conditions
              </span>

              <strong>
                {openConditions.length}
              </strong>
            </div>

            <div>
              <span>
                Controls not yet credited
              </span>

              <strong>
                {controlsNotCredited} of {totalControls}
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
interface RiskFactorControlGroupProps {
  assessmentId: number;
  riskFactor: RiskFactor;
  controls: Control[];
  controlAssessments: Record<number, ControlAssessmentRecord>;
  gaps: ControlGap[];
  canEdit: boolean;
  onChanged: () => Promise<void>;
  // R7.2: the risks a control can be remapped to.
  mappableFactors: { id: number; category: string }[];
  // Every control on the assessment, so a rating can be applied to the same
  // control type wherever it is mapped.
  allControls: Control[];
  aiEffectiveness: Record<number, string>;
}

// Stage 7 (R7.1-R7.6): one identified risk, its mapped controls (each
// with an inline design/effectiveness assessment form), its open gaps,
// and a form to map a new control from the library.
function RiskFactorControlGroup({
  assessmentId,
  riskFactor,
  controls,
  controlAssessments,
  gaps,
  canEdit,
  onChanged,
  mappableFactors,
  allControls,
  aiEffectiveness,
}: RiskFactorControlGroupProps) {
  const [addingControl, setAddingControl] = useState(false);
  const [controlType, setControlType] = useState(CONTROL_LIBRARY[0].value);
  const [owner, setOwner] = useState("");
  const [department, setDepartment] = useState("");
  const [frequency, setFrequency] = useState("");
  const [trigger, setTrigger] = useState("");
  const [scope, setScope] = useState("");
  const [operatingStatus, setOperatingStatus] = useState("ACTIVE");
  const [evidenceSource, setEvidenceSource] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleAddControl() {
    if (!canEdit) {
      setError("Only an FCRM Analyst, Manager or Admin can map controls.");
      return;
    }

    setSaving(true);
    setError(null);

    try {
      await addControl(assessmentId, {
        risk_factor_id: riskFactor.id,
        control_type: controlType,
        owner: owner || null,
        performing_department: department || null,
        frequency: frequency || null,
        trigger: trigger || null,
        scope: scope || null,
        operating_status: operatingStatus,
        evidence_source: evidenceSource || null,
      });
      setAddingControl(false);
      setOwner("");
      setDepartment("");
      setFrequency("");
      setTrigger("");
      setScope("");
      setOperatingStatus("ACTIVE");
      setEvidenceSource("");
      await onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add control");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="control-item" style={{ flexDirection: "column", alignItems: "stretch" }}>
      <div className="control-item-main">
        <h3>{riskCategoryLabel(riskFactor.category)}</h3>
        <p>
          Severity: <strong>{riskFactor.severity}</strong>
          {controls.length === 0 && " • No control mapped"}
        </p>
      </div>

      {riskFactor.category === "CONTROL_ENVIRONMENT_RISK" && (
        <p style={{ margin: "4px 0 0", fontSize: 12, color: "#667085" }}>
          The control environment is what the other controls are measured against. These controls are recorded
          here but are not scored as a separate risk in the residual calculation.
        </p>
      )}

      {gaps.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 4 }}>
          {gaps.map((gap) => (
            <span key={gap.id} className="control-status not-validated">
              {gapTypeLabel(gap.gap_type)}
            </span>
          ))}
        </div>
      )}

      {controls.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 8 }}>
          {controls.map((control) => (
            <ControlRow
              key={control.id}
              assessmentId={assessmentId}
              control={control}
              currentAssessment={controlAssessments[control.id]}
              canEdit={canEdit}
              onChanged={onChanged}
              mappableFactors={mappableFactors}
              siblings={allControls
                .filter((other) => other.control_type === control.control_type && other.id !== control.id)
                .map((other) => ({ control: other, current: controlAssessments[other.id] }))}
              aiEffectiveness={aiEffectiveness[control.id]}
            />
          ))}
        </div>
      )}

      {canEdit && (
        <div style={{ marginTop: 8 }}>
          {!addingControl ? (
            <button className="secondary-button" onClick={() => setAddingControl(true)}>
              + Map a control
            </button>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 4 }}>
              <select aria-label="Control type" value={controlType} onChange={(e) => setControlType(e.target.value)}>
                {CONTROL_LIBRARY.map((entry) => (
                  <option key={entry.value} value={entry.value}>
                    {entry.label}
                  </option>
                ))}
              </select>
              <input aria-label="Control owner"
                placeholder="Control owner"
                value={owner}
                onChange={(e) => setOwner(e.target.value)}
              />
              <input aria-label="Performing department"
                placeholder="Performing department"
                value={department}
                onChange={(e) => setDepartment(e.target.value)}
              />
              <input aria-label="Frequency (e.g. daily, per-transaction)"
                placeholder="Frequency (e.g. daily, per-transaction)"
                value={frequency}
                onChange={(e) => setFrequency(e.target.value)}
              />
              <input aria-label="Trigger (what starts the control)"
                placeholder="Trigger (what starts the control)"
                value={trigger}
                onChange={(e) => setTrigger(e.target.value)}
              />
              <input aria-label="Scope (what the control covers)"
                placeholder="Scope (what the control covers)"
                value={scope}
                onChange={(e) => setScope(e.target.value)}
              />
              <input aria-label="Evidence source"
                placeholder="Evidence source"
                value={evidenceSource}
                onChange={(e) => setEvidenceSource(e.target.value)}
              />
              <label>
                Operating status
                <select value={operatingStatus} onChange={(e) => setOperatingStatus(e.target.value)}>
                  {CONTROL_OPERATING_STATUSES.map((value) => (
                    <option key={value} value={value}>
                      {value.replace(/_/g, " ").toLowerCase()}
                    </option>
                  ))}
                </select>
              </label>
              {error && <p role="alert" style={{ color: "#b91c1c" }}>{error}</p>}
              <div style={{ display: "flex", gap: 8 }}>
                <button className="primary-button" onClick={handleAddControl} disabled={saving}>
                  {saving ? "Saving..." : "Save control"}
                </button>
                <button
                  className="secondary-button"
                  onClick={() => setAddingControl(false)}
                  disabled={saving}
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

interface ControlRowProps {
  assessmentId: number;
  control: Control;
  currentAssessment: ControlAssessmentRecord | undefined;
  canEdit: boolean;
  onChanged: () => Promise<void>;
  mappableFactors: { id: number; category: string }[];
  // The same control type mapped to other risks, with each one's current
  // assessment, and the AI's accepted-evidence view of this control.
  siblings: { control: Control; current: ControlAssessmentRecord | undefined }[];
  aiEffectiveness?: string;
}

// One mapped control: its metadata, current status badge, and an inline
// form for recording a new design/effectiveness assessment (R7.4-R7.6).
function ControlRow({
  assessmentId,
  control,
  currentAssessment,
  canEdit,
  onChanged,
  mappableFactors,
  siblings,
  aiEffectiveness,
}: ControlRowProps) {
  const [assessing, setAssessing] = useState(false);
  const [applyToAll, setApplyToAll] = useState(false);
  const [prefilled, setPrefilled] = useState(false);
  const [designAdequacy, setDesignAdequacy] = useState(
    currentAssessment?.design_adequacy ?? "NOT_ASSESSED"
  );
  const [effectiveness, setEffectiveness] = useState(
    currentAssessment?.operating_effectiveness ?? "UNVERIFIED"
  );
  const [hasEvidence, setHasEvidence] = useState(currentAssessment?.has_evidence ?? false);
  const [coverageComplete, setCoverageComplete] = useState(
    currentAssessment?.coverage_complete ?? true
  );
  const [dependsOnUnavailableData, setDependsOnUnavailableData] = useState(
    currentAssessment?.depends_on_unavailable_data ?? false
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const status = controlDisplayStatus(currentAssessment);

  async function handleSaveAssessment() {
    if (!canEdit) {
      setError("Only an FCRM Analyst, Manager or Admin can assess a control.");
      return;
    }

    if (effectiveness === "EFFECTIVE" && !hasEvidence) {
      setError("A control cannot be marked Effective without supporting evidence.");
      return;
    }

    setSaving(true);
    setError(null);

    try {
      await assessControl(assessmentId, control.id, {
        design_adequacy: designAdequacy,
        operating_effectiveness: effectiveness,
        has_evidence: hasEvidence,
        coverage_complete: coverageComplete,
        depends_on_unavailable_data: dependsOnUnavailableData,
      });

      // The same control is usually mapped to several risks. Apply the same
      // rating to those mappings, but keep each one's own evidence status:
      // a mapping without accepted evidence can't be rated Effective.
      if (applyToAll) {
        for (const sibling of siblings) {
          const siblingHasEvidence = sibling.current?.has_evidence ?? false;
          await assessControl(assessmentId, sibling.control.id, {
            design_adequacy: designAdequacy,
            operating_effectiveness:
              effectiveness === "EFFECTIVE" && !siblingHasEvidence
                ? "PARTIALLY_EFFECTIVE"
                : effectiveness,
            has_evidence: siblingHasEvidence,
            coverage_complete: coverageComplete,
            depends_on_unavailable_data: dependsOnUnavailableData,
          });
        }
      }
      setAssessing(false);
      await onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save assessment");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      className="control-item"
      style={{ flexDirection: "column", alignItems: "stretch", background: "#f9fafb" }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div className="control-item-main">
          <h3 style={{ fontSize: 14 }}>{controlTypeLabel(control.control_type)}</h3>
          <p>
            {control.owner ? `Owner: ${control.owner}` : "No owner recorded"}
            {control.performing_department ? ` • Department: ${control.performing_department}` : ""}
            {control.frequency ? ` • Frequency: ${control.frequency}` : ""}
            {control.evidence_source ? ` • Evidence: ${control.evidence_source}` : ""}
          </p>
          <p>
            Status: {control.operating_status.replace(/_/g, " ").toLowerCase()}
            {control.trigger ? ` • Trigger: ${control.trigger}` : ""}
            {control.scope ? ` • Scope: ${control.scope}` : ""}
          </p>
        </div>

        <span
          className={`control-status ${status.toLowerCase().replace(/_/g, "-")}`}
        >
          {controlStatusLabel(status)}
        </span>
      </div>

      {currentAssessment?.effectiveness_rationale?.startsWith("AI-suggested") && (
        <p style={{ margin: "4px 0 0", fontSize: 12, color: "#667085" }}>
          AI-suggested rating from the evidence you accepted — confirm or change it under “Assess control”.
        </p>
      )}
      {!currentAssessment?.effectiveness_rationale?.startsWith("AI-suggested") &&
        currentAssessment?.design_rationale?.startsWith("AI-suggested design review") && (
          <p style={{ margin: "4px 0 0", fontSize: 12, color: "#667085" }}>
            AI-suggested design review: {currentAssessment.design_adequacy.replace(/_/g, " ").toLowerCase()}. Confirm
            or change it under “Assess control”.
          </p>
        )}

      {canEdit && (
        <div style={{ marginTop: 6 }}>
          {!assessing ? (
            <button
              className="secondary-button"
              onClick={() => {
                // Pre-fill from the AI's view of the accepted evidence, but
                // only when no rating has been recorded yet.
                if (effectiveness === "UNVERIFIED" && aiEffectiveness && aiEffectiveness !== "UNVERIFIED") {
                  setEffectiveness(aiEffectiveness);
                  setPrefilled(true);
                }
                setAssessing(true);
              }}
            >
              Assess control
            </button>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 4 }}>
              <label>
                Design adequacy
                <select
                  value={designAdequacy}
                  onChange={(e) => setDesignAdequacy(e.target.value)}
                >
                  {DESIGN_ADEQUACY_VALUES.map((value) => (
                    <option key={value} value={value}>
                      {value.replace(/_/g, " ")}
                    </option>
                  ))}
                </select>
              </label>

              <label>
                Operating effectiveness
                <select
                  value={effectiveness}
                  onChange={(e) => setEffectiveness(e.target.value)}
                >
                  {OPERATING_EFFECTIVENESS_VALUES.map((value) => (
                    <option key={value} value={value}>
                      {value.replace(/_/g, " ")}
                    </option>
                  ))}
                </select>
              </label>

              {prefilled && (
                <p style={{ margin: 0, fontSize: 12, color: "#667085" }}>
                  Effectiveness pre-filled from the AI's reading of the evidence you accepted. Confirm or change it.
                </p>
              )}

              <label>
                <input
                  type="checkbox"
                  checked={hasEvidence}
                  onChange={(e) => setHasEvidence(e.target.checked)}
                />{" "}
                Supporting evidence available
              </label>

              <label>
                <input
                  type="checkbox"
                  checked={coverageComplete}
                  onChange={(e) => setCoverageComplete(e.target.checked)}
                />{" "}
                Covers the full risk
              </label>

              <label>
                <input
                  type="checkbox"
                  checked={dependsOnUnavailableData}
                  onChange={(e) => setDependsOnUnavailableData(e.target.checked)}
                />{" "}
                Depends on currently unavailable data
              </label>

              {siblings.length > 0 && (
                <label>
                  <input
                    type="checkbox"
                    checked={applyToAll}
                    onChange={(e) => setApplyToAll(e.target.checked)}
                  />{" "}
                  Also apply this rating to the {siblings.length} other risk{siblings.length === 1 ? "" : "s"} this
                  control is mapped to (evidence status stays per mapping)
                </label>
              )}

              {error && <p role="alert" style={{ color: "#b91c1c" }}>{error}</p>}

              <div style={{ display: "flex", gap: 8 }}>
                <button
                  className="primary-button"
                  onClick={handleSaveAssessment}
                  disabled={saving}
                >
                  {saving ? "Saving..." : "Save assessment"}
                </button>
                <button
                  className="secondary-button"
                  onClick={() => setAssessing(false)}
                  disabled={saving}
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* R7.2 / R10.2: versioned edit, remap and unmap, with history. */}
      <ControlChangePanel
        assessmentId={assessmentId}
        control={control}
        factors={mappableFactors}
        canChange={canEdit}
        onChanged={() => void onChanged()}
      />

      {canEdit && (
        <ControlConditionForm assessmentId={assessmentId} controlId={control.id} onAdded={onChanged} />
      )}
    </div>
  );
}

interface ControlConditionRowProps {
  assessmentId: number;
  condition: ControlCondition;
  canEdit: boolean;
  onChanged: () => Promise<void>;
}

// R7.7: a required control enhancement, tracked to completion.
function ControlConditionRow({
  assessmentId,
  condition,
  canEdit,
  onChanged,
}: ControlConditionRowProps) {
  const [saving, setSaving] = useState(false);

  async function handleMarkComplete() {
    setSaving(true);
    try {
      await updateControlCondition(assessmentId, condition.id, { status: "COMPLETED" });
      await onChanged();
    } catch (error) {
      console.error(error);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="control-item">
      <div className="control-item-main">
        <h3 style={{ fontSize: 14 }}>{condition.description}</h3>
        <p>
          Status: <strong>{condition.status}</strong>
          {condition.owner ? ` • Owner: ${condition.owner}` : ""}
          {condition.due_date ? ` • Due: ${condition.due_date}` : ""}
        </p>
      </div>

      {canEdit && condition.status !== "COMPLETED" && (
        <button className="secondary-button" onClick={handleMarkComplete} disabled={saving}>
          {saving ? "Saving..." : "Mark complete"}
        </button>
      )}
    </div>
  );
}

interface ResidualRiskStageProps {
  assessment: Assessment;
  riskFactors: RiskFactor[];
  controlSummary: AssessmentControlSummary | null;
  onAdvance: () => Promise<void>;
  advanceLoading: boolean;
  advanceError: string | null;
  canRunPipeline: boolean;
}

function ResidualRiskStage({
  assessment,
  riskFactors,
  controlSummary,
  onAdvance,
  advanceLoading,
  advanceError,
  canRunPipeline,
}: ResidualRiskStageProps) {
  const controlReduction = controlSummary?.control_reduction ?? 0;

  const inherentRisk =
    assessment.inherent_score ?? assessment.overall_score ?? 0;

  // The control environment is a mitigant, not a risk with its own
  // residual, so it is left out of the per-risk residual list.
  const applicableRiskFactors = riskFactors.filter(
    (factor) =>
      factor.applicable &&
      !factor.excluded &&
      factor.category !== "CONTROL_ENVIRONMENT_RISK"
  );

  // Bands come from the methodology in force, so a score gets the same label
  // here as on the inherent-risk and approval screens. getRiskLevel() is only
  // the fallback while they load.
  const [methodologyBands, setMethodologyBands] = useState<
    { name: string; min: number; max: number }[]
  >([]);
  useEffect(() => {
    let cancelled = false;
    getInherentRiskCalculation(assessment.id)
      .then((calculation) => {
        if (!cancelled) setMethodologyBands(calculation.risk_bands ?? []);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [assessment.id]);

  function bandForScore(score: number): string {
    const match = [...methodologyBands]
      .sort((a, b) => b.min - a.min)
      .find((band) => score >= band.min);
    return match ? match.name.toUpperCase() : getRiskLevel(score);
  }

  // The backend's residual-grid result (inherent band x control rating,
  // held up by any non-mitigable rule), not a client-side subtraction.
  const residualState = useResidualRisk(assessment.id, controlSummary);
  const residualCalc = residualState.residual;
  const residualRisk = residualCalc?.residual_score ?? null;

  const residualRiskLevel = residualCalc?.residual_band ?? "PENDING";

  // The band of record can differ from what the score alone implies: a
  // policy rule (e.g. a FATF call-for-action jurisdiction) may have set it.
  const inherentBandOfRecord =
    residualCalc?.inherent_band ??
    assessment.inherent_risk_level ??
    getRiskLevel(inherentRisk);

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

        <ResidualGridPanel residual={residualCalc} error={residualState.error} />

        {canRunPipeline && (
          <section className="workflow-card" style={{ marginBottom: 16 }}>
            <div className="workflow-card-header">
              <div>
                <h2>Approve Residual Risk</h2>
                <p>
                  Confirm the residual risk figures above and continue to
                  Human Review.
                </p>
              </div>
            </div>

            {advanceError && <p role="alert" style={{ color: "#b91c1c" }}>{advanceError}</p>}

            <button
              className="primary-button"
              onClick={onAdvance}
              disabled={advanceLoading}
            >
              {advanceLoading ? "Advancing..." : "Approve & Continue to Human Review"}
            </button>
          </section>
        )}

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
                <RiskLevelIcon level={inherentBandOfRecord} />{" "}
                {inherentBandOfRecord}
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
                {controlReduction > 0 ? "-" : ""}{controlReduction.toFixed(1)}
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
                {formatResidualScore(residualRisk)}
              </strong>

              <small>
                <RiskLevelIcon level={residualRiskLevel} />{" "}
                {residualRiskLevel}
              </small>

            </div>

          </div>

        </section>


        {/* Risk-Factor Analysis */}

        <section className="workflow-card">

          <div className="workflow-card-header">

            <div>
              <h2>
                Residual Risk by Risk Factor
              </h2>

              <p>
                Remaining exposure after applying
                mapped controls (R7.2/R7.6).
              </p>
            </div>

          </div>


          <div className="residual-dimension-list">

            {applicableRiskFactors.map((factor) => {

              const controlsForFactor = (controlSummary?.controls ?? []).filter(
                (control) => control.risk_factor_id === factor.id
              );

              const bestStatus = controlsForFactor.reduce<ControlDisplayStatus>(
                (best, control) => {
                  const status = controlDisplayStatus(
                    controlSummary?.control_assessments[control.id]
                  );
                  const rank: Record<ControlDisplayStatus, number> = {
                    EFFECTIVE: 4,
                    PARTIALLY_EFFECTIVE: 2,
                    INEFFECTIVE: 0,
                    UNVERIFIED: 0,
                    // Below the others, so a control that exists but is
                    // unverified is shown as Unverified, not Not Assessed.
                    NOT_ASSESSED: -1,
                  };
                  return rank[status] > rank[best] ? status : best;
                },
                "NOT_ASSESSED"
              );

              const reduction =
                bestStatus === "EFFECTIVE" ? 4 : bestStatus === "PARTIALLY_EFFECTIVE" ? 2 : 0;

              const factorResidual = Math.max(factor.score - reduction, 0);
              const factorLevel = bandForScore(factorResidual);

              return (
                <div
                  className="residual-dimension-row"
                  key={factor.id}
                >

                  <div className="dimension-name">

                    <strong>
                      {riskCategoryLabel(factor.category)}
                    </strong>

                    <span>
                      {controlsForFactor.length > 0
                        ? `${controlsForFactor.length} control(s) mapped — ${controlStatusLabel(bestStatus)}`
                        : "No control mapped"}
                    </span>

                  </div>


                  <div className="dimension-score">

                    <div className="dimension-score-values">

                      <span>
                        {factor.score.toFixed(1)}
                      </span>

                      <span className="dimension-arrow">
                        →
                      </span>

                      <strong>
                        {factorResidual.toFixed(1)}
                      </strong>

                    </div>

                    <span
                      className={`dimension-level ${factorLevel.toLowerCase()}`}
                    >
                      <RiskLevelIcon level={factorLevel} />{" "}
                      {factorLevel}
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

            {residualRiskLevel === "CRITICAL" ? (

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

            ) : residualRiskLevel === "HIGH" ? (

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

            ) : residualRiskLevel === "MEDIUM" ? (

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

        <RecommendedConditionsPanel
          assessmentId={assessment.id}
          canEdit={canRunPipeline}
          refreshKey={residualCalc?.residual_band ?? null}
        />

        {residualCalc && (
          <ResidualConfirmationPanel
            key={`${residualCalc.id ?? "preview"}-${residualCalc.frozen}`}
            assessmentId={assessment.id}
            residual={residualCalc}
            canEdit={canRunPipeline}
          />
        )}

      </div>


      {/* RIGHT SIDE */}

      <aside className="residual-sidebar">

        <section className="residual-result-card">

          <span>
            RESIDUAL RISK RESULT
          </span>

          <strong>
            {formatResidualScore(residualRisk)}
          </strong>

          <div
            className={`residual-result-level ${residualRiskLevel.toLowerCase()}`}
          >
            <RiskLevelIcon level={residualRiskLevel} />{" "}
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
                {controlReduction > 0 ? "-" : ""}{controlReduction.toFixed(1)}
              </strong>
            </div>

            <div className="movement-row final">
              <span>
                Residual Risk
              </span>

              <strong>
                {formatResidualScore(residualRisk)}
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
// ---------------------------------------------------------------------
// Stage 9 (R9.1-R9.4): the structured, decision-ready assessment draft.
// Auto-generated by the backend on arrival at HUMAN_REVIEW (see
// PATCH .../advance-stage); this panel displays it, lets an authorized
// analyst edit the narrative sections (R9.3) and accept it (R9.4), and
// exposes a manual (re)generate action for when it doesn't exist yet or
// needs refreshing after a later change.
// ---------------------------------------------------------------------

interface AssessmentDraftPanelProps {
  assessmentId: number;
  canEdit: boolean;
  currentUserEmail: string;
}

function AssessmentDraftPanel({
  assessmentId,
  canEdit,
  currentUserEmail,
}: AssessmentDraftPanelProps) {
  const [draft, setDraft] = useState<AssessmentDraft | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [accepting, setAccepting] = useState(false);
  const [editValues, setEditValues] = useState({
    executive_summary: "",
    business_change_description: "",
    analyst_recommendation: "",
  });

  async function refresh() {
    try {
      setLoading(true);
      setDraft(await getAssessmentDraft(assessmentId));
      setError(null);
    } catch {
      setDraft(null);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assessmentId]);

  async function handleGenerate() {
    setGenerating(true);
    setError(null);
    try {
      setDraft(await generateAssessmentDraft(assessmentId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to generate draft");
    } finally {
      setGenerating(false);
    }
  }

  async function handleSaveEdit() {
    setSaving(true);
    setError(null);
    try {
      const updated = await updateAssessmentDraft(assessmentId, {
        ...editValues,
        edited_by: currentUserEmail,
      });
      setDraft(updated);
      setEditing(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save edit");
    } finally {
      setSaving(false);
    }
  }

  async function handleAccept() {
    setAccepting(true);
    setError(null);
    try {
      setDraft(await acceptAssessmentDraft(assessmentId, currentUserEmail));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to accept draft");
    } finally {
      setAccepting(false);
    }
  }

  if (loading) {
    return (
      <section className="risk-results-section">
        <h2>Assessment Draft</h2>
        <p>Loading...</p>
      </section>
    );
  }

  if (!draft) {
    return (
      <section className="risk-results-section">
        <h2>Assessment Draft (Stage 9)</h2>
        <p className="risk-reason">
          No structured assessment draft has been generated yet.
        </p>
        {error && (
          <p className="risk-reason" role="alert" style={{ color: "#b91c1c" }}>
            {error}
          </p>
        )}
        {canEdit && (
          <button
            type="button"
            className="primary-button"
            onClick={handleGenerate}
            disabled={generating}
          >
            {generating ? "Generating..." : "Generate Assessment Draft"}
          </button>
        )}
      </section>
    );
  }

  const uncertainty = draft.uncertainty || {};
  const uncertaintyCount =
    (uncertainty.low_confidence_items?.length ?? 0) +
    (uncertainty.unsupported_conclusions?.length ?? 0) +
    (uncertainty.conflicting_evidence?.length ?? 0) +
    (uncertainty.unresolved_questions?.length ?? 0);
  const controlsWithoutEvidence = draft.mapped_controls.filter((control) => !control.has_evidence).length;
  const profileUnconfirmed = "confirmed" in (draft.business_profile ?? {}) && !draft.business_profile.confirmed;
  const flaggedCount = draft.risk_gaps.length + draft.missing_information.length + uncertaintyCount;
  // What a reviewer should look at first, before reading the whole report.
  const attention: string[] = [];
  if (profileUnconfirmed) attention.push("The business owner has not confirmed the business profile.");
  if (draft.missing_information.length > 0) {
    attention.push(`${draft.missing_information.length} piece(s) of information are missing.`);
  }
  if (draft.risk_gaps.length > 0) attention.push(`${draft.risk_gaps.length} risk gap(s) are recorded.`);
  if (uncertaintyCount > 0) attention.push(`${uncertaintyCount} point(s) of uncertainty are flagged.`);
  if (controlsWithoutEvidence > 0) attention.push(`${controlsWithoutEvidence} mapped control(s) have no supporting evidence.`);
  if (!draft.accepted_by) attention.push("The draft has not been accepted yet.");

  return (
    <section className="risk-results-section">
      <h2>Assessment Draft (Stage 9)</h2>
      <p className="risk-reason">
        Structured, decision-ready assessment — version {draft.version}
        {draft.is_edited ? ` (edited by ${draft.edited_by})` : " (as generated)"}.
        Generated {new Date(draft.generated_at).toLocaleString()} via{" "}
        {draft.generation_method}
        {draft.model_version ? ` (${draft.model_version})` : ""}, config{" "}
        {draft.config_version ?? "Default"}.
      </p>

      {draft.accepted_by && (
        <p className="risk-reason" style={{ color: "#15803d" }}>
          Accepted by {draft.accepted_by} on{" "}
          {draft.accepted_at && new Date(draft.accepted_at).toLocaleString()}.
        </p>
      )}

      <DraftVersionHistory assessmentId={assessmentId} currentVersion={draft.version} />

      {error && (
        <p className="risk-reason" role="alert" style={{ color: "#b91c1c" }}>
          {error}
        </p>
      )}

      {editing ? (
        <div className="form-group">
          <label>Executive Summary</label>
          <textarea aria-label="Executive Summary"
            rows={3}
            value={editValues.executive_summary}
            onChange={(event) =>
              setEditValues((current) => ({ ...current, executive_summary: event.target.value }))
            }
          />
          <label>Business Change Description</label>
          <textarea aria-label="Business Change Description"
            rows={2}
            value={editValues.business_change_description}
            onChange={(event) =>
              setEditValues((current) => ({
                ...current,
                business_change_description: event.target.value,
              }))
            }
          />
          <label>Analyst Recommendation</label>
          <textarea aria-label="Analyst Recommendation"
            rows={2}
            value={editValues.analyst_recommendation}
            onChange={(event) =>
              setEditValues((current) => ({ ...current, analyst_recommendation: event.target.value }))
            }
          />
          <div className="form-actions">
            <button type="button" className="secondary-button" onClick={() => setEditing(false)} disabled={saving}>
              Cancel
            </button>
            <button type="button" className="primary-button" onClick={handleSaveEdit} disabled={saving}>
              {saving ? "Saving..." : "Save Edit"}
            </button>
          </div>
        </div>
      ) : (
        <>
          <div className="draft-facts">
            <div>
              <span>Inherent risk</span>
              <strong>
                {String(draft.inherent_risk.score ?? "—")} · {String(draft.inherent_risk.band ?? "—")}
              </strong>
            </div>
            <div>
              <span>Residual risk</span>
              <strong>
                {String(draft.residual_risk.score ?? "—")} · {String(draft.residual_risk.band ?? "—")}
              </strong>
            </div>
            <div>
              <span>Controls effective</span>
              <strong>
                {String(draft.control_effectiveness.effective_controls ?? 0)} of{" "}
                {String(draft.control_effectiveness.total_controls ?? 0)}
              </strong>
            </div>
            <div>
              <span>Documents on file</span>
              <strong>{draft.evidence_references.length}</strong>
            </div>
          </div>

          <div className={`draft-attention ${attention.length === 0 ? "draft-attention-clear" : ""}`}>
            <h3>Needs your attention</h3>
            {attention.length === 0 ? (
              <p>Nothing is flagged on this draft.</p>
            ) : (
              <ul>
                {attention.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            )}
          </div>

          <h3>Executive Summary</h3>
          <p className="risk-reason">{draft.executive_summary}</p>

          <details className="draft-group">
          <summary>Business profile &amp; scope</summary>

          <h3>Business Change Description</h3>
          <p className="risk-reason">{draft.business_change_description}</p>

          <h3>Validated Business Profile</h3>
          {Object.entries(draft.business_profile ?? {}).filter(
            ([key, value]) => key !== "confirmed" && value != null && value !== "" && !(Array.isArray(value) && value.length === 0)
          ).length === 0 ? (
            <p className="risk-reason">No structured profile captured.</p>
          ) : (
            <dl className="risk-reason" style={{ display: "grid", gridTemplateColumns: "minmax(140px, max-content) 1fr", gap: "4px 12px" }}>
              {Object.entries(draft.business_profile)
                .filter(([key, value]) => key !== "confirmed" && value != null && value !== "" && !(Array.isArray(value) && value.length === 0))
                .map(([key, value]) => (
                  <div key={key} style={{ display: "contents" }}>
                    <dt style={{ fontWeight: 600 }}>{key.replace(/_/g, " ")}</dt>
                    <dd style={{ margin: 0 }}>{Array.isArray(value) ? value.join(", ") : String(value)}</dd>
                  </div>
                ))}
            </dl>
          )}
          {"confirmed" in (draft.business_profile ?? {}) && (
            <p className="risk-reason">
              Profile {draft.business_profile.confirmed ? "confirmed by the business owner" : "not yet confirmed"}.
            </p>
          )}

          <h3>Applicable Risk Categories</h3>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, margin: "8px 0" }}>
            {draft.applicable_risk_categories.map((category) => (
              <span key={category} className="parsed-badge">
                {RISK_CATEGORIES.find((option) => option.value === category)?.label ?? category}
              </span>
            ))}
          </div>

          <h3>Detailed Risk Indicators</h3>
          {draft.risk_indicators.length === 0 ? (
            <p className="risk-reason">No indicators recorded.</p>
          ) : (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, margin: "8px 0" }}>
              {draft.risk_indicators.map((indicator) => (
                <span key={indicator} className="parsed-badge">
                  {indicator.replace(/_/g, " ").toLowerCase()}
                </span>
              ))}
            </div>
          )}

          <h3>Risk Statements</h3>
          {draft.risk_statements.map((statement) => (
            <p className="risk-reason" key={statement.category}>
              <strong>
                {RISK_CATEGORIES.find((option) => option.value === statement.category)?.label ?? statement.category}:{" "}
              </strong>
              {statement.statement}
            </p>
          ))}

          </details>

          <details className="draft-group">
          <summary>Risk, evidence &amp; controls</summary>
          <h3>Inherent Risk</h3>
          <p className="risk-reason">
            Score {String(draft.inherent_risk.score ?? "—")}, band {String(draft.inherent_risk.band ?? "—")}
            {draft.inherent_risk.escalated ? " (mandatory escalation applied)" : ""}
            {draft.inherent_risk.overridden
              ? ` — analyst override of the calculated ${String(draft.inherent_risk.calculated_band ?? "—")} ` +
                `(${String(draft.inherent_risk.calculated_score ?? "—")}): ${String(draft.inherent_risk.override_reason ?? "")}`
              : ""}
          </p>

          <h3>Evidence &amp; Source References</h3>
          {draft.evidence_references.length === 0 ? (
            <p className="risk-reason">No documents were on file when this draft was generated.</p>
          ) : (
            <ul>
              {draft.evidence_references.map((reference, index) => (
                <li key={index}>
                  {String(reference.filename ?? "Document")} ({String(reference.document_type ?? "—").replace(/_/g, " ").toLowerCase()})
                </li>
              ))}
            </ul>
          )}

          <h3>Mapped Controls &amp; Effectiveness</h3>
          <p className="risk-reason">
            {String(draft.control_effectiveness.effective_controls ?? 0)} of{" "}
            {String(draft.control_effectiveness.total_controls ?? 0)} mapped controls are effective.
          </p>
          {draft.mapped_controls.length > 0 && (
            <ul>
              {draft.mapped_controls.map((control, index) => (
                <li key={index}>
                  {String(control.control_type ?? "").replace(/_/g, " ").toLowerCase()} — design{" "}
                  {String(control.design_adequacy ?? "—").replace(/_/g, " ").toLowerCase()}, operating{" "}
                  {String(control.operating_effectiveness ?? "—").replace(/_/g, " ").toLowerCase()}
                  {control.has_evidence ? "" : " (no supporting evidence)"}
                </li>
              ))}
            </ul>
          )}

          <h3>Residual Risk</h3>
          <p className="risk-reason">
            Score {String(draft.residual_risk.score ?? "—")}, band {String(draft.residual_risk.band ?? "—")}
          </p>

          </details>

          <details className="draft-group">
          <summary>
            Gaps, assumptions &amp; uncertainty{flaggedCount > 0 ? ` (${flaggedCount} flagged)` : ""}
          </summary>
          {draft.risk_gaps.length > 0 && (
            <>
              <h3>Risk Gaps</h3>
              <ul>
                {draft.risk_gaps.map((gap, index) => (
                  <li key={index}>{String(gap.gap_type)}{gap.description ? `: ${gap.description}` : ""}</li>
                ))}
              </ul>
            </>
          )}

          <h3>Assumptions</h3>
          {draft.assumptions.length === 0 ? (
            <p className="risk-reason">None recorded.</p>
          ) : (
            <ul>
              {draft.assumptions.map((item, index) => <li key={index}>{item}</li>)}
            </ul>
          )}

          <h3>Missing Information</h3>
          {draft.missing_information.length === 0 ? (
            <p className="risk-reason">None recorded.</p>
          ) : (
            <ul>
              {draft.missing_information.map((item, index) => <li key={index}>{item}</li>)}
            </ul>
          )}

          <h3>Uncertainty (R9.2)</h3>
          {(!uncertainty.low_confidence_items?.length &&
            !uncertainty.unsupported_conclusions?.length &&
            !uncertainty.conflicting_evidence?.length &&
            !uncertainty.unresolved_questions?.length) ? (
            <p className="risk-reason">No unresolved uncertainty flagged.</p>
          ) : (
            <>
              {uncertainty.low_confidence_items?.map((item, index) => (
                <p className="risk-reason" key={`lc-${index}`} style={{ color: "#b45309" }}>
                  <strong>Low confidence: </strong>{item}
                </p>
              ))}
              {uncertainty.unsupported_conclusions?.map((item, index) => (
                <p className="risk-reason" key={`uc-${index}`} style={{ color: "#b45309" }}>
                  <strong>Unsupported conclusion: </strong>{item}
                </p>
              ))}
              {uncertainty.conflicting_evidence?.map((item, index) => (
                <p className="risk-reason" key={`ce-${index}`} style={{ color: "#b91c1c" }}>
                  <strong>Conflicting evidence: </strong>{String((item as { message?: string }).message ?? JSON.stringify(item))}
                </p>
              ))}
              {uncertainty.unresolved_questions?.map((item, index) => (
                <p className="risk-reason" key={`uq-${index}`} style={{ color: "#b45309" }}>
                  <strong>Unresolved question: </strong>{item}
                </p>
              ))}
            </>
          )}

          </details>

          <h3>Recommended Conditions</h3>
          {draft.recommended_conditions.length === 0 ? (
            <p className="risk-reason">None recorded.</p>
          ) : (
            <ul>
              {draft.recommended_conditions.map((item, index) => <li key={index}>{item}</li>)}
            </ul>
          )}

          <h3>{draft.is_edited ? "Analyst Recommendation" : "Automated Recommendation"}</h3>
          {/* Stage 19 (Explainability): a recommendation is never a decision. */}
          <p className="recommendation-notice" role="note">
            <span aria-hidden="true">ℹ </span>
            <strong>Advisory only.</strong>{" "}
            {draft.recommendation_notice ||
              "This is a recommendation for human review, not an approval or rejection."}
          </p>
          <p className="risk-reason">{draft.analyst_recommendation}</p>

          <h3>Required Approvals</h3>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, margin: "8px 0" }}>
            {draft.required_approvals.map((role) => (
              <span key={role} className="parsed-badge">{role}</span>
            ))}
          </div>

          {canEdit && (
            <div className="form-actions">
              <button
                type="button"
                className="doc-action-button"
                onClick={handleGenerate}
                disabled={generating}
              >
                {generating ? "Regenerating..." : "Regenerate Draft"}
              </button>
              <button
                type="button"
                className="secondary-button"
                onClick={() => {
                  setEditValues({
                    executive_summary: draft.executive_summary,
                    business_change_description: draft.business_change_description,
                    analyst_recommendation: draft.analyst_recommendation,
                  });
                  setEditing(true);
                }}
              >
                Edit
              </button>
              {!draft.accepted_by && (
                <button
                  type="button"
                  className="primary-button"
                  onClick={handleAccept}
                  disabled={accepting}
                >
                  {accepting ? "Accepting..." : "Accept Draft"}
                </button>
              )}
            </div>
          )}
        </>
      )}
    </section>
  );
}

interface FcrmReviewStageProps {
  assessment: Assessment;
  riskResults: RiskResult[];
  controlSummary: AssessmentControlSummary | null;
  onSubmitForReview: () => Promise<void>;
  submitLoading: boolean;
  submitError: string | null;
  canRunPipeline: boolean;
  onAssessmentUpdated: (updated: Assessment) => void;
  currentUserId: number;
  currentUserRole: string;
}

function FcrmReviewStage({
  assessment,
  riskResults,
  controlSummary,
  onSubmitForReview,
  submitLoading,
  submitError,
  canRunPipeline,
  onAssessmentUpdated,
  currentUserId,
  currentUserRole,
}: FcrmReviewStageProps) {
  // P3: bumped after governance actions so the readiness panel re-checks.
  const [readinessKey, setReadinessKey] = useState(0);
  const inherentRisk = assessment.inherent_score ?? assessment.overall_score ?? 0;

  const controlReduction = controlSummary?.control_reduction ?? 0;

  // The backend's residual-grid result (inherent band x control rating,
  // held up by any non-mitigable rule), not a client-side subtraction.
  const residualState = useResidualRisk(assessment.id, controlSummary);
  const residualCalc = residualState.residual;
  const residualRisk = residualCalc?.residual_score ?? null;

  const suggestedRating = residualCalc?.residual_band ?? "PENDING";

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
    if (!canRunPipeline) {
      setSaveError("Only an FCRM Analyst, Manager or Admin can save the FCRM review.");
      return;
    }

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

  // Stage 7 (R7.6): open control gaps carry forward into FCRM review as
  // findings, instead of a hardcoded per-dimension guess.
  const findings: {
    key: string;
    title: string;
    detail: string;
    resolved: boolean;
  }[] = (controlSummary?.gaps ?? []).map((gap) => ({
    key: `gap-${gap.id}`,
    title: gapTypeLabel(gap.gap_type),
    detail:
      gap.description ??
      "This control gap should be reviewed before final disposition.",
    resolved: false,
  }));

  if (findings.length === 0 && riskResults.length > 0) {
    findings.push({
      key: "clear",
      title: "No open control findings",
      detail: "All mapped controls are currently rated Effective.",
      resolved: true,
    });
  }

  // Stage 10 (R10.6): review comments linked to a specific section.
  const [reviewComments, setReviewComments] = useState<AssessmentComment[]>([]);
  const [reviewCommentSection, setReviewCommentSection] = useState<string>(
    COMMENT_SECTIONS[0].value
  );
  const [reviewCommentBody, setReviewCommentBody] = useState("");
  const [reviewCommentSaving, setReviewCommentSaving] = useState(false);
  const [reviewCommentError, setReviewCommentError] = useState<string | null>(null);
  // R10.6: optionally pin the comment to one risk, control or document,
  // stored as "<kind>:<id>" in related_entity_id.
  const [reviewCommentEntity, setReviewCommentEntity] = useState("");
  const [commentFactors, setCommentFactors] = useState<RiskFactor[]>([]);
  const [commentDocuments, setCommentDocuments] = useState<AssessmentDocument[]>([]);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getRiskFactors(assessment.id), getAssessmentDocuments(assessment.id)])
      .then(([factors, documents]) => {
        if (cancelled) return;
        setCommentFactors(factors.filter((factor) => factor.applicable && !factor.excluded));
        setCommentDocuments(documents.filter((document) => document.is_current));
      })
      .catch((error) => console.error("Failed to load comment targets", error));
    return () => {
      cancelled = true;
    };
  }, [assessment.id]);

  const commentTargets: { value: string; label: string }[] =
    reviewCommentSection === "EVIDENCE"
      ? commentDocuments.map((document) => ({ value: `document:${document.id}`, label: document.filename }))
      : reviewCommentSection === "CONTROL_MAPPINGS"
      ? (controlSummary?.controls ?? []).map((control) => ({
          value: `control:${control.id}`,
          label: `${controlTypeLabel(control.control_type)} (${riskCategoryLabel(
            commentFactors.find((factor) => factor.id === control.risk_factor_id)?.category ?? ""
          )})`,
        }))
      : ["RISK_FACTORS", "SCORES", "RISK_RATIONALE", "RESIDUAL_RISK"].includes(reviewCommentSection)
      ? commentFactors.map((factor) => ({ value: `risk_factor:${factor.id}`, label: riskCategoryLabel(factor.category) }))
      : [];

  function commentTargetLabel(related: string | null): string | null {
    if (!related) return null;
    const [kind, id] = related.split(":");
    const numericId = Number(id);
    if (kind === "document") {
      return commentDocuments.find((document) => document.id === numericId)?.filename ?? `Document #${id}`;
    }
    if (kind === "control") {
      const control = controlSummary?.controls.find((item) => item.id === numericId);
      return control ? controlTypeLabel(control.control_type) : `Control #${id}`;
    }
    if (kind === "risk_factor") {
      const factor = commentFactors.find((item) => item.id === numericId);
      return factor ? riskCategoryLabel(factor.category) : `Risk factor #${id}`;
    }
    return related;
  }

  useEffect(() => {
    let cancelled = false;

    getAssessmentComments(assessment.id)
      .then((data) => {
        if (!cancelled) setReviewComments(data);
      })
      .catch((error) => console.error("Failed to load review comments", error));

    return () => {
      cancelled = true;
    };
  }, [assessment.id]);

  async function handlePostReviewComment() {
    if (!reviewCommentBody.trim()) return;

    setReviewCommentSaving(true);
    setReviewCommentError(null);

    try {
      const comment = await postAssessmentComment(
        assessment.id,
        reviewCommentBody.trim(),
        "ALL",
        reviewCommentSection,
        reviewCommentEntity || null
      );
      setReviewComments((current) => [...current, comment]);
      setReviewCommentBody("");
      setReviewCommentEntity("");
    } catch (error) {
      setReviewCommentError(
        error instanceof Error ? error.message : "Failed to post comment"
      );
    } finally {
      setReviewCommentSaving(false);
    }
  }

  async function handleResolveReviewComment(
    commentId: number,
    exceptionReason?: string
  ) {
    setReviewCommentError(null);

    try {
      const updated = await resolveAssessmentComment(assessment.id, commentId, {
        resolved: true,
        exceptionReason: exceptionReason ?? null,
      });
      setReviewComments((current) =>
        current.map((comment) => (comment.id === updated.id ? updated : comment))
      );
    } catch (error) {
      setReviewCommentError(
        error instanceof Error ? error.message : "Failed to update comment"
      );
    }
  }

  const unresolvedReviewComments = reviewComments.filter(
    (comment) => !comment.resolved && !(comment.exception_reason ?? "").trim()
  );

  // Stage 10 (R10.5): return the assessment to the business owner (or
  // another team) for clarification instead of continuing the review.
  const [requestInfoOpen, setRequestInfoOpen] = useState(false);
  const [requestInfoTarget, setRequestInfoTarget] = useState("");
  const [requestInfoNote, setRequestInfoNote] = useState("");
  const [requestInfoSaving, setRequestInfoSaving] = useState(false);
  const [requestInfoError, setRequestInfoError] = useState<string | null>(null);

  async function handleRequestInformation() {
    if (!requestInfoTarget.trim() || !requestInfoNote.trim()) {
      setRequestInfoError("A target and a note are both required.");
      return;
    }

    setRequestInfoSaving(true);
    setRequestInfoError(null);

    try {
      const updated = await requestAssessmentInformation(
        assessment.id,
        requestInfoTarget.trim(),
        requestInfoNote.trim()
      );
      onAssessmentUpdated(updated);
      setRequestInfoOpen(false);
      setRequestInfoTarget("");
      setRequestInfoNote("");
    } catch (error) {
      setRequestInfoError(
        error instanceof Error ? error.message : "Failed to request information"
      );
    } finally {
      setRequestInfoSaving(false);
    }
  }

  const canProvideInformation =
    canRunPipeline || currentUserId === assessment.owner_id;

  const [provideInfoResponse, setProvideInfoResponse] = useState("");
  const [provideInfoSaving, setProvideInfoSaving] = useState(false);
  const [provideInfoError, setProvideInfoError] = useState<string | null>(null);

  async function handleProvideInformation() {
    if (!provideInfoResponse.trim()) {
      setProvideInfoError("A response is required.");
      return;
    }

    setProvideInfoSaving(true);
    setProvideInfoError(null);

    try {
      const updated = await provideAssessmentInformation(
        assessment.id,
        provideInfoResponse.trim()
      );
      onAssessmentUpdated(updated);
      setProvideInfoResponse("");
    } catch (error) {
      setProvideInfoError(
        error instanceof Error ? error.message : "Failed to submit response"
      );
    } finally {
      setProvideInfoSaving(false);
    }
  }

  return (
    <div className="fcrm-layout">

      {/* LEFT */}

      <div className="fcrm-main">

        <DecisionReadinessPanel assessmentId={assessment.id} refreshKey={assessment.status} />

        {/* Stage 10 (R10.5): respond to an outstanding information request */}

        {assessment.status === "INFORMATION_REQUESTED" && canProvideInformation && (
          <section className="workflow-card">

            <div className="workflow-card-header">
              <div>
                <h2>Information Requested</h2>
                <p>
                  {assessment.information_requested_by ?? "The reviewer"} asked{" "}
                  {assessment.information_request_target ?? "you"} for clarification:
                </p>
              </div>
            </div>

            <p>
              <strong>{assessment.information_request_note}</strong>
            </p>

            <div className="justification-content">
              <textarea aria-label="Provide the requested information"
                placeholder="Provide the requested information..."
                value={provideInfoResponse}
                onChange={(event) => setProvideInfoResponse(event.target.value)}
                rows={4}
              />

              {provideInfoError && (
                <p className="save-review-error" role="alert">{provideInfoError}</p>
              )}

              <div className="justification-footer">
                <button
                  type="button"
                  className="save-review-button"
                  disabled={provideInfoSaving}
                  onClick={handleProvideInformation}
                >
                  {provideInfoSaving ? "Submitting…" : "Submit Response"}
                </button>
              </div>
            </div>

          </section>
        )}

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
                          <RiskLevelIcon level={result.severity} />{" "}
                          {result.severity} ({result.score.toFixed(1)})
                        </strong>
                      </div>

                      <select aria-label={`Reviewer rating for ${result.dimension}`}
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

              <div className="disagreement-banner" role="status">

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

              <div className="agreement-banner" role="status">

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

            <textarea aria-label="Reviewer justification"
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
                <span className="save-review-error" role="alert">
                  {saveError}
                </span>
              )}

              {savedJustSaved && !saveError && (
                <span className="save-review-success" role="status">
                  Saved
                </span>
              )}

              {!canRunPipeline && (
                <p style={{ color: "#667085" }}>
                  Only an FCRM Analyst, Manager or Admin can save the FCRM review.
                </p>
              )}

              <button
                type="button"
                className="save-review-button"
                disabled={
                  saveLoading ||
                  !canRunPipeline ||
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
                Rule-based findings generated from the current control
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

        {/* AI gap analysis: advisory findings beyond the rules above. */}
        <AIChallengePanel
          assessmentId={assessment.id}
          controls={(controlSummary?.controls ?? []).map((control) => ({
            id: control.id,
            label: controlTypeLabel(control.control_type),
          }))}
          canEdit={canRunPipeline}
        />


        {/* Stage 10 (R10.2/R10.3/R10.4): analyst value overrides */}

        <section className="workflow-card">

          <div className="workflow-card-header">

            <div>
              <h2>
                Analyst Value Overrides
              </h2>

              <p>
                Every change to an AI-generated value -- extracted
                fields, risk category, factor ratings, rationale,
                control mappings, control effectiveness, residual
                risk, or recommended conditions -- with the original
                AI value, the analyst-confirmed value, and the
                required reason.
              </p>
            </div>

          </div>

          {/* P2: the override ledger -- the system value is read from the
              record by the server, proposals need independent review, and
              nothing here changes a calculated value. */}
          <OverrideLedgerPanel
            assessmentId={assessment.id}
            controls={controlSummary?.controls ?? []}
            // P3 (provisional): overrides are proposed by an FCRM Analyst
            // (incl. Senior Analyst); the server enforces this too.
            canPropose={canRunPipeline && currentUserRole === "FCRM_ANALYST"}
          />

        </section>


        {/* R11: the mandatory challenge-review sign-off. */}
        <section className="workflow-card">
          <ChallengeSignoffPanel assessmentId={assessment.id} onChanged={() => setReadinessKey((k) => k + 1)} />
        </section>

        {/* P3: committee readiness, as computed by the server. */}
        <section className="workflow-card">
          <CommitteeReadinessPanel assessmentId={assessment.id} refreshKey={readinessKey} />
        </section>

        {/* Stage 10 (R10.6): section-linked review comments */}

        <section className="workflow-card">

          <div className="workflow-card-header">

            <div>
              <h2>
                Review Comments
              </h2>

              <p>
                Comments linked to a specific risk, control, document,
                or assessment section. An unresolved comment blocks
                final approval unless an authorized reviewer explicitly
                accepts the exception.
              </p>
            </div>

            {unresolvedReviewComments.length > 0 && (
              <span className="fcrm-badge" style={{ background: "#b91c1c" }}>
                {unresolvedReviewComments.length} UNRESOLVED
              </span>
            )}

          </div>

          {reviewComments.length === 0 ? (
            <div className="workflow-empty">
              No review comments yet.
            </div>
          ) : (
            <div className="fcrm-dimension-list">
              {reviewComments.map((comment) => (
                <div className="fcrm-dimension-row" key={comment.id}>
                  <div className="fcrm-dimension-info">
                    <strong>
                      {COMMENT_SECTIONS.find((s) => s.value === comment.section)?.label ??
                        comment.section ??
                        "General"}
                      {comment.related_entity_id && ` › ${commentTargetLabel(comment.related_entity_id)}`}
                      {" — "}
                      {comment.author_name ?? "Unknown"}
                    </strong>

                    <span>{comment.body}</span>

                    {comment.resolved && (
                      <span className="finding-status resolved">
                        {comment.exception_reason
                          ? `EXCEPTION ACCEPTED: ${comment.exception_reason}`
                          : "RESOLVED"}
                      </span>
                    )}
                  </div>

                  {!comment.resolved && canRunPipeline && (
                    <div className="fcrm-dimension-compare">
                      <button
                        type="button"
                        className="save-review-button"
                        onClick={() => handleResolveReviewComment(comment.id)}
                      >
                        Resolve
                      </button>

                      <button
                        type="button"
                        className="save-review-button"
                        onClick={() => {
                          const reason = window.prompt(
                            "Reason for accepting this exception:"
                          );
                          if (reason && reason.trim()) {
                            handleResolveReviewComment(comment.id, reason.trim());
                          }
                        }}
                      >
                        Accept Exception
                      </button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          <div className="justification-content">
            <select aria-label="Review comment section"
              value={reviewCommentSection}
              onChange={(event) => {
                setReviewCommentSection(event.target.value);
                setReviewCommentEntity("");
              }}
            >
              {COMMENT_SECTIONS.map((section) => (
                <option key={section.value} value={section.value}>
                  {section.label}
                </option>
              ))}
            </select>

            {commentTargets.length > 0 && (
              <select aria-label="Specific item this comment is about"
                value={reviewCommentEntity}
                onChange={(event) => setReviewCommentEntity(event.target.value)}
              >
                <option value="">Whole section</option>
                {commentTargets.map((target) => (
                  <option key={target.value} value={target.value}>
                    {target.label}
                  </option>
                ))}
              </select>
            )}

            <textarea aria-label="Add a review comment"
              placeholder="Add a review comment..."
              value={reviewCommentBody}
              onChange={(event) => setReviewCommentBody(event.target.value)}
              rows={3}
            />

            {reviewCommentError && (
              <p className="save-review-error" role="alert">{reviewCommentError}</p>
            )}

            <div className="justification-footer">
              <button
                type="button"
                className="save-review-button"
                disabled={reviewCommentSaving || !reviewCommentBody.trim()}
                onClick={handlePostReviewComment}
              >
                {reviewCommentSaving ? "Posting…" : "Add Comment"}
              </button>
            </div>
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
              assessment.status !== "HUMAN_REVIEW"
            }
            onClick={onSubmitForReview}
          >
            {submitLoading
              ? "Submitting…"
              : assessment.status !== "HUMAN_REVIEW"
              ? `Assessment is ${assessment.status}`
              : "Submit to Manager"}
          </button>

          {submitError && (
            <p className="submit-review-error" role="alert">
              {submitError}
            </p>
          )}

          {/* Stage 10 (R10.5): request more information */}

          {canRunPipeline && assessment.status === "HUMAN_REVIEW" && !requestInfoOpen && (
            <button
              type="button"
              className="save-review-button"
              style={{ marginTop: 8 }}
              onClick={() => setRequestInfoOpen(true)}
            >
              Request More Information
            </button>
          )}

          {assessment.status === "INFORMATION_REQUESTED" && (
            <p style={{ color: "#b91c1c", marginTop: 8 }}>
              Waiting on {assessment.information_request_target ?? "the business owner"}:
              {" "}
              {assessment.information_request_note}
            </p>
          )}

          {requestInfoOpen && (
            <div className="justification-content" style={{ marginTop: 8 }}>
              <input aria-label="Return to (e.g. business owner, compliance team)"
                type="text"
                placeholder="Return to (e.g. business owner, compliance team)"
                value={requestInfoTarget}
                onChange={(event) => setRequestInfoTarget(event.target.value)}
              />

              <textarea aria-label="What information is needed?"
                placeholder="What information is needed?"
                value={requestInfoNote}
                onChange={(event) => setRequestInfoNote(event.target.value)}
                rows={3}
              />

              {requestInfoError && (
                <p className="save-review-error" role="alert">{requestInfoError}</p>
              )}

              <div className="justification-footer">
                <button
                  type="button"
                  className="save-review-button"
                  disabled={requestInfoSaving}
                  onClick={handleRequestInformation}
                >
                  {requestInfoSaving ? "Sending…" : "Send Request"}
                </button>

                <button
                  type="button"
                  className="save-review-button"
                  onClick={() => setRequestInfoOpen(false)}
                >
                  Cancel
                </button>
              </div>
            </div>
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
                {controlReduction > 0 ? "-" : ""}{controlReduction.toFixed(1)}
              </strong>
            </div>

            <div>
              <span>
                Residual Risk
              </span>

              <strong>
                {formatResidualScore(residualRisk)}
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

interface ChallengeReviewStageProps {
  // Accepting a finding without fixing it is Manager/Admin only (R11.6).
  canAccept: boolean;
  assessmentId: number;
  canEdit: boolean;
}

function challengeSeverityRank(severity: string): number {
  return { LOW: 0, MEDIUM: 1, HIGH: 2, CRITICAL: 3 }[severity] ?? 0;
}

// Stage 11 (R11.1-R11.6): Conditional Challenge Review. Self-fetches its
// own data (same pattern as the FCRM review load above) since it isn't
// part of the data this workflow's main effect already loads.
function ChallengeReviewStage({ assessmentId, canEdit, canAccept }: ChallengeReviewStageProps) {
  const [review, setReview] = useState<ChallengeReview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    try {
      setLoading(true);
      setError(null);
      setReview(await getChallengeReview(assessmentId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load challenge review");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assessmentId]);

  if (loading && !review) {
    return (
      <div className="workflow-placeholder">
        <h2>Conditional Challenge Review</h2>
        <p>Loading...</p>
      </div>
    );
  }

  if (error && !review) {
    return (
      <div className="workflow-placeholder">
        <h2>Conditional Challenge Review</h2>
        <p role="alert" style={{ color: "#b91c1c" }}>{error}</p>
      </div>
    );
  }

  if (!review) {
    return null;
  }

  const openFindings = review.findings
    .filter((f) => f.resolution_status === "OPEN")
    .sort((a, b) => challengeSeverityRank(b.severity) - challengeSeverityRank(a.severity));
  const closedFindings = review.findings.filter((f) => f.resolution_status !== "OPEN");

  return (
    <div className="controls-layout">
      <div className="controls-main">
        <section className="workflow-card">
          <div className="workflow-card-header">
            <div>
              <h2>Conditional Challenge Review</h2>
              <p>
                Additional scrutiny triggered when risk, evidence, controls, or
                ratings meet the configured challenge conditions (R11.1-R11.6).
              </p>
            </div>
            <span className="control-engine-badge">
              {review.triggered ? "REVIEW TRIGGERED" : "NOT TRIGGERED"}
            </span>
          </div>

          <div className="control-list" style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {review.triggers.map((trigger) => (
              <span
                key={trigger.name}
                className={`control-status ${trigger.fired ? "not-validated" : "effective"}`}
              >
                {trigger.label}
              </span>
            ))}
          </div>
        </section>

        <section className="workflow-card">
          <div className="workflow-card-header">
            <div>
              <h2>Open Findings</h2>
              <p>
                High-severity findings must be resolved or explicitly accepted
                before this assessment can be submitted to committee (R11.6).
              </p>
            </div>
          </div>

          {openFindings.length === 0 ? (
            <p className="control-list" style={{ color: "#667085" }}>No open challenge findings.</p>
          ) : (
            <div className="control-list" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {openFindings.map((finding) => (
                <ChallengeFindingRow
                  key={finding.id}
                  assessmentId={assessmentId}
                  finding={finding}
                  canEdit={canEdit}
                  canAccept={canAccept}
                  onChanged={refresh}
                />
              ))}
            </div>
          )}
        </section>

        {closedFindings.length > 0 && (
          <section className="workflow-card">
            <div className="workflow-card-header">
              <div>
                <h2>Resolved &amp; Accepted Findings</h2>
              </div>
            </div>
            <div className="control-list" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {closedFindings.map((finding) => (
                <ChallengeFindingRow
                  key={finding.id}
                  assessmentId={assessmentId}
                  finding={finding}
                  canEdit={canEdit}
                  canAccept={canAccept}
                  onChanged={refresh}
                />
              ))}
            </div>
          </section>
        )}
      </div>

      <aside className="controls-sidebar">
        <section className="residual-risk-card challenge-review-status-card">
          <h2>Challenge Review Status</h2>
          <div className="residual-risk-content">
            <div className="residual-risk-row">
              <span>OPEN HIGH-SEVERITY FINDINGS</span>
              <strong className={review.high_severity_open_count > 0 ? "reduction-value" : ""}>
                {review.high_severity_open_count}
              </strong>
            </div>
            <div className="residual-risk-row">
              <span>TOTAL FINDINGS</span>
              <strong>{review.findings.length}</strong>
            </div>
          </div>
        </section>

        {review.high_severity_open_count > 0 && (
          <section className="control-warning-card">
            <span>⚠ COMMITTEE SUBMISSION BLOCKED</span>
            <h3>Resolve or accept high-severity findings</h3>
            <p>
              A manager cannot submit this assessment to committee while an
              unresolved high-severity challenge finding remains open (R11.6).
            </p>
          </section>
        )}
      </aside>
    </div>
  );
}

interface ChallengeFindingRowProps {
  assessmentId: number;
  finding: ChallengeFindingRecord;
  canEdit: boolean;
  canAccept: boolean;
  onChanged: () => Promise<void>;
}

function ChallengeFindingRow({ assessmentId, finding, canEdit, canAccept, onChanged }: ChallengeFindingRowProps) {
  const [mode, setMode] = useState<"none" | "resolve" | "accept">("none");
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // G-4 (2026-10-03): only MEDIUM findings may be accepted, as a Committee
  // exception; an acceptance recorded before that no longer counts.
  const acceptable = finding.severity === "MEDIUM";
  const legacyAcceptance = finding.resolution_status === "ACCEPTED" && finding.acceptance_authority !== "COMMITTEE";
  const unsettled = finding.resolution_status === "OPEN" || legacyAcceptance;
  const showResolve = canEdit && unsettled;
  const showAccept = canAccept && acceptable && unsettled;

  async function handleResolve() {
    if (!note.trim()) {
      setError("A resolution note is required.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await resolveChallengeFinding(assessmentId, finding.id, note);
      setMode("none");
      setNote("");
      await onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to resolve finding");
    } finally {
      setSaving(false);
    }
  }

  async function handleAccept() {
    if (!note.trim()) {
      setError("A reason is required to accept this finding.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await acceptChallengeFinding(assessmentId, finding.id, note);
      setMode("none");
      setNote("");
      await onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to accept finding");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="control-item" style={{ flexDirection: "column", alignItems: "stretch" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div className="control-item-main">
          <h3 style={{ fontSize: 14 }}>{finding.description}</h3>
          <p>
            {finding.related_section}
            {finding.supporting_evidence ? ` • ${finding.supporting_evidence}` : ""}
            {finding.recommended_action ? ` • Recommended: ${finding.recommended_action}` : ""}
          </p>
          {finding.resolution_status === "RESOLVED" && (
            <p>
              Resolved by {finding.resolved_by} — {finding.resolution_note}
            </p>
          )}
          {finding.resolution_status === "ACCEPTED" && (
            <p>
              {legacyAcceptance ? "Accepted under the earlier rule by " : "Accepted as a Committee exception by "}
              {finding.accepted_by} — {finding.accepted_reason}
              {legacyAcceptance &&
                (acceptable
                  ? " (no longer counts: the Committee must accept it, or it must be resolved)"
                  : " (no longer counts: high/critical findings must be resolved)")}
            </p>
          )}
        </div>
        <span
          className={`control-status ${
            finding.severity === "HIGH" || finding.severity === "CRITICAL"
              ? "not-validated"
              : "partially-effective"
          }`}
        >
          <RiskLevelIcon level={finding.severity} />{" "}
          {finding.severity}
        </span>
      </div>

      {(showResolve || showAccept) && (
        <div style={{ marginTop: 6 }}>
          {mode === "none" ? (
            <div style={{ display: "flex", gap: 8 }}>
              {showResolve && (
                <button className="secondary-button" onClick={() => setMode("resolve")}>
                  Resolve
                </button>
              )}
              {showAccept && (
                <button className="secondary-button" onClick={() => setMode("accept")}>
                  Accept
                </button>
              )}
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 4 }}>
              <textarea aria-label={mode === "resolve" ? "How this finding was resolved" : "Reason for accepting this finding"}
                placeholder={
                  mode === "resolve"
                    ? "Describe how this finding was resolved..."
                    : "Reason for accepting this finding without resolving it..."
                }
                value={note}
                onChange={(e) => setNote(e.target.value)}
                rows={2}
              />
              {error && <p role="alert" style={{ color: "#b91c1c" }}>{error}</p>}
              <div style={{ display: "flex", gap: 8 }}>
                <button
                  className="primary-button"
                  onClick={mode === "resolve" ? handleResolve : handleAccept}
                  disabled={saving}
                >
                  {saving ? "Saving..." : mode === "resolve" ? "Confirm resolution" : "Confirm acceptance"}
                </button>
                <button
                  className="secondary-button"
                  onClick={() => {
                    setMode("none");
                    setError(null);
                  }}
                  disabled={saving}
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* =========================================
   STAGE 13 -- CONDITIONS AND REMEDIATION TRACKING
   Unified open/closed action-item list for an assessment, covering
   control gaps, committee conditions, missing evidence, policy
   exceptions, vendor remediation, and monitoring enhancements (R13.1).
   Sources with their own detector (control gaps, committee conditions,
   missing evidence) are pulled in via "Sync"; the other three are added
   directly here.
   ========================================= */

const CLOSURE_APPROVAL_SOURCES = new Set([
  "COMMITTEE_CONDITION",
  "POLICY_EXCEPTION",
  "VENDOR_REMEDIATION",
]);

function actionItemSourceLabel(sourceType: string): string {
  return (
    ACTION_ITEM_SOURCE_TYPES.find((s) => s.value === sourceType)?.label ?? sourceType
  );
}

function isActionItemOpen(status: string): boolean {
  return status !== "COMPLETED" && status !== "CANCELLED";
}

function ActionItemsPanel({
  assessmentId,
  canManage,
  currentUserRole,
}: {
  assessmentId: number;
  canManage: boolean;
  currentUserRole?: string;
}) {
  const [items, setItems] = useState<ActionItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<"open" | "closed" | "all">("open");
  const [error, setError] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);

  const [showCreate, setShowCreate] = useState(false);
  const [newSourceType, setNewSourceType] = useState("MONITORING_ENHANCEMENT");
  const [newTitle, setNewTitle] = useState("");
  const [newOwner, setNewOwner] = useState("");
  const [newDepartment, setNewDepartment] = useState("");
  const [newDueDate, setNewDueDate] = useState("");
  const [newPriority, setNewPriority] = useState("MEDIUM");
  const [creating, setCreating] = useState(false);

  const [closureDrafts, setClosureDrafts] = useState<Record<number, string>>({});

  const canDecideClosure =
    currentUserRole === "FCRM_ANALYST" ||
    currentUserRole === "MANAGER" ||
    currentUserRole === "ADMIN" ||
    currentUserRole === "COMMITTEE_MEMBER";

  function refresh() {
    setLoading(true);
    getActionItems(assessmentId, filter)
      .then(setItems)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load action items"))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assessmentId, filter]);

  async function handleSync() {
    setSyncing(true);
    setError(null);
    try {
      await syncActionItems(assessmentId);
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to sync action items");
    } finally {
      setSyncing(false);
    }
  }

  async function handleCreate() {
    if (!newTitle.trim()) {
      setError("A title is required.");
      return;
    }
    setCreating(true);
    setError(null);
    try {
      await createActionItem(assessmentId, {
        source_type: newSourceType,
        title: newTitle.trim(),
        owner: newOwner.trim() || null,
        department: newDepartment.trim() || null,
        due_date: newDueDate || null,
        priority: newPriority,
      });
      setNewTitle("");
      setNewOwner("");
      setNewDepartment("");
      setNewDueDate("");
      setShowCreate(false);
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create action item");
    } finally {
      setCreating(false);
    }
  }

  async function handleRequestClosure(item: ActionItem) {
    const evidence = (closureDrafts[item.id] ?? "").trim();
    if (!evidence) {
      setError("Completion evidence is required to close an action item.");
      return;
    }
    try {
      await requestActionItemClosure(assessmentId, item.id, evidence);
      setClosureDrafts((current) => ({ ...current, [item.id]: "" }));
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to request closure");
    }
  }

  async function handleClosureDecision(item: ActionItem, decision: "approve" | "reject") {
    try {
      await decideActionItemClosure(assessmentId, item.id, decision);
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to record closure decision");
    }
  }

  async function handleFieldUpdate(item: ActionItem, field: "owner" | "department" | "due_date" | "priority", value: string) {
    try {
      const updated = await updateActionItem(assessmentId, item.id, { [field]: value || null } as never);
      setItems((current) => current.map((i) => (i.id === item.id ? updated : i)));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update action item");
    }
  }

  return (
    <div style={{ marginTop: 16 }}>
      <div className="workflow-card-header">
        <div>
          <h3>Conditions & Remediation Actions</h3>
          <p>
            Trackable action items for control gaps, committee conditions, missing
            evidence, policy exceptions, vendor remediation, and monitoring
            enhancements.
          </p>
        </div>
      </div>

      <div className="form-actions" style={{ marginBottom: 8 }}>
        <select aria-label="Show action items" value={filter} onChange={(event) => setFilter(event.target.value as typeof filter)}>
          <option value="open">Open</option>
          <option value="closed">Closed</option>
          <option value="all">All</option>
        </select>

        {canManage && (
          <>
            <button type="button" className="secondary-button" onClick={handleSync} disabled={syncing}>
              {syncing ? "Syncing…" : "Sync from Gaps/Conditions/Evidence"}
            </button>
            <button type="button" className="secondary-button" onClick={() => setShowCreate((v) => !v)}>
              {showCreate ? "Cancel" : "+ New Action Item"}
            </button>
          </>
        )}
      </div>

      {error && <p className="risk-reason" role="alert" style={{ color: "#b91c1c" }}>{error}</p>}

      {showCreate && canManage && (
        <div className="form-group" style={{ border: "1px solid #e5e7eb", borderRadius: 6, padding: 12, marginBottom: 12 }}>
          <label>Source type</label>
          <select aria-label="Source type" value={newSourceType} onChange={(event) => setNewSourceType(event.target.value)}>
            {ACTION_ITEM_SOURCE_TYPES.map((s) => (
              <option key={s.value} value={s.value}>{s.label}</option>
            ))}
          </select>

          <label>Title</label>
          <input aria-label="Title" type="text" value={newTitle} onChange={(event) => setNewTitle(event.target.value)} />

          <label>Owner</label>
          <input aria-label="Owner" type="text" value={newOwner} onChange={(event) => setNewOwner(event.target.value)} />

          <label>Department</label>
          <input aria-label="Department" type="text" value={newDepartment} onChange={(event) => setNewDepartment(event.target.value)} />

          <label>Due date</label>
          <input aria-label="Due date" type="date" value={newDueDate} onChange={(event) => setNewDueDate(event.target.value)} />

          <label>Priority</label>
          <select aria-label="Priority" value={newPriority} onChange={(event) => setNewPriority(event.target.value)}>
            {ACTION_ITEM_PRIORITIES.map((p) => (
              <option key={p} value={p}>{p}</option>
            ))}
          </select>

          <div className="form-actions">
            <button type="button" className="primary-button" onClick={handleCreate} disabled={creating}>
              {creating ? "Creating…" : "Create Action Item"}
            </button>
          </div>
        </div>
      )}

      {loading ? (
        <div className="workflow-empty">Loading action items…</div>
      ) : items.length === 0 ? (
        <div className="workflow-empty">No {filter === "all" ? "" : filter} action items.</div>
      ) : (
        <table className="risk-table" style={{ width: "100%" }}>
          <thead>
            <tr>
              <th>Source</th>
              <th>Title</th>
              <th>Owner</th>
              <th>Department</th>
              <th>Due</th>
              <th>Priority</th>
              <th>Status</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.id} style={item.escalated ? { background: "#fef2f2" } : undefined}>
                <td>{actionItemSourceLabel(item.source_type)}</td>
                <td>
                  {item.title}
                  {item.escalated && (
                    <div style={{ color: "#b91c1c", fontSize: 12 }}>
                      ESCALATED — {item.escalation_note}
                    </div>
                  )}
                </td>
                <td>
                  {canManage ? (
                    <input aria-label={`Owner for ${item.title}`}
                      type="text"
                      defaultValue={item.owner ?? ""}
                      style={{ width: 100 }}
                      onBlur={(event) => handleFieldUpdate(item, "owner", event.target.value)}
                    />
                  ) : (
                    item.owner ?? "—"
                  )}
                </td>
                <td>
                  {canManage ? (
                    <input aria-label={`Department for ${item.title}`}
                      type="text"
                      defaultValue={item.department ?? ""}
                      style={{ width: 100 }}
                      onBlur={(event) => handleFieldUpdate(item, "department", event.target.value)}
                    />
                  ) : (
                    item.department ?? "—"
                  )}
                </td>
                <td>
                  {canManage ? (
                    <input aria-label={`Due date for ${item.title}`}
                      type="date"
                      defaultValue={item.due_date ?? ""}
                      onBlur={(event) => handleFieldUpdate(item, "due_date", event.target.value)}
                    />
                  ) : (
                    item.due_date ?? "—"
                  )}
                </td>
                <td>
                  {canManage ? (
                    <select aria-label={`Priority for ${item.title}`}
                      value={item.priority}
                      onChange={(event) => handleFieldUpdate(item, "priority", event.target.value)}
                    >
                      {ACTION_ITEM_PRIORITIES.map((p) => (
                        <option key={p} value={p}>{p}</option>
                      ))}
                    </select>
                  ) : (
                    item.priority
                  )}
                </td>
                <td>{item.status.replace(/_/g, " ")}</td>
                <td>
                  {item.status === "PENDING_CLOSURE_APPROVAL" ? (
                    canDecideClosure ? (
                      <div className="form-actions">
                        <button type="button" className="secondary-button" onClick={() => handleClosureDecision(item, "approve")}>
                          Approve
                        </button>
                        <button type="button" className="secondary-button" onClick={() => handleClosureDecision(item, "reject")}>
                          Reject
                        </button>
                      </div>
                    ) : (
                      "Awaiting reviewer approval"
                    )
                  ) : isActionItemOpen(item.status) && canManage ? (
                    <div>
                      <textarea aria-label="Completion evidence"
                        rows={2}
                        placeholder="Completion evidence"
                        value={closureDrafts[item.id] ?? ""}
                        onChange={(event) =>
                          setClosureDrafts((current) => ({ ...current, [item.id]: event.target.value }))
                        }
                        style={{ width: 160 }}
                      />
                      <button type="button" className="secondary-button" onClick={() => handleRequestClosure(item)}>
                        {CLOSURE_APPROVAL_SOURCES.has(item.source_type) ? "Request Closure" : "Complete"}
                      </button>
                    </div>
                  ) : item.completion_evidence ? (
                    <span title={item.completion_evidence}>Evidence on file</span>
                  ) : (
                    "—"
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

interface AuditCompliancePanelProps {
  assessmentId: number;
  isFinalDecision: boolean;
  isAdmin: boolean;
  canExport: boolean;
}

// Stage 16 (R16.2-R16.3): explain-the-rating and export-the-package for
// one assessment. (P5: retention and legal holds moved to
// RetentionHoldPanel, shown on the Decision step.) Self-fetches its own
// data (same pattern as ChallengeReviewStage above) since none of it is
// part of the data this workflow's main effect already loads.
function AuditCompliancePanel({
  assessmentId,
  canExport,
}: AuditCompliancePanelProps) {
  const [explanation, setExplanation] = useState<AssessmentExplanation | null>(null);
  const [showExplanation, setShowExplanation] = useState(false);
  const [explainLoading, setExplainLoading] = useState(false);
  const [explainError, setExplainError] = useState<string | null>(null);

  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  async function handleExplain() {
    setShowExplanation(true);
    if (explanation) return;
    setExplainLoading(true);
    setExplainError(null);
    try {
      setExplanation(await getAssessmentExplanation(assessmentId));
    } catch (error) {
      setExplainError(error instanceof Error ? error.message : "Failed to load explanation");
    } finally {
      setExplainLoading(false);
    }
  }

  async function handleExport() {
    setExporting(true);
    setExportError(null);
    try {
      const blob = await downloadAuditExport(assessmentId);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `assessment-${assessmentId}-audit-export.json`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (error) {
      setExportError(error instanceof Error ? error.message : "Failed to export audit package");
    } finally {
      setExporting(false);
    }
  }

  return (
    <div>
      <h3>Audit &amp; Explainability</h3>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button className="secondary-button" onClick={handleExplain} disabled={explainLoading}>
          {explainLoading ? "Loading..." : "Explain this rating"}
        </button>

        {canExport && (
          <button className="secondary-button" onClick={handleExport} disabled={exporting}>
            {exporting ? "Exporting..." : "Export audit package"}
          </button>
        )}
      </div>

      {explainError && <p role="alert" style={{ color: "#b91c1c" }}>{explainError}</p>}

      <DecisionRecordPanel assessmentId={assessmentId} />

      {showExplanation && explanation && (
        <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 10 }}>
          {explanation.sections.map((section) => (
            <div key={section.rating} className="control-item" style={{ flexDirection: "column", alignItems: "stretch" }}>
              <strong>
                {section.rating}
                {section.value != null ? `: ${section.value}` : ""}
                {section.level ? ` (${section.level})` : ""}
              </strong>
              <p>{section.explanation}</p>
            </div>
          ))}

          {explanation.overrides.length > 0 && (
            <div>
              <strong>Overrides</strong>
              <ul>
                {explanation.overrides.map((override, index) => (
                  <li key={index}>
                    {override.section} / {override.field_name}: {override.ai_value ?? "—"} →{" "}
                    {override.human_value} — {override.reason} ({override.overridden_by},{" "}
                    {formatDateTime(override.created_at)})
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Stage 19: facts vs assumptions vs recommendations vs decisions. */}
          <ExplainabilityPanel key={assessmentId} assessmentId={assessmentId} />
        </div>
      )}

      {exportError && <p role="alert" style={{ color: "#b91c1c" }}>{exportError}</p>}

    </div>
  );
}

export default AssessmentWorkflow;
