import { useEffect, useState } from "react";
import {
  getRiskResults,
  getAssessmentDocuments,
  getAssessmentIntelligence,
  getAssessmentAudit,
  updateAssessmentStatus,
  analyzeAssessment,
  updateAssessment,
  uploadAssessmentDocument,
} from "../api/assessments";

import type {
  Assessment,
  AssessmentDocument,
  RiskResult,
  AssessmentIntelligence,
  AuditEvent,
} from "../api/assessments";

import "./AssessmentDetails.css";

interface AssessmentDetailsProps {
  assessment: Assessment;
  onBack: () => void;
  onStatusUpdated: (assessment: Assessment) => void;
}

function AssessmentDetails({
  assessment,
  onBack,
  onStatusUpdated,
}: AssessmentDetailsProps) {
  const [riskResults, setRiskResults] = useState<RiskResult[]>([]);
  const [documents, setDocuments] = useState<AssessmentDocument[]>([]);
  const [intelligence, setIntelligence] =
    useState<AssessmentIntelligence | null>(null);
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([]);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [updatingStatus, setUpdatingStatus] = useState(false);
  const [analyzing, setAnalyzing] = useState(false);

  const [isEditing, setIsEditing] = useState(false);
  const [savingEdit, setSavingEdit] = useState(false);

  const [uploading, setUploading] = useState(false);

  const [editForm, setEditForm] = useState({
    title: assessment.title,
    change_type: assessment.change_type,
    description: assessment.description,
    evidence: assessment.evidence,
  });

  /*
   * Editing assessment details is allowed in DRAFT or REMEDIATION.
   * Uploading a new source document is allowed only in REMEDIATION.
   */
  const canEdit =
    assessment.status === "DRAFT" || assessment.status === "REMEDIATION";

  const canUploadDocuments = assessment.status === "REMEDIATION";

  /*
   * Load all assessment-related data. Called on mount/id change,
   * and again after any action that changes server-side state
   * (analyze, status update, edit, upload) so the view stays in
   * sync without requiring a manual page refresh.
   */
  async function loadAssessmentData() {
    try {
      setLoading(true);
      setError("");

      const [
        riskResultsResult,
        documentsResult,
        auditResult,
        intelligenceResult,
      ] = await Promise.allSettled([
        getRiskResults(assessment.id),
        getAssessmentDocuments(assessment.id),
        getAssessmentAudit(assessment.id),
        getAssessmentIntelligence(assessment.id),
      ]);

      // Risk results
      if (riskResultsResult.status === "fulfilled") {
        setRiskResults(riskResultsResult.value);
      } else {
        console.error(
          "Unable to load risk results:",
          riskResultsResult.reason
        );
        setRiskResults([]);
      }

      // Documents
      if (documentsResult.status === "fulfilled") {
        setDocuments(documentsResult.value);
      } else {
        console.error(
          "Unable to load assessment documents:",
          documentsResult.reason
        );
        setDocuments([]);
      }

      // Audit
      if (auditResult.status === "fulfilled") {
        setAuditEvents(auditResult.value);
      } else {
        console.error(
          "Unable to load assessment audit:",
          auditResult.reason
        );
        setAuditEvents([]);
      }

      // Intelligence — a 404 here just means this assessment has
      // no intelligence record (e.g. created manually), which is
      // expected, not an error.
      if (intelligenceResult.status === "fulfilled") {
        setIntelligence(intelligenceResult.value);
      } else {
        setIntelligence(null);
      }
    } catch (err) {
      console.error(err);
      setError("Unable to load assessment details.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadAssessmentData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assessment.id]);

  /*
   * Update assessment status.
   */
  async function handleStatusUpdate(status: string) {
    try {
      setUpdatingStatus(true);
      setError("");

      const updatedAssessment = await updateAssessmentStatus(
        assessment.id,
        status
      );

      onStatusUpdated(updatedAssessment);

      await loadAssessmentData();
    } catch (err) {
      console.error(err);
      setError("Unable to update assessment status.");
    } finally {
      setUpdatingStatus(false);
    }
  }

  /*
   * Start assessment analysis.
   */
  async function handleAnalyze() {
    try {
      setAnalyzing(true);
      setError("");

      const updatedAssessment = await analyzeAssessment(assessment.id);

      onStatusUpdated(updatedAssessment);

      await loadAssessmentData();
    } catch (err) {
      console.error(err);
      setError("Unable to analyze assessment.");
    } finally {
      setAnalyzing(false);
    }
  }

  /*
   * Save assessment edits.
   */
  async function handleSaveEdit() {
    try {
      setSavingEdit(true);
      setError("");

      const updatedAssessment = await updateAssessment(
        assessment.id,
        editForm
      );

      onStatusUpdated(updatedAssessment);

      setIsEditing(false);

      await loadAssessmentData();
    } catch (err) {
      console.error(err);
      setError("Unable to save assessment changes.");
    } finally {
      setSavingEdit(false);
    }
  }

  /*
   * Upload a source document.
   */
  async function handleUploadDocument(
    e: React.ChangeEvent<HTMLInputElement>
  ) {
    const file = e.target.files?.[0];

    if (!file) {
      return;
    }

    try {
      setUploading(true);
      setError("");

      const newDocument = await uploadAssessmentDocument(
        assessment.id,
        file
      );

      setDocuments((current) => [newDocument, ...current]);

      await loadAssessmentData();
    } catch (err) {
      console.error(err);
      setError("Unable to upload document.");
    } finally {
      setUploading(false);

      /*
       * Reset the input so the same file can be
       * selected again if needed.
       */
      e.target.value = "";
    }
  }

  return (
    <div className="details-page">
      {/* Back */}
      <button className="back-button" onClick={onBack}>
        ← Back to Dashboard
      </button>

      {/* Header */}
      <div className="details-header">
        <div>
          <span className="change-type">{assessment.change_type}</span>

          <h2>{assessment.title}</h2>

          <p>{assessment.description}</p>
        </div>

        <div className="risk-summary">
          <span>Overall Risk</span>

          <strong>{assessment.overall_score ?? "-"}</strong>

          <span>{assessment.risk_level ?? "NOT ANALYZED"}</span>
        </div>
      </div>

      {/* Risk Breakdown */}
      <section className="content-card">
        <div className="card-header">
          <h3>Risk Breakdown</h3>

          <p>Risk assessment across applicable dimensions.</p>
        </div>

        {loading && (
          <div className="details-loading">Loading risk results...</div>
        )}

        {error && <div className="details-loading">{error}</div>}

        {!loading && !error && (
          <div className="risk-results">
            {riskResults.length === 0 ? (
              <div className="empty-state">
                No risk results are available yet.
              </div>
            ) : (
              riskResults.map((result) => (
                <div className="risk-result-row" key={result.id}>
                  <div className="risk-dimension">
                    <h4>{result.dimension}</h4>

                    <p>{result.reason}</p>
                  </div>

                  <div className="risk-score">
                    <strong>{result.score}</strong>

                    <span
                      className={`severity ${result.severity.toLowerCase()}`}
                    >
                      {result.severity}
                    </span>
                  </div>
                </div>
              ))
            )}
          </div>
        )}
      </section>

      {/* Assessment Details */}
      <section className="content-card evidence-card">
        <div className="card-header">
          <h3>Assessment Details</h3>

          <p>
            {canEdit
              ? "Supporting information for this assessment. You can edit these details."
              : "Supporting information provided for this assessment."}
          </p>

          {canEdit && !isEditing && (
            <button
              className="secondary-button"
              onClick={() => {
                setEditForm({
                  title: assessment.title,
                  change_type: assessment.change_type,
                  description: assessment.description,
                  evidence: assessment.evidence,
                });

                setIsEditing(true);
              }}
            >
              Edit
            </button>
          )}
        </div>

        {isEditing ? (
          <div className="form-group" style={{ padding: 24 }}>
            <label>Title</label>

            <input
              value={editForm.title}
              onChange={(e) =>
                setEditForm({ ...editForm, title: e.target.value })
              }
            />

            <label>Change Type</label>

            <input
              value={editForm.change_type}
              onChange={(e) =>
                setEditForm({
                  ...editForm,
                  change_type: e.target.value,
                })
              }
            />

            <label>Description</label>

            <textarea
              rows={3}
              value={editForm.description}
              onChange={(e) =>
                setEditForm({
                  ...editForm,
                  description: e.target.value,
                })
              }
            />

            <label>Evidence</label>

            <textarea
              rows={4}
              value={editForm.evidence}
              onChange={(e) =>
                setEditForm({
                  ...editForm,
                  evidence: e.target.value,
                })
              }
            />

            <div className="form-actions">
              <button
                className="secondary-button"
                onClick={() => setIsEditing(false)}
                disabled={savingEdit}
              >
                Cancel
              </button>

              <button
                className="primary-button"
                onClick={handleSaveEdit}
                disabled={savingEdit}
              >
                {savingEdit ? "Saving..." : "Save Changes"}
              </button>
            </div>
          </div>
        ) : (
          <div className="evidence-content">{assessment.evidence}</div>
        )}
      </section>

      {/* Business Intelligence */}
      {intelligence && (
        <section className="content-card intelligence-card">
          <div className="card-header">
            <h3>Business Intelligence</h3>

            <p>Structured information extracted from the assessment source.</p>
          </div>

          <div className="intelligence-grid">
            <div className="intelligence-item">
              <span>Business Line</span>

              <strong>{intelligence.business_line || "Not available"}</strong>
            </div>

            <div className="intelligence-item">
              <span>Transaction Volume</span>

              <strong>
                {intelligence.transaction_volume || "Not available"}
              </strong>
            </div>

            <div className="intelligence-item">
              <span>Average Transaction</span>

              <strong>
                {intelligence.average_transaction_size || "Not available"}
              </strong>
            </div>

            <div className="intelligence-item">
              <span>Maximum Transaction</span>

              <strong>
                {intelligence.maximum_transaction_limit || "Not available"}
              </strong>
            </div>

            <div className="intelligence-item intelligence-wide">
              <span>Channels</span>

              <div className="tag-list">
                {intelligence.channels.map((item) => (
                  <span key={item} className="info-tag">
                    {item}
                  </span>
                ))}
              </div>
            </div>

            <div className="intelligence-item intelligence-wide">
              <span>Countries</span>

              <div className="tag-list">
                {intelligence.countries.map((item) => (
                  <span key={item} className="info-tag">
                    {item}
                  </span>
                ))}
              </div>
            </div>

            <div className="intelligence-item intelligence-wide">
              <span>Customer Segments</span>

              <div className="tag-list">
                {intelligence.customer_segments.map((item) => (
                  <span key={item} className="info-tag">
                    {item}
                  </span>
                ))}
              </div>
            </div>

            <div className="intelligence-item intelligence-wide">
              <span>Third-Party Vendors</span>

              <div className="tag-list">
                {intelligence.third_party_vendors.map((item) => (
                  <span key={item} className="info-tag">
                    {item}
                  </span>
                ))}
              </div>
            </div>

            <div className="intelligence-item intelligence-wide">
              <span>Data Shared</span>

              <div className="tag-list">
                {intelligence.data_shared.map((item) => (
                  <span key={item} className="info-tag">
                    {item}
                  </span>
                ))}
              </div>
            </div>

            <div className="intelligence-item intelligence-wide">
              <span>Technologies</span>

              <div className="tag-list">
                {intelligence.technologies.map((item) => (
                  <span key={item} className="info-tag">
                    {item}
                  </span>
                ))}
              </div>
            </div>

            <div className="intelligence-item intelligence-wide">
              <span>Regulatory Considerations</span>

              <div className="tag-list">
                {intelligence.regulatory_considerations.map((item) => (
                  <span key={item} className="info-tag">
                    {item}
                  </span>
                ))}
              </div>
            </div>

            <div className="intelligence-item intelligence-wide">
              <span>Existing Controls</span>

              <div className="tag-list">
                {intelligence.existing_controls.map((item) => (
                  <span key={item} className="info-tag">
                    {item}
                  </span>
                ))}
              </div>
            </div>

            <div className="intelligence-item intelligence-wide">
              <span>Additional Risk Factors</span>

              <div className="tag-list">
                {intelligence.additional_risk_factors.map((item) => (
                  <span key={item} className="info-tag risk-tag">
                    {item}
                  </span>
                ))}
              </div>
            </div>
          </div>
        </section>
      )}

      {/* Source Documents */}
      <section className="content-card document-card">
        <div className="card-header">
          <h3>Source Documents</h3>

          <p>Documents used to create this assessment.</p>
        </div>

        {canUploadDocuments && (
          <div style={{ padding: "0 24px 20px" }}>
            <label
              className="secondary-button"
              style={{
                display: "inline-block",
                cursor: uploading ? "not-allowed" : "pointer",
              }}
            >
              {uploading ? "Uploading..." : "+ Upload Document"}

              <input
                type="file"
                accept=".docx,.xlsx,.pdf,.txt,.csv"
                onChange={handleUploadDocument}
                disabled={uploading}
                style={{ display: "none" }}
              />
            </label>
          </div>
        )}

        {documents.length === 0 ? (
          <div className="empty-state">
            No source documents are attached to this assessment.
          </div>
        ) : (
          <div className="document-list">
            {documents.map((document) => (
              <a
                key={document.id}
                className="document-item"
                href={`http://127.0.0.1:8000/api/assessments/documents/${document.id}/file`}
                target="_blank"
                rel="noopener noreferrer"
                style={{ textDecoration: "none", color: "inherit" }}
              >
                <div className="document-icon">📄</div>

                <div className="document-info">
                  <strong>{document.filename}</strong>
                  <span>{document.file_type.toUpperCase()}</span>
                </div>
              </a>
            ))}
          </div>
        )}
      </section>

      {/* Audit History */}
      <section className="content-card audit-card">
        <div className="card-header">
          <h3>Audit History</h3>

          <p>A chronological record of actions performed on this assessment.</p>
        </div>

        {auditEvents.length === 0 ? (
          <div className="empty-state">No audit events recorded yet.</div>
        ) : (
          <div className="audit-list">
            {auditEvents.map((event) => (
              <div className="audit-item" key={event.id}>
                <div className="audit-icon">
                  {event.action === "APPROVAL"
                    ? "✓"
                    : event.action === "REJECTION"
                    ? "!"
                    : event.action === "REMEDIATION"
                    ? "↻"
                    : "•"}
                </div>

                <div className="audit-content">
                  <div className="audit-header">
                    <strong>{event.action.replace("_", " ")}</strong>

                    <span>
                      {new Date(event.created_at).toLocaleString()}
                    </span>
                  </div>

                  {event.previous_status || event.new_status ? (
                    <div className="audit-status">
                      {event.previous_status || "—"}

                      <span>→</span>

                      {event.new_status || "—"}
                    </div>
                  ) : null}

                  {event.details && <p>{event.details}</p>}

                  <span className="audit-actor">
                    By {event.actor || "System"}
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Review Decision */}
      <section className="content-card review-card">
        <div className="card-header">
          <h3>Review Decision</h3>

          <p>Complete the human review for this assessment.</p>
        </div>

        {/* DRAFT */}
        {assessment.status === "DRAFT" ? (
          <div className="review-actions">
            <button
              className="review-button approve"
              onClick={handleAnalyze}
              disabled={analyzing}
            >
              {analyzing ? "Analyzing..." : "Start Analyze"}
            </button>
          </div>
        ) : /* READY FOR REVIEW */
        assessment.status === "READY_FOR_REVIEW" ? (
          <div className="review-actions">
            <button
              className="review-button approve"
              onClick={() => handleStatusUpdate("UNDER_REVIEW")}
              disabled={updatingStatus}
            >
              {updatingStatus ? "Starting..." : "Start Review"}
            </button>
          </div>
        ) : /* UNDER REVIEW */
        assessment.status === "UNDER_REVIEW" ? (
          <div className="review-actions">
            <button
              className="review-button approve"
              onClick={() => handleStatusUpdate("APPROVED")}
              disabled={updatingStatus}
            >
              {updatingStatus ? "Updating..." : "Approve"}
            </button>

            <button
              className="review-button remediation"
              onClick={() => handleStatusUpdate("REMEDIATION")}
              disabled={updatingStatus}
            >
              {updatingStatus ? "Updating..." : "Send for Remediation"}
            </button>

            <button
              className="review-button reject"
              onClick={() => handleStatusUpdate("REJECTED")}
              disabled={updatingStatus}
            >
              {updatingStatus ? "Updating..." : "Reject"}
            </button>
          </div>
        ) : /* REMEDIATION */
        assessment.status === "REMEDIATION" ? (
          <div className="review-completed">
            <div className="review-status-icon">↻</div>

            <div>
              <strong>Remediation Required</strong>

              <p>
                Update the assessment details or upload revised documents,
                then restart analysis.
              </p>

              <div className="review-actions" style={{ marginTop: 12 }}>
                <button
                  className="review-button approve"
                  onClick={() => handleStatusUpdate("DRAFT")}
                  disabled={updatingStatus}
                >
                  {updatingStatus ? "Updating..." : "Revise & Restart"}
                </button>
              </div>
            </div>
          </div>
        ) : (
          /* APPROVED / REJECTED / OTHER */
          <div className="review-completed">
            <div className="review-status-icon">✓</div>

            <div>
              <strong>
                {assessment.status === "APPROVED"
                  ? "Assessment Approved"
                  : assessment.status === "REJECTED"
                  ? "Assessment Rejected"
                  : `Status: ${assessment.status}`}
              </strong>

              <p>fv
                The current review decision is{" "}
                <strong>{assessment.status}</strong>.
              </p>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}

export default AssessmentDetails;