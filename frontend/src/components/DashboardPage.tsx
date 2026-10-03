import { useState } from "react";
import "./Dashboard.css";

import type { Assessment } from "../api/assessments";
import type { CurrentUser } from "../api/auth";
import RiskLevelBadge from "./RiskLevelBadge";
import NavIcon from "./NavIcons";
import { clickableProps } from "../utils/a11y";
import { riskLevelInfo } from "../utils/riskLevel";
import { DAY_MS, bucketOf, isActive, launchingWithinWeek, lifecycle, type Bucket } from "../utils/assessmentLifecycle";

/*
 * Operations dashboard: KPI cards by lifecycle bucket, the risk-level
 * mix of active assessments, and an SLA-ordered action table.
 */

/* Risk distribution groups, most to least severe. */
type RiskGroup = "critical" | "high" | "medium" | "low" | "unrated";

const RISK_GROUPS: { key: RiskGroup; label: string }[] = [
  { key: "critical", label: "Critical" },
  { key: "high", label: "High" },
  { key: "medium", label: "Medium" },
  { key: "low", label: "Low" },
  { key: "unrated", label: "Unrated" },
];

function riskGroupOf(assessment: Assessment): RiskGroup {
  const { key } = riskLevelInfo(assessment.risk_level);
  if (key === "critical" || key === "very_high") return "critical";
  if (key === "high") return "high";
  if (key === "medium") return "medium";
  if (key === "low" || key === "very_low") return "low";
  return "unrated";
}

/* "Assigned" = when it entered its current stage, else when created. */
function assignedAt(assessment: Assessment): Date {
  return new Date(assessment.status_entered_at ?? assessment.created_at);
}

function formatDate(date: Date): string {
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleDateString(undefined, { month: "short", day: "2-digit", year: "numeric" });
}

type SlaTone = "overdue" | "urgent" | "soon" | "ok" | "none";

function slaInfo(assessment: Assessment, now: number): { text: string; tone: SlaTone; dueIn: number } {
  const due = assessment.status_due_at ? new Date(assessment.status_due_at).getTime() : NaN;
  if (Number.isNaN(due)) {
    return { text: "No SLA", tone: "none", dueIn: Number.POSITIVE_INFINITY };
  }

  const diff = due - now;
  const abs = Math.abs(diff);
  const hours = Math.floor(abs / (60 * 60 * 1000));
  const minutes = Math.floor((abs % (60 * 60 * 1000)) / (60 * 1000));
  const days = Math.floor(abs / DAY_MS);

  const span =
    abs >= 2 * DAY_MS
      ? `${days} days`
      : abs >= DAY_MS && minutes === 0
        ? `${hours}h`
        : `${String(hours).padStart(2, "0")}h ${String(minutes).padStart(2, "0")}m`;

  if (diff < 0 || assessment.sla_state === "OVERDUE") {
    return { text: diff < 0 ? `Overdue by ${span}` : "Overdue", tone: "overdue", dueIn: diff };
  }

  const tone: SlaTone =
    diff < 12 * 60 * 60 * 1000
      ? "urgent"
      : diff < 2 * DAY_MS || assessment.sla_state === "AT_RISK"
        ? "soon"
        : "ok";
  return { text: `${span} remaining`, tone, dueIn: diff };
}

/* Change vs the previous 30 days, by creation date; null if no baseline. */
function trend(items: Assessment[], now: number): number | null {
  let current = 0;
  let previous = 0;
  for (const item of items) {
    const age = now - new Date(item.created_at).getTime();
    if (age < 30 * DAY_MS) current += 1;
    else if (age < 60 * DAY_MS) previous += 1;
  }
  if (previous === 0) return null;
  return Math.round(((current - previous) / previous) * 100);
}

export type ActionFilter = "all" | "overdue" | "mine" | "launching";
type RangeFilter = "7" | "30" | "90" | "all";

