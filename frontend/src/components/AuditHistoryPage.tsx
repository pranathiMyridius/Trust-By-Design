import { Fragment, useMemo, useState } from "react";
import type { AuditEvent, Assessment } from "../api/assessments";

interface AuditHistoryPageProps {
  auditEvents: AuditEvent[];
  assessments: Assessment[];
  onSelectAssessment: (assessment: Assessment) => void;
}

const PAGE_SIZE = 25;

function humanize(value: string): string {
  const text = value.replace(/_/g, " ").toLowerCase();
  return text.charAt(0).toUpperCase() + text.slice(1);
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
    case "RISK_CALCULATOR_UPDATED":
      return "audit-badge calculator";
    case "MANUAL_SCORE_OVERRIDE":
      return "audit-badge override";
    default:
      return "audit-badge";
  }
}

function formatDate(date: string) {
  return new Date(date).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
}

function formatTime(date: string) {
  return new Date(date).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" });
}

// `details` is free text, but some events store a JSON object.
function parseDetails(details: string): Record<string, unknown> | null {
  const trimmed = details.trim();
  if (!trimmed.startsWith("{")) return null;
  try {
    const parsed = JSON.parse(trimmed);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

function csvCell(value: string): string {
  return `"${value.replace(/"/g, '""')}"`;
}

function AuditHistoryPage({ auditEvents, assessments, onSelectAssessment }: AuditHistoryPageProps) {
  const [search, setSearch] = useState("");
  const [actionFilter, setActionFilter] = useState("");
  const [actorFilter, setActorFilter] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [page, setPage] = useState(1);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  const assessmentById = useMemo(() => new Map(assessments.map((a) => [a.id, a])), [assessments]);

  function titleFor(event: AuditEvent): string {
    if (event.assessment_id == null) {
      return event.action.startsWith("DELEGATION_") ? "Delegation" : "Risk Calculator";
    }
    return assessmentById.get(event.assessment_id)?.title || "Assessment";
  }

  const actions = useMemo(() => [...new Set(auditEvents.map((e) => e.action))].sort(), [auditEvents]);
  const actors = useMemo(() => [...new Set(auditEvents.map((e) => e.actor || "System"))].sort(), [auditEvents]);

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return auditEvents.filter((event) => {
      if (actionFilter && event.action !== actionFilter) return false;
      if (actorFilter && (event.actor || "System") !== actorFilter) return false;
      const day = event.created_at.slice(0, 10);
      if (dateFrom && day < dateFrom) return false;
      if (dateTo && day > dateTo) return false;
      if (needle) {
        const haystack = [
          titleFor(event),
          event.assessment_id != null ? `assessment #${event.assessment_id}` : "",
          event.action,
          event.details ?? "",
          event.actor ?? "",
        ]
          .join(" ")
          .toLowerCase();
        if (!haystack.includes(needle)) return false;
      }
      return true;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auditEvents, search, actionFilter, actorFilter, dateFrom, dateTo, assessmentById]);

  const hasFilters = Boolean(search || actionFilter || actorFilter || dateFrom || dateTo);
  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const visible = filtered.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);

  function clearFilters() {
    setSearch("");
    setActionFilter("");
    setActorFilter("");
    setDateFrom("");
    setDateTo("");
    setPage(1);
  }

  function toggle(id: number) {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function exportCsv() {
    const header = ["Date", "Time", "Assessment", "Reference", "Action", "From status", "To status", "Actor", "Details"];
    const rows = filtered.map((event) => [
      formatDate(event.created_at),
      formatTime(event.created_at),
      titleFor(event),
      event.assessment_id != null ? `#${event.assessment_id}` : "",
      formatAction(event.action),
      event.previous_status ?? "",
      event.new_status ?? "",
      event.actor || "System",
      event.details ?? "",
    ]);
    const csv = [header, ...rows].map((row) => row.map(csvCell).join(",")).join("\r\n");
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `audit-history-${new Date().toISOString().slice(0, 10)}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="audit-history-page">
      <div className="page-header">
        <div>
          <h2>Audit History</h2>
          <p>Track assessment creation, analysis, review decisions, and status changes.</p>
        </div>
        <button type="button" className="secondary-button" onClick={exportCsv} disabled={filtered.length === 0}>
          Export CSV
        </button>
      </div>

      <section className="content-card audit-history-card">
        <div className="card-header audit-history-header">
          <div>
            <h3>Assessment Activity</h3>
            <p>Complete audit trail of actions performed across risk assessments.</p>
          </div>

          <div className="audit-count" role="status" aria-live="polite">
            {hasFilters ? `${filtered.length} of ${auditEvents.length}` : auditEvents.length}{" "}
            {auditEvents.length === 1 ? "Event" : "Events"}
          </div>
        </div>

        {auditEvents.length > 0 && (
          <div className="audit-filters">
            <div className="audit-filter audit-filter-search">
              <label htmlFor="audit-search">Search</label>
              <input
                id="audit-search"
                type="search"
                placeholder="Assessment, actor or detail…"
                value={search}
                onChange={(event) => {
                  setSearch(event.target.value);
                  setPage(1);
                }}
              />
            </div>
            <div className="audit-filter">
              <label htmlFor="audit-action">Action</label>
              <select
                id="audit-action"
                value={actionFilter}
                onChange={(event) => {
                  setActionFilter(event.target.value);
                  setPage(1);
                }}
              >
                <option value="">All actions</option>
                {actions.map((action) => (
                  <option key={action} value={action}>
                    {humanize(action)}
                  </option>
                ))}
              </select>
            </div>
            <div className="audit-filter">
              <label htmlFor="audit-actor">Actor</label>
              <select
                id="audit-actor"
                value={actorFilter}
                onChange={(event) => {
                  setActorFilter(event.target.value);
                  setPage(1);
                }}
              >
                <option value="">Everyone</option>
                {actors.map((actor) => (
                  <option key={actor} value={actor}>
                    {actor}
                  </option>
                ))}
              </select>
            </div>
            <div className="audit-filter">
              <label htmlFor="audit-from">From</label>
              <input
                id="audit-from"
                type="date"
                value={dateFrom}
                onChange={(event) => {
                  setDateFrom(event.target.value);
                  setPage(1);
                }}
              />
            </div>
            <div className="audit-filter">
              <label htmlFor="audit-to">To</label>
              <input
                id="audit-to"
                type="date"
                value={dateTo}
                onChange={(event) => {
                  setDateTo(event.target.value);
                  setPage(1);
                }}
              />
            </div>
            {hasFilters && (
              <button type="button" className="secondary-button" onClick={clearFilters}>
                Clear filters
              </button>
            )}
          </div>
        )}

        {auditEvents.length === 0 ? (
          <div className="empty-state">
            <div className="empty-icon">✓</div>
            <h3>No audit history yet</h3>
            <p>Audit events will appear here when assessments are created, analyzed, reviewed, or updated.</p>
          </div>
        ) : filtered.length === 0 ? (
          <div className="empty-state">
            <div className="empty-icon">✓</div>
            <h3>No events match these filters</h3>
            <p>Try a different search, action or date range.</p>
            <button type="button" className="secondary-button" onClick={clearFilters}>
              Clear filters
            </button>
          </div>
        ) : (
          <>
            <div className="audit-table-container">
              <table className="audit-table">
                <thead>
                  <tr>
                    <th className="assessment-column">Assessment</th>
                    <th className="action-column">Action</th>
                    <th className="status-column">Status Change</th>
                    <th className="actor-column">Actor</th>
                    <th className="date-column">Date &amp; Time</th>
                    <th className="details-column">
                      <span className="sr-only">Details</span>
                    </th>
                  </tr>
                </thead>

                <tbody>
                  {visible.map((event) => {
                    const assessment = event.assessment_id != null ? assessmentById.get(event.assessment_id) : undefined;
                    const isStandalone = event.assessment_id == null;
                    const isDelegation = event.action.startsWith("DELEGATION_");
                    const isOpen = expanded.has(event.id);
                    const structured = event.details ? parseDetails(event.details) : null;

                    return (
                      <Fragment key={event.id}>
                        <tr className={`audit-row${isOpen ? " is-open" : ""}`}>
                          <td>
                            <div className="assessment-cell">
                              <div className="assessment-avatar" aria-hidden="true">
                                {isStandalone
                                  ? isDelegation
                                    ? "⇄"
                                    : "▣"
                                  : assessment?.title
                                  ? assessment.title.charAt(0).toUpperCase()
                                  : "A"}
                              </div>
                              <div className="assessment-info">
                                {assessment ? (
                                  <button
                                    type="button"
                                    className="audit-open-link"
                                    onClick={() => onSelectAssessment(assessment)}
                                    title="Open this assessment"
                                  >
                                    {assessment.title}
                                  </button>
                                ) : (
                                  <strong>{titleFor(event)}</strong>
                                )}
                                <span>
                                  {isStandalone
                                    ? "Not tied to an assessment"
                                    : `${assessment?.reference_id ?? "Assessment"} · #${event.assessment_id}`}
                                </span>
                              </div>
                            </div>
                          </td>

                          <td>
                            <span className={getActionClass(event.action)}>{formatAction(event.action)}</span>
                          </td>

                          <td>
                            {event.previous_status || event.new_status ? (
                              <div className="status-transition">
                                <span className="status-pill">{humanize(event.previous_status || "—")}</span>
                                <span className="transition-arrow" aria-hidden="true">
                                  →
                                </span>
                                <span className="sr-only"> changed to </span>
                                <span className="status-pill status-pill-new">{humanize(event.new_status || "—")}</span>
                              </div>
                            ) : (
                              <span className="no-status">—</span>
                            )}
                          </td>

                          <td>
                            <div className="actor-cell">
                              <span className="actor-avatar">{(event.actor || "System").charAt(0).toUpperCase()}</span>
                              <span>{event.actor || "System"}</span>
                            </div>
                          </td>

                          <td>
                            <div className="date-cell">
                              <strong>{formatDate(event.created_at)}</strong>
                              <span>{formatTime(event.created_at)}</span>
                            </div>
                          </td>

                          <td>
                            {event.details ? (
                              <button
                                type="button"
                                className="audit-expand"
                                aria-expanded={isOpen}
                                aria-controls={`audit-details-${event.id}`}
                                onClick={() => toggle(event.id)}
                              >
                                <span className="sr-only">{isOpen ? "Hide details" : "Show details"}</span>
                                <span aria-hidden="true">{isOpen ? "▾" : "▸"}</span>
                              </button>
                            ) : null}
                          </td>
                        </tr>
                        {isOpen && event.details && (
                          <tr className="audit-details-row" id={`audit-details-${event.id}`}>
                            <td colSpan={6}>
                              <div className="audit-details">
                                <h4>What happened</h4>
                                {structured ? (
                                  <dl>
                                    {Object.entries(structured).map(([key, value]) => (
                                      <div key={key}>
                                        <dt>{humanize(key)}</dt>
                                        <dd>{typeof value === "object" ? JSON.stringify(value) : String(value)}</dd>
                                      </div>
                                    ))}
                                  </dl>
                                ) : (
                                  <p>{event.details}</p>
                                )}
                              </div>
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>

            <nav className="pagination audit-pagination" aria-label="Audit history pages">
              <button
                type="button"
                className="pagination-button"
                onClick={() => setPage(Math.max(1, safePage - 1))}
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
                onClick={() => setPage(Math.min(totalPages, safePage + 1))}
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

export default AuditHistoryPage;
