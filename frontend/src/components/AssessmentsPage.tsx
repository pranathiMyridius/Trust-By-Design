import { useState, useMemo } from "react";
import {
  CHANGE_TYPES,
  LEGACY_CHANGE_TYPES,
  type Assessment,
} from "../api/assessments";
import { SlaBadge } from "./WorkflowPanel";
import AssessmentScore from "./AssessmentScore";
import { clickableProps } from "../utils/a11y";

interface AssessmentsPageProps {
  assessments: Assessment[];
  onSelectAssessment: (assessment: Assessment) => void;
  onCreateAssessment: () => void;
}

const PAGE_SIZE = 10;

const ALL_CHANGE_TYPES = [...CHANGE_TYPES, ...LEGACY_CHANGE_TYPES];

function changeTypeLabel(value: string): string {
  return ALL_CHANGE_TYPES.find((t) => t.value === value)?.label ?? value;
}

function AssessmentsPage({
  assessments,
  onSelectAssessment,
  onCreateAssessment,
}: AssessmentsPageProps) {
  const [currentPage, setCurrentPage] = useState(1);

  // Filters: created-on-or-after / created-on-or-before (inclusive date
  // range on `created_at`) and a change-type picklist.
  const [changeTypeFilter, setChangeTypeFilter] = useState("");
  const [createdFrom, setCreatedFrom] = useState("");
  const [createdTo, setCreatedTo] = useState("");
  // Stage 19 (Scalability): filter by legal entity / business unit.
  const [legalEntityFilter, setLegalEntityFilter] = useState("");
  const [businessUnitFilter, setBusinessUnitFilter] = useState("");

  const orgOptions = useMemo(() => {
    const entities = new Set<string>();
    const units = new Set<string>();
    for (const assessment of assessments) {
      if (assessment.legal_entity) entities.add(assessment.legal_entity);
      if (assessment.business_unit) units.add(assessment.business_unit);
    }
    return {
      entities: [...entities].sort(),
      units: [...units].sort(),
    };
  }, [assessments]);

  const changeTypesInUse = useMemo(() => {
    const known = new Set(ALL_CHANGE_TYPES.map((t) => t.value));
    const options = [...ALL_CHANGE_TYPES];
    for (const assessment of assessments) {
      if (!known.has(assessment.change_type)) {
        known.add(assessment.change_type);
        options.push({ value: assessment.change_type, label: assessment.change_type });
      }
    }
    return options;
  }, [assessments]);

  const filteredAssessments = useMemo(() => {
    return assessments.filter((assessment) => {
      if (changeTypeFilter && assessment.change_type !== changeTypeFilter) {
        return false;
      }

      if (legalEntityFilter && assessment.legal_entity !== legalEntityFilter) {
        return false;
      }

      if (businessUnitFilter && assessment.business_unit !== businessUnitFilter) {
        return false;
      }

      if (createdFrom || createdTo) {
        const createdDate = assessment.created_at.slice(0, 10); // YYYY-MM-DD
        if (createdFrom && createdDate < createdFrom) return false;
        if (createdTo && createdDate > createdTo) return false;
      }

      return true;
    });
  }, [assessments, changeTypeFilter, createdFrom, createdTo, legalEntityFilter, businessUnitFilter]);

  const hasActiveFilters = Boolean(
    changeTypeFilter || createdFrom || createdTo || legalEntityFilter || businessUnitFilter
  );

  function clearFilters() {
    setChangeTypeFilter("");
    setLegalEntityFilter("");
    setBusinessUnitFilter("");
    setCreatedFrom("");
    setCreatedTo("");
    setCurrentPage(1);
  }

  const totalPages = Math.max(1, Math.ceil(filteredAssessments.length / PAGE_SIZE));

  // Keep currentPage valid if the filtered set shrinks
  const safePage = Math.min(currentPage, totalPages);

  const paginatedAssessments = useMemo(() => {
    const start = (safePage - 1) * PAGE_SIZE;
    return filteredAssessments.slice(start, start + PAGE_SIZE);
  }, [filteredAssessments, safePage]);

  function goToPage(page: number) {
    setCurrentPage(Math.min(Math.max(page, 1), totalPages));
  }

  return (
    <div className="assessments-page">
      <div className="page-header assessments-header">
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

      <section className="content-card assessments-card">
        <div className="card-header assessments-card-header">
          <div>
            <h3>All Assessments</h3>
            <p>
              Review submitted changes and their current risk status.
            </p>
          </div>
        </div>

        {assessments.length > 0 && (
          <div className="assessments-filters">
            <div className="form-group assessments-filter">
              <label htmlFor="filter-change-type">Change type</label>
              <select
                id="filter-change-type"
                value={changeTypeFilter}
                onChange={(event) => {
                  setChangeTypeFilter(event.target.value);
                  setCurrentPage(1);
                }}
              >
                <option value="">All types</option>
                {changeTypesInUse.map((type) => (
                  <option key={type.value} value={type.value}>
                    {type.label}
                  </option>
                ))}
              </select>
            </div>

            {orgOptions.entities.length > 0 && (
              <div className="form-group assessments-filter">
                <label htmlFor="filter-legal-entity">Legal entity</label>
                <select
                  id="filter-legal-entity"
                  value={legalEntityFilter}
                  onChange={(event) => {
                    setLegalEntityFilter(event.target.value);
                    setCurrentPage(1);
                  }}
                >
                  <option value="">All legal entities</option>
                  {orgOptions.entities.map((entity) => (
                    <option key={entity} value={entity}>
                      {entity}
                    </option>
                  ))}
                </select>
              </div>
            )}

            {orgOptions.units.length > 0 && (
              <div className="form-group assessments-filter">
                <label htmlFor="filter-business-unit">Business unit</label>
                <select
                  id="filter-business-unit"
                  value={businessUnitFilter}
                  onChange={(event) => {
                    setBusinessUnitFilter(event.target.value);
                    setCurrentPage(1);
                  }}
                >
                  <option value="">All business units</option>
                  {orgOptions.units.map((unit) => (
                    <option key={unit} value={unit}>
                      {unit}
                    </option>
                  ))}
                </select>
              </div>
            )}

            <div className="form-group assessments-filter">
              <label htmlFor="filter-created-from">Created on/after</label>
              <input
                id="filter-created-from"
                type="date"
                value={createdFrom}
                onChange={(event) => {
                  setCreatedFrom(event.target.value);
                  setCurrentPage(1);
                }}
              />
            </div>

            <div className="form-group assessments-filter">
              <label htmlFor="filter-created-to">Created on/before</label>
              <input
                id="filter-created-to"
                type="date"
                value={createdTo}
                onChange={(event) => {
                  setCreatedTo(event.target.value);
                  setCurrentPage(1);
                }}
              />
            </div>

            {hasActiveFilters && (
              <button
                type="button"
                className="secondary-button"
                onClick={clearFilters}
              >
                Clear Filters
              </button>
            )}

            <span className="assessments-count" role="status" aria-live="polite">
              {filteredAssessments.length} of {assessments.length} assessment
              {assessments.length === 1 ? "" : "s"}
            </span>
          </div>
        )}

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
        ) : filteredAssessments.length === 0 ? (
          <div className="empty-state">
            <div className="empty-icon">✓</div>

            <h3>No assessments match these filters</h3>

            <p>Try widening the date range or choosing a different change type.</p>

            <button className="secondary-button" onClick={clearFilters}>
              Clear Filters
            </button>
          </div>
        ) : (
          <>
            <div className="assessment-list">
              {paginatedAssessments.map((assessment) => (
                <div
                  className="assessment-row"
                  key={assessment.id}
                  {...clickableProps(() => onSelectAssessment(assessment), {
                    label: `Open assessment ${assessment.title}`,
                  })}
                >
                  <div className="assessment-info">
                    <h4>{assessment.title}</h4>

                    <div className="assessment-tags">
                    {assessment.reference_id && (
                      <span className="change-type assessment-ref">
                        {assessment.reference_id}
                      </span>
                    )}

                    <span className="change-type">
                      {changeTypeLabel(assessment.change_type)}
                    </span>

                    {assessment.priority && (
                      <span className="change-type">
                        {assessment.priority} priority
                      </span>
                    )}

                    {assessment.is_draft && (
                      <span className="draft-pill">Draft</span>
                    )}
                    </div>
                  </div>

                  <div className="assessment-status">
                    <span className="wf-list-meta">
                      {assessment.workflow_status_label ?? assessment.status}
                      <SlaBadge state={assessment.sla_state} />
                    </span>

                    {assessment.overall_score != null || assessment.risk_level ? (
                      <AssessmentScore score={assessment.overall_score} level={assessment.risk_level} />
                    ) : (
                      <span className="assessment-not-analyzed">Not analyzed</span>
                    )}
                  </div>
                </div>
              ))}
            </div>

            <nav className="pagination" aria-label="Assessment list pages">
              <button
                type="button"
                className="pagination-button"
                aria-label="Previous page"
                onClick={() => goToPage(safePage - 1)}
                disabled={safePage === 1}
              >
                ← Previous
              </button>

              <span className="pagination-info" aria-live="polite">
                Page {safePage} of {totalPages}
              </span>

              <button
                type="button"
                className="pagination-button"
                aria-label="Next page"
                onClick={() => goToPage(safePage + 1)}
                disabled={safePage === totalPages}
              >
                Next →
              </button>
            </nav>
          </>
        )}
      </section>
    </div>
  );
}

export default AssessmentsPage;