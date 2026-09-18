import { useState, useMemo } from "react";
import type { Assessment } from "../api/assessments";

interface AssessmentsPageProps {
  assessments: Assessment[];
  onSelectAssessment: (assessment: Assessment) => void;
  onCreateAssessment: () => void;
}

const PAGE_SIZE = 10;

function AssessmentsPage({
  assessments,
  onSelectAssessment,
  onCreateAssessment,
}: AssessmentsPageProps) {
  const [currentPage, setCurrentPage] = useState(1);

  const totalPages = Math.max(1, Math.ceil(assessments.length / PAGE_SIZE));

  // Keep currentPage valid if assessments shrink (e.g. after filtering/deleting)
  const safePage = Math.min(currentPage, totalPages);

  const paginatedAssessments = useMemo(() => {
    const start = (safePage - 1) * PAGE_SIZE;
    return assessments.slice(start, start + PAGE_SIZE);
  }, [assessments, safePage]);

  function goToPage(page: number) {
    setCurrentPage(Math.min(Math.max(page, 1), totalPages));
  }

  return (
    <>
      <div className="page-header">
        <div>
          <h2>Assessments</h2>
          <p>
            View and manage all business change risk assessments.
          </p>
        </div>

        <button
          className="primary-button"
          onClick={onCreateAssessment}
        >
          + New Assessment
        </button>
      </div>

      <section className="content-card">
        <div className="card-header">
          <div>
            <h3>All Assessments</h3>
            <p>
              Review submitted changes and their current risk status.
            </p>
          </div>
        </div>

        {assessments.length === 0 ? (
          <div className="empty-state">
            <div className="empty-icon">✓</div>

            <h3>No assessments yet</h3>

            <p>
              Create your first assessment to begin evaluating
              business change risk.
            </p>

            <button
              className="primary-button"
              onClick={onCreateAssessment}
            >
              Create Assessment
            </button>
          </div>
        ) : (
          <>
            <div className="assessment-list">
              {paginatedAssessments.map((assessment) => (
                <div
                  className="assessment-row"
                  key={assessment.id}
                  onClick={() => onSelectAssessment(assessment)}
                >
                  <div>
                    <h4>{assessment.title}</h4>

                    <span className="change-type">
                      {assessment.change_type}
                    </span>
                  </div>

                  <div className="assessment-status">
                    <span>{assessment.status}</span>

                    {assessment.overall_score != null ? (
                      <>
                        <strong>{assessment.overall_score}</strong>

                        <span>
                          {assessment.risk_level ?? "NOT ANALYZED"}
                        </span>
                      </>
                    ) : (
                      <span>NOT ANALYZED</span>
                    )}
                  </div>
                </div>
              ))}
            </div>

            <div className="pagination">
              <button
                className="pagination-button"
                onClick={() => goToPage(safePage - 1)}
                disabled={safePage === 1}
              >
                ← Previous
              </button>

              <span className="pagination-info">
                Page {safePage} of {totalPages}
              </span>

              <button
                className="pagination-button"
                onClick={() => goToPage(safePage + 1)}
                disabled={safePage === totalPages}
              >
                Next →
              </button>
            </div>
          </>
        )}
      </section>
    </>
  );
}

export default AssessmentsPage;