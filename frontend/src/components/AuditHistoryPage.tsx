import type { AuditEvent, Assessment } from "../api/assessments";

interface AuditHistoryPageProps {
  auditEvents: AuditEvent[];
  assessments: Assessment[];
  onSelectAssessment: (assessment: Assessment) => void;
}

function AuditHistoryPage({
  auditEvents,
  assessments,
  onSelectAssessment,
}: AuditHistoryPageProps) {
  function getAssessment(assessmentId: number): Assessment | undefined {
    return assessments.find(
      (assessment) => assessment.id === assessmentId
    );
  }

  function formatAction(action: string) {
    return action.replace(/_/g, " ");
  }

  function getActionClass(action: string) {
    switch (action) {
      case "CREATED":
        return "audit-badge created";

      case "ANALYSIS":
        return "audit-badge analysis";

      case "STATUS_CHANGE":
        return "audit-badge status";

      case "APPROVAL":
        return "audit-badge approval";

      case "REMEDIATION":
        return "audit-badge remediation";

      case "REJECTION":
        return "audit-badge rejection";

      default:
        return "audit-badge";
    }
  }

  function formatDate(date: string) {
    return new Date(date).toLocaleDateString("en-IN", {
      day: "2-digit",
      month: "short",
      year: "numeric",
    });
  }

  function formatTime(date: string) {
    return new Date(date).toLocaleTimeString("en-IN", {
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  return (
    <div className="audit-history-page">
      <div className="page-header">
        <div>
          <h2>Audit History</h2>
          <p>
            Track assessment creation, analysis, review decisions,
            and status changes.
          </p>
        </div>
      </div>

      <section className="content-card audit-history-card">
        <div className="card-header audit-history-header">
          <div>
            <h3>Assessment Activity</h3>
            <p>
              Complete audit trail of actions performed across
              risk assessments.
            </p>
          </div>

          <div className="audit-count">
            {auditEvents.length}{" "}
            {auditEvents.length === 1 ? "Event" : "Events"}
          </div>
        </div>

        {auditEvents.length === 0 ? (
          <div className="empty-state">
            <div className="empty-icon">✓</div>

            <h3>No audit history yet</h3>

            <p>
              Audit events will appear here when assessments are
              created, analyzed, reviewed, or updated.
            </p>
          </div>
        ) : (
          <div className="audit-table-container">
            <table className="audit-table">
              <thead>
                <tr>
                  <th className="assessment-column">
                    Assessment
                  </th>

                  <th className="action-column">
                    Action
                  </th>

                  <th className="status-column">
                    Status Change
                  </th>

                  <th className="actor-column">
                    Actor
                  </th>

                  <th className="date-column">
                    Date & Time
                  </th>
                  
                </tr>
              </thead>

              <tbody>
                {auditEvents.map((event) => {
                  const assessment = getAssessment(
                    event.assessment_id
                  );

                  return (
                    <tr
                      key={event.id}
                      className="audit-row"
                    >
                      <td>
                        <div className="assessment-cell">
                          <div className="assessment-avatar">
                            {assessment?.title
                              ? assessment.title
                                  .charAt(0)
                                  .toUpperCase()
                              : "A"}
                          </div>

                          <div className="assessment-info">
                            <strong>
                              {assessment?.title ||
                                "Assessment"}
                            </strong>

                            <span>
                              Assessment #{event.assessment_id}
                            </span>
                          </div>
                        </div>
                      </td>

                      <td>
                        <span className={getActionClass(event.action)}>
                          {formatAction(event.action)}
                        </span>
                      </td>

                      <td>
                        {event.previous_status ||
                        event.new_status ? (
                          <div className="status-transition">
                            <span className="status-pill">
                              {event.previous_status || "—"}
                            </span>

                            <span className="transition-arrow">
                              →
                            </span>

                            <span className="status-pill">
                              {event.new_status || "—"}
                            </span>
                          </div>
                        ) : (
                          <span className="no-status">—</span>
                        )}
                      </td>

                      <td>
                        <div className="actor-cell">
                          <span className="actor-avatar">
                            {(event.actor || "System")
                              .charAt(0)
                              .toUpperCase()}
                          </span>

                          <span>
                            {event.actor || "System"}
                          </span>
                        </div>
                      </td>

                      <td>
                        <div className="date-cell">
                          <strong>
                            {formatDate(event.created_at)}
                          </strong>

                          <span>
                            {formatTime(event.created_at)}
                          </span>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

export default AuditHistoryPage;