interface DashboardPageProps {
  user: CurrentUser;
  assessments: Assessment[];
  loading: boolean;
  error: string;
  search: string;
  canRunPipeline: boolean;
  analyzingId: number | null;
  analyzeProgress: string;
  actionFilter: ActionFilter;
  onActionFilterChange: (filter: ActionFilter) => void;
  onRetry: () => void;
  onOpenAssessment: (assessment: Assessment) => void;
  onCreateAssessment: () => void;
  onAnalyze: (assessmentId: number) => void;
}

const TABLE_LIMIT = 12;

export default function DashboardPage({
  user,
  assessments,
  loading,
  error,
  search,
  canRunPipeline,
  analyzingId,
  analyzeProgress,
  actionFilter,
  onActionFilterChange,
  onRetry,
  onOpenAssessment,
  onCreateAssessment,
  onAnalyze,
}: DashboardPageProps) {
  const [stageFilter, setStageFilter] = useState("all");
  const [rangeFilter, setRangeFilter] = useState<RangeFilter>("all");
  const [riskFilter, setRiskFilter] = useState<RiskGroup | null>(null);
  const [showAll, setShowAll] = useState(false);

  // SLA countdowns are measured from when the dashboard was opened.
  const [now] = useState(() => Date.now());

  const buckets: Record<Bucket, Assessment[]> = {
    draft: [],
    in_progress: [],
    pending_review: [],
    approved: [],
    inactive: [],
  };
  for (const assessment of assessments) buckets[bucketOf(assessment)].push(assessment);

  const provisional = assessments.filter((a) => a.analysis_is_provisional);
  const active = assessments.filter(isActive);

  const distribution = RISK_GROUPS.map((group) => {
    const count = active.filter((a) => riskGroupOf(a) === group.key).length;
    return {
      ...group,
      count,
      percent: active.length ? Math.round((count / active.length) * 100) : 0,
    };
  });

  const stageLabels = new Map<string, string>();
  for (const assessment of active) {
    stageLabels.set(lifecycle(assessment), assessment.workflow_status_label ?? lifecycle(assessment));
  }
  const stages = [...stageLabels.entries()].sort((a, b) => a[1].localeCompare(b[1]));

  const query = search.trim().toLowerCase();

  const rows = active
    .filter((assessment) => {
      if (actionFilter === "overdue" && slaInfo(assessment, now).tone !== "overdue") return false;
      if (actionFilter === "mine" && assessment.current_assignee_id !== user.id && assessment.owner_id !== user.id) {
        return false;
      }
      if (actionFilter === "launching" && !launchingWithinWeek(assessment)) return false;
      if (stageFilter !== "all" && lifecycle(assessment) !== stageFilter) return false;
      if (riskFilter && riskGroupOf(assessment) !== riskFilter) return false;
      if (rangeFilter !== "all" && now - assignedAt(assessment).getTime() > Number(rangeFilter) * DAY_MS) {
        return false;
      }
      if (query) {
        const haystack = [
          assessment.reference_id,
          `#${assessment.id}`,
          assessment.title,
          assessment.change_type,
          assessment.risk_level,
          assessment.workflow_status_label,
        ]
          .filter(Boolean)
          .join(" ")
          .toLowerCase();
        if (!haystack.includes(query)) return false;
      }
      return true;
    })
    .map((assessment) => ({ assessment, sla: slaInfo(assessment, now) }))
    .sort((a, b) => a.sla.dueIn - b.sla.dueIn || assignedAt(b.assessment).getTime() - assignedAt(a.assessment).getTime());

  const visibleRows = showAll ? rows : rows.slice(0, TABLE_LIMIT);

  const kpis: {
    label: string;
    items: Assessment[];
    accent?: boolean;
    note?: string;
  }[] = [
    { label: "Total Assessments", items: assessments },
    { label: "Draft Stage", items: buckets.draft },
    { label: "In Progress", items: buckets.in_progress },
    { label: "Pending Review", items: buckets.pending_review },
    { label: "Approved Final", items: buckets.approved },
    { label: "Provisional (AI)", items: provisional, accent: true, note: "Needs review" },
  ];

  const filtersActive =
    actionFilter !== "all" || stageFilter !== "all" || rangeFilter !== "all" || riskFilter !== null || query !== "";

  function resetFilters() {
    onActionFilterChange("all");
    setStageFilter("all");
    setRangeFilter("all");
    setRiskFilter(null);
  }

  return (
    <div className="dash">
      <section className="dash-kpis" aria-label="Assessment summary">
        {kpis.map((kpi) => {
          const change = kpi.accent ? null : trend(kpi.items, now);
          return (
            <div key={kpi.label} className={`dash-kpi${kpi.accent ? " dash-kpi-accent" : ""}`}>
              <span className="dash-kpi-label">{kpi.label}</span>
              <div className="dash-kpi-row">
                <strong>{loading ? "…" : kpi.items.length.toLocaleString()}</strong>
                {kpi.accent ? (
                  <span className="dash-kpi-note">{kpi.note}</span>
                ) : (
                  change !== null && (
                    <span
                      className={`dash-trend ${change >= 0 ? "up" : "down"}`}
                      title="New in the last 30 days vs the 30 days before"
                    >
                      <span aria-hidden="true">{change >= 0 ? "↑" : "↓"}</span>
                      <span className="sr-only">{change >= 0 ? "Up" : "Down"}</span> {Math.abs(change)}%
                    </span>
                  )
                )}
              </div>
            </div>
          );
        })}
      </section>

      <section className="dash-card dash-distribution" aria-labelledby="dash-dist-title">
        <div className="dash-dist-head">
          <h3 id="dash-dist-title">Risk Level Distribution Across Active Assessments</h3>
          <ul className="dash-legend">
            {distribution.map((group) => (
              <li key={group.key}>
                <span className={`dash-dot dash-${group.key}`} aria-hidden="true" />
                {group.label} ({group.percent}%)
              </li>
            ))}
          </ul>
        </div>

        <div className="dash-bar" role="group" aria-label="Filter actions by risk level">
          {active.length === 0 ? (
            <span className="dash-bar-empty" />
          ) : (
            distribution
              .filter((group) => group.count > 0)
              .map((group) => (
                <button
                  key={group.key}
                  type="button"
                  className={`dash-bar-seg dash-${group.key}${riskFilter === group.key ? " selected" : ""}`}
                  style={{ flexGrow: group.count }}
                  aria-pressed={riskFilter === group.key}
                  aria-label={`${group.label}: ${group.count} of ${active.length} active`}
                  title={`${group.label}: ${group.count} (${group.percent}%) — click to filter`}
                  onClick={() => setRiskFilter((current) => (current === group.key ? null : group.key))}
                />
              ))
          )}
        </div>
      </section>

      <section className="dash-card dash-actions" aria-label="Action queue">
        <div className="dash-toolbar">
          <div className="dash-tabs" role="group" aria-label="Action filter">
            {(
              [
                ["all", "All Actions"],
                ["overdue", "Overdue SLA"],
                ["mine", "Assigned to Me"],
                ["launching", "Launching ≤ 7 Days"],
              ] as const
            ).map(([value, label]) => (
              <button
                key={value}
                type="button"
                className={`dash-tab${actionFilter === value ? " active" : ""}`}
                aria-pressed={actionFilter === value}
                onClick={() => onActionFilterChange(value)}
              >
                {label}
              </button>
            ))}
          </div>

          <div className="dash-selects">
            {riskFilter && (
              <button type="button" className="dash-chip" onClick={() => setRiskFilter(null)}>
                Risk: {RISK_GROUPS.find((g) => g.key === riskFilter)?.label} <span aria-hidden="true">×</span>
                <span className="sr-only">(clear)</span>
              </button>
            )}
            <label className="dash-select">
              <span className="sr-only">Stage</span>
              <select value={stageFilter} onChange={(event) => setStageFilter(event.target.value)}>
                <option value="all">Stage: All Stages</option>
                {stages.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
              <NavIcon name="chevron-down" size={16} />
            </label>
            <label className="dash-select">
              <NavIcon name="calendar" size={16} />
              <span className="sr-only">Assigned within</span>
              <select value={rangeFilter} onChange={(event) => setRangeFilter(event.target.value as RangeFilter)}>
                <option value="7">Last 7 Days</option>
                <option value="30">Last 30 Days</option>
                <option value="90">Last 90 Days</option>
                <option value="all">All Time</option>
              </select>
            </label>
          </div>
        </div>

        {error ? (
          <div className="dash-empty" role="alert">
            <h3>{error}</h3>
            <button className="primary-button" type="button" onClick={onRetry}>
              Try Again
            </button>
          </div>
        ) : !loading && assessments.length === 0 ? (
          <div className="dash-empty">
            <h3>No assessments yet</h3>
            <p>
              Create an assessment to analyze customer, operational, financial, compliance, technology, and
              geographic risks.
            </p>
            <button className="primary-button" type="button" onClick={onCreateAssessment}>
              Create Assessment
            </button>
          </div>
        ) : (
          <div className="dash-table-wrap">
            <table className="dash-table">
              <thead>
                <tr>
                  <th scope="col">Assessment ID</th>
                  <th scope="col">Entity Name</th>
                  <th scope="col">Risk Level</th>
                  <th scope="col">Stage</th>
                  <th scope="col">SLA Status</th>
                  <th scope="col">Assigned Date</th>
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  <tr>
                    <td colSpan={6} className="dash-table-note">
                      Loading assessments…
                    </td>
                  </tr>
                ) : visibleRows.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="dash-table-note">
                      No assessments match these filters.
                      {filtersActive && (
                        <button type="button" className="dash-link" onClick={resetFilters}>
                          Clear filters
                        </button>
                      )}
                    </td>
                  </tr>
                ) : (
                  visibleRows.map(({ assessment, sla }) => {
                    const group = riskGroupOf(assessment);
                    return (
                      <tr
                        key={assessment.id}
                        {...clickableProps(() => onOpenAssessment(assessment), {
                          label: `Open assessment ${assessment.title}`,
                        })}
                      >
                        <td className="dash-id">{assessment.reference_id ?? `#${assessment.id}`}</td>
                        <td>
                          <span className="dash-entity">{assessment.title}</span>
                          <span className="dash-sub">{assessment.change_type}</span>
                        </td>
                        <td>
                          <div className="dash-risk-cell">
                            <RiskLevelBadge
                              level={assessment.risk_level}
                              emptyLabel="Unrated"
                              className={`dash-risk dash-risk-${group}`}
                            />
                            {assessment.overall_score != null && (
                              <span className="dash-score" aria-label={`Risk score ${assessment.overall_score}`}>
                                {assessment.overall_score}
                              </span>
                            )}
                            {group === "unrated" && canRunPipeline && (
                              <button
                                type="button"
                                className="dash-analyze"
                                onClick={(event) => {
                                  event.stopPropagation();
                                  onAnalyze(assessment.id);
                                }}
                                disabled={analyzingId === assessment.id}
                              >
                                {analyzingId === assessment.id ? `Analyzing… ${analyzeProgress}` : "Analyze"}
                              </button>
                            )}
                          </div>
                        </td>
                        <td className="dash-stage">
                          {assessment.workflow_status_label ?? lifecycle(assessment)}
                          {assessment.analysis_is_provisional && (
                            <span className="dash-provisional" title="AI result is provisional and needs human review">
                              <NavIcon name="sparkle" size={12} /> Provisional
                            </span>
                          )}
                        </td>
                        <td>
                          <span className={`dash-sla dash-sla-${sla.tone}`}>{sla.text}</span>
                        </td>
                        <td className="dash-date">{formatDate(assignedAt(assessment))}</td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        )}

        {!loading && rows.length > TABLE_LIMIT && (
          <div className="dash-footer">
            <span>
              Showing {visibleRows.length} of {rows.length}
            </span>
            <button type="button" className="dash-link" onClick={() => setShowAll((value) => !value)}>
              {showAll ? "Show fewer" : "Show all"}
            </button>
          </div>
        )}
      </section>
    </div>
  );
}
