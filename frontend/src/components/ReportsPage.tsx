import { useEffect, useState } from "react";

import type { CurrentUser } from "../api/auth";
import {
  AI_REPORT_ROLES,
  getReport,
  type AiEvaluationReport,
  type CountRow,
  type GovernanceReport,
  type OperationalReport,
  type PortfolioReport,
  type ReportBase,
  type ReportKind,
  type ReportPeriod,
  type RiskReport,
  type Row,
} from "../api/reports";
import { BandPill, BarList, DataTable, Panel, StatRow, type BarItem, type Column } from "./ReportWidgets";
import { formatValue, humanize, isoDate } from "./reportFormat";
import "./Reports.css";
import { friendlyError } from "../utils/errorMessages";
import { handleTablistKeyDown } from "../utils/a11y";

/**
 * Stage 17: Reporting and Monitoring. One page, a shared date range,
 * and a tab per report. Every chart has a table beside it (the
 * accessible / exportable view) and every table downloads as CSV.
 */

interface ReportsPageProps {
  user: CurrentUser;
  onOpenAssessment: (assessmentId: number) => void;
}

const TABS: { kind: ReportKind; label: string; ai?: boolean }[] = [
  { kind: "operational", label: "Operational" },
  { kind: "risk", label: "Risk" },
  { kind: "high-risk-portfolio", label: "High-risk portfolio" },
  { kind: "governance", label: "Governance" },
  { kind: "ai-evaluation", label: "AI evaluation", ai: true },
];

type Preset = "30d" | "90d" | "ytd" | "12m" | "all" | "custom";

function presetPeriod(preset: Preset): ReportPeriod {
  const today = new Date();
  const back = (days: number) => isoDate(new Date(today.getTime() - days * 86400000));
  switch (preset) {
    case "30d":
      return { date_from: back(29), date_to: isoDate(today) };
    case "90d":
      return { date_from: back(89), date_to: isoDate(today) };
    case "ytd":
      return { date_from: `${today.getFullYear()}-01-01`, date_to: isoDate(today) };
    case "12m":
      return { date_from: back(364), date_to: isoDate(today) };
    default:
      return { date_from: null, date_to: null };
  }
}

function counts(rows: CountRow[]): BarItem[] {
  return rows.map((row) => ({ key: row.key, label: row.label || row.key, value: row.count }));
}

function refColumn(onOpen: (id: number) => void): Column {
  return {
    key: "reference_id",
    label: "Assessment",
    render: (row) => (
      <button className="rp-link" onClick={() => onOpen(Number(row.assessment_id))}>
        <strong>{String(row.reference_id)}</strong>
        <span>{String(row.title ?? "")}</span>
      </button>
    ),
  };
}

export default function ReportsPage({ user, onOpenAssessment }: ReportsPageProps) {
  const tabs = TABS.filter((tab) => !tab.ai || AI_REPORT_ROLES.includes(user.role));
  const [kind, setKind] = useState<ReportKind>("operational");
  const [preset, setPreset] = useState<Preset>("all");
  const [period, setPeriod] = useState<ReportPeriod>(presetPeriod("all"));
  const [report, setReport] = useState<ReportBase | null>(null);
  const [reportKind, setReportKind] = useState<ReportKind | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshToken, setRefreshToken] = useState(0);

  const invalidRange = Boolean(period.date_from && period.date_to && period.date_from > period.date_to);

  useEffect(() => {
    if (invalidRange) {
      return;
    }
    let cancelled = false;
    getReport(kind, period).then(
      (data) => {
        if (!cancelled) {
          setReport(data);
          setReportKind(kind);
          setError(null);
          setLoading(false);
        }
      },
      (err) => {
        if (!cancelled) {
          setError(friendlyError(err, "This report couldn't be loaded. Try a different date range or refresh."));
          setLoading(false);
        }
      }
    );
    return () => {
      cancelled = true;
    };
  }, [kind, period, invalidRange, refreshToken]);

  function choosePreset(next: Preset) {
    setPreset(next);
    if (next !== "custom") {
      setLoading(true);
      setPeriod(presetPeriod(next));
    }
  }

  function chooseTab(next: ReportKind) {
    if (next !== kind) {
      setLoading(true);
      setKind(next);
    }
  }

  const current = report && reportKind === kind ? report : null;

  return (
    <div className="rp-page">
      <div className="page-header">
        <div>
          <h2>Reports</h2>
          <p>Operational, risk, governance and AI-performance reporting for a selected period.</p>
        </div>
      </div>

      <div className="rp-controls">
        <div className="rp-presets" role="group" aria-label="Date range">
          {(
            [
              ["30d", "Last 30 days"],
              ["90d", "Last 90 days"],
              ["ytd", "Year to date"],
              ["12m", "Last 12 months"],
              ["all", "All time"],
              ["custom", "Custom"],
            ] as [Preset, string][]
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              className={`rp-preset ${preset === value ? "active" : ""}`}
              aria-pressed={preset === value}
              onClick={() => choosePreset(value)}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="rp-dates">
          <label>
            From
            <input
              type="date"
              value={period.date_from ?? ""}
              onChange={(event) => {
                setPreset("custom");
                setLoading(true);
                setPeriod((p) => ({ ...p, date_from: event.target.value || null }));
              }}
            />
          </label>
          <label>
            To
            <input
              type="date"
              value={period.date_to ?? ""}
              onChange={(event) => {
                setPreset("custom");
                setLoading(true);
                setPeriod((p) => ({ ...p, date_to: event.target.value || null }));
              }}
            />
          </label>
          <button
            className="secondary-button"
            onClick={() => {
              setLoading(true);
              setRefreshToken((value) => value + 1);
            }}
            disabled={loading || invalidRange}
          >
            {loading ? "Loading…" : "Refresh"}
          </button>
        </div>
      </div>

      <div className="rp-tabs" role="tablist" aria-label="Reports" onKeyDown={handleTablistKeyDown}>
        {tabs.map((tab) => (
          <button
            key={tab.kind}
            type="button"
            role="tab"
            id={`rp-tab-${tab.kind}`}
            aria-selected={kind === tab.kind}
            aria-controls="rp-tabpanel"
            tabIndex={kind === tab.kind ? 0 : -1}
            className={`rp-tab ${kind === tab.kind ? "active" : ""}`}
            onClick={() => chooseTab(tab.kind)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {invalidRange && (
        <div className="wf-banner wf-banner-danger" role="alert">
          The start date must be on or before the end date. Change the "From" or "To" date.
        </div>
      )}
      {error && !invalidRange && <div className="wf-banner wf-banner-danger" role="alert">{error}</div>}

      <div id="rp-tabpanel" role="tabpanel" aria-labelledby={`rp-tab-${kind}`} aria-busy={loading}>

      {current && (
        <p className="rp-meta">
          {current.period.date_from || current.period.date_to
            ? `${current.period.date_from ? formatValue(current.period.date_from, "date") : "Beginning"} – ${
                current.period.date_to ? formatValue(current.period.date_to, "date") : "today"
              }`
            : "All time"}
          {" · "}
          {current.population} assessment{current.population === 1 ? "" : "s"} in scope · generated{" "}
          {formatValue(current.generated_at, "datetime")} for {current.generated_by}
          {loading && " · refreshing…"}
        </p>
      )}

      {!current && loading && !error && <p className="rp-empty" role="status">Loading report…</p>}

      {current && kind === "operational" && (
        <OperationalView report={current as OperationalReport} onOpen={onOpenAssessment} />
      )}
      {current && kind === "risk" && <RiskView report={current as RiskReport} onOpen={onOpenAssessment} />}
      {current && kind === "high-risk-portfolio" && (
        <PortfolioView report={current as PortfolioReport} onOpen={onOpenAssessment} />
      )}
      {current && kind === "governance" && (
        <GovernanceView report={current as GovernanceReport} onOpen={onOpenAssessment} />
      )}
      {current && kind === "ai-evaluation" && (
        <AiView report={current as AiEvaluationReport} onOpen={onOpenAssessment} />
      )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* R17.1 Operational                                                   */
/* ------------------------------------------------------------------ */

function OperationalView({ report, onOpen }: { report: OperationalReport; onOpen: (id: number) => void }) {
  const s = report.summary;
  const ref = refColumn(onOpen);
  return (
    <>
      <StatRow
        stats={[
          { label: "Assessments", value: s.total_assessments },
          { label: "Active", value: s.active },
          { label: "Avg completion", value: s.avg_completion_days, format: "days", hint: s.median_completion_days != null ? `median ${formatValue(s.median_completion_days, "days")}` : undefined },
          { label: "Overdue", value: s.overdue, tone: "danger" },
          { label: "Open conditions", value: s.open_conditions, tone: "warning" },
          { label: "Deferred / rejected", value: Number(s.deferred ?? 0) + Number(s.rejected ?? 0) },
        ]}
      />
      <div className="rp-grid">
        <Panel title="Assessments by status">
          <BarList items={counts(report.by_status)} />
        </Panel>
        <Panel title="Assessments by risk band" subtitle="Residual band, else inherent, else AI-assessed.">
          <BarList items={report.by_risk_band.map((row) => ({ key: row.key, label: humanize(row.key), value: row.count }))} />
        </Panel>
        <Panel title="Assessments by business unit" subtitle="Business unit on the request (legal entity where none was given).">
          <BarList items={counts(report.by_business_unit)} />
        </Panel>
        <Panel title="Assessments by legal entity">
          <BarList items={counts(report.by_legal_entity)} />
        </Panel>
        <Panel title="Average time in each status" subtitle="From workflow history, for statuses exited in scope.">
          <BarList
            format="days"
            items={report.time_in_status.map((row) => ({
              key: String(row.key),
              label: String(row.label),
              value: Number(row.avg_days),
              detail: `${row.transitions} exits`,
            }))}
            empty="No completed status changes yet."
          />
        </Panel>
        <Panel title="Overdue assessments" wide>
          <DataTable
            filename="overdue-assessments.csv"
            rows={report.overdue}
            columns={[
              ref,
              { key: "workflow_status", label: "Status" },
              { key: "owner", label: "Owner" },
              { key: "team", label: "Team" },
              { key: "status_due_at", label: "Status due", format: "datetime" },
              { key: "target_date", label: "Target", format: "date" },
              { key: "escalation_level", label: "Escalation", format: "int" },
            ]}
            empty="Nothing is overdue."
          />
        </Panel>
        <Panel title="Open conditions and actions" wide>
          <DataTable
            filename="open-conditions.csv"
            rows={report.open_conditions}
            columns={[
              ref,
              { key: "title", label: "Condition / action" },
              { key: "source", label: "Source", format: "label" },
              { key: "owner", label: "Owner" },
              { key: "priority", label: "Priority", format: "label" },
              { key: "due_date", label: "Due", format: "date" },
              { key: "overdue", label: "Overdue", format: "bool" },
            ]}
            empty="No open conditions."
          />
        </Panel>
        <Panel title="Deferred and rejected" wide>
          <DataTable
            filename="deferred-rejected.csv"
            rows={report.deferred_and_rejected}
            columns={[
              ref,
              { key: "outcome", label: "Outcome", format: "label" },
              { key: "decided_at", label: "Decided", format: "datetime" },
              { key: "rationale", label: "Rationale" },
              { key: "current_status", label: "Current status" },
            ]}
            empty="No deferred or rejected assessments."
          />
        </Panel>
        <Panel title="Completed assessments" wide>
          <DataTable
            filename="completions.csv"
            rows={report.completions}
            columns={[
              ref,
              { key: "outcome", label: "Outcome", format: "label" },
              { key: "completed_at", label: "Completed", format: "datetime" },
              { key: "days", label: "Submission → outcome", format: "days" },
            ]}
            empty="No assessment has reached an outcome yet."
          />
        </Panel>
      </div>
    </>
  );
}

/* ------------------------------------------------------------------ */
/* R17.2 Risk                                                          */
/* ------------------------------------------------------------------ */

const DIMENSION_COLUMNS: Column[] = [
  { key: "label", label: "Value" },
  { key: "assessments", label: "Assessments", format: "int" },
  { key: "high_or_critical", label: "High / critical", format: "int" },
  { key: "avg_score", label: "Avg score", format: "num" },
  { key: "max_band", label: "Highest band", render: (row) => <BandPill band={row.max_band} /> },
];

function DimensionPanel({ title, rows, filename }: { title: string; rows: Row[]; filename: string }) {
  return (
    <Panel title={title} subtitle="Bars: high / critical assessments.">
      <BarList
        items={rows
          .filter((row) => Number(row.high_or_critical) > 0)
          .slice(0, 8)
          .map((row) => ({
            key: String(row.label),
            label: String(row.label),
            value: Number(row.high_or_critical),
            detail: `of ${row.assessments}`,
          }))}
        empty="No high or critical assessments."
      />
      <DataTable filename={filename} rows={rows} columns={DIMENSION_COLUMNS} />
    </Panel>
  );
}

function RiskView({ report, onOpen }: { report: RiskReport; onOpen: (id: number) => void }) {
  const s = report.summary;
  const ref = refColumn(onOpen);
  return (
    <>
      <StatRow
        stats={[
          { label: "High / critical risks", value: s.high_or_critical_risks, tone: "danger" },
          { label: "High-risk assessments", value: s.high_risk_assessments, tone: "danger" },
          { label: "Open control gaps", value: s.open_control_gaps, tone: "warning" },
          { label: "Residual above tolerance", value: s.above_tolerance, tone: "danger", hint: `tolerance ${formatValue(s.residual_tolerance, "num")}` },
        ]}
      />
      <div className="rp-grid">
        <Panel title="Main risk drivers" subtitle="Risk categories by number of high / critical ratings.">
          <BarList
            items={report.main_risk_drivers.map((row) => ({
              key: String(row.key),
              label: String(row.label),
              value: Number(row.high_or_critical),
              detail: `${row.applicable} applicable`,
            }))}
            empty="No applicable risks in scope."
          />
        </Panel>
        <Panel title="Most frequent risk indicators">
          <BarList items={report.top_indicators.map((row) => ({ key: row.key, label: row.label, value: row.count }))} empty="No indicators recorded." />
        </Panel>
        <Panel title="High and critical risks" wide>
          <DataTable
            filename="high-critical-risks.csv"
            rows={report.high_and_critical_risks}
            columns={[
              ref,
              { key: "product", label: "Product" },
              { key: "geographies", label: "Geographies" },
              { key: "category", label: "Risk", format: "label" },
              { key: "band", label: "Band", render: (row) => <BandPill band={row.band} /> },
              { key: "score", label: "Score", format: "num" },
              { key: "rationale", label: "Rationale" },
            ]}
            empty="No high or critical risks."
          />
        </Panel>
        <DimensionPanel title="Risk by geography" rows={report.by_geography} filename="risk-by-geography.csv" />
        <DimensionPanel title="Risk by product" rows={report.by_product} filename="risk-by-product.csv" />
        <DimensionPanel title="Risk by customer segment" rows={report.by_customer_segment} filename="risk-by-segment.csv" />
        <DimensionPanel title="Risk by channel" rows={report.by_channel} filename="risk-by-channel.csv" />
        <Panel title="Risk by typology" subtitle="Applicable risks per category, by band." wide>
          <DataTable
            filename="risk-by-typology.csv"
            rows={report.by_typology}
            columns={[
              { key: "label", label: "Typology" },
              { key: "applicable", label: "Applicable", format: "int" },
              { key: "critical", label: "Critical", format: "int" },
              { key: "high", label: "High", format: "int" },
              { key: "medium", label: "Medium", format: "int" },
              { key: "low", label: "Low", format: "int" },
            ]}
          />
        </Panel>
        <Panel title="Control gaps by type">
          <BarList items={report.control_gaps_by_type.map((row) => ({ key: row.key, label: humanize(row.key), value: row.count }))} empty="No open control gaps." />
        </Panel>
        <Panel title="Residual risk above tolerance">
          <DataTable
            filename="residual-above-tolerance.csv"
            rows={report.residual_above_tolerance}
            columns={[
              ref,
              { key: "residual_score", label: "Residual", format: "num" },
              // A band set by a non-mitigable rule has no score; the band says why it is listed.
              { key: "residual_band", label: "Band" },
              { key: "excess", label: "Over by", format: "num" },
              { key: "workflow_status", label: "Status" },
            ]}
            empty="Nothing above tolerance."
          />
        </Panel>
        <Panel title="Open control gaps" wide>
          <DataTable
            filename="control-gaps.csv"
            rows={report.control_gaps}
            columns={[
              ref,
              { key: "gap_type", label: "Gap", format: "label" },
              { key: "description", label: "Description" },
              { key: "detected_at", label: "Detected", format: "datetime" },
            ]}
            empty="No open control gaps."
          />
        </Panel>
      </div>
    </>
  );
}

/* ------------------------------------------------------------------ */
/* High-risk portfolio                                                 */
/* ------------------------------------------------------------------ */

function PortfolioView({ report, onOpen }: { report: PortfolioReport; onOpen: (id: number) => void }) {
  const s = report.summary;
  return (
    <>
      <StatRow
        stats={[
          { label: "High-risk assessments", value: s.assessments },
          { label: "Critical", value: s.critical, tone: "danger" },
          { label: "High", value: s.high, tone: "warning" },
          { label: "Open actions", value: s.open_actions },
          { label: "Overdue actions", value: s.overdue_actions, tone: "danger" },
          { label: "Open control gaps", value: s.open_control_gaps, tone: "warning" },
        ]}
      />
      {report.assessments.length === 0 ? (
        <p className="rp-empty">No high or critical assessments in this period.</p>
      ) : (
        <>
          <div className="rp-grid rp-grid-3">
            <Panel title="Products">
              <BarList items={counts(report.products)} />
            </Panel>
            <Panel title="Geographies">
              <BarList items={counts(report.geographies)} />
            </Panel>
            <Panel title="High / critical risks">
              <BarList items={report.top_risks.map((row) => ({ key: row.key, label: row.label, value: row.count }))} empty="No high or critical risk factors rated." />
            </Panel>
          </div>
          <div className="rp-portfolio">
            {report.assessments.map((item) => (
              <article className="rp-card" key={item.assessment_id}>
                <header>
                  <button className="rp-link" onClick={() => onOpen(item.assessment_id)}>
                    <strong>{item.reference_id}</strong>
                    <span>{item.title}</span>
                  </button>
                  <div className="rp-card-meta">
                    <BandPill band={item.band} />
                    {item.score != null && <span>Score {formatValue(item.score, "num")}</span>}
                    <span>{item.workflow_status}</span>
                  </div>
                </header>
                <dl className="rp-facts">
                  <div>
                    <dt>Product</dt>
                    <dd>{item.product ?? "—"}</dd>
                  </div>
                  <div>
                    <dt>Business unit</dt>
                    <dd>{item.business_unit ?? "—"}</dd>
                  </div>
                  <div>
                    <dt>Geographies</dt>
                    <dd>{item.geographies.join(", ") || "—"}</dd>
                  </div>
                  <div>
                    <dt>Segments / channels</dt>
                    <dd>{[...item.customer_segments, ...item.channels].join(", ") || "—"}</dd>
                  </div>
                </dl>
                <div className="rp-card-cols">
                  <div>
                    <h4>Risks</h4>
                    {item.risks.length ? (
                      <ul>
                        {item.risks.map((risk) => (
                          <li key={risk.category}>
                            <BandPill band={risk.band} /> {humanize(risk.category)}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="rp-empty">None applicable.</p>
                    )}
                  </div>
                  <div>
                    <h4>Controls</h4>
                    {item.controls.length ? (
                      <ul>
                        {item.controls.map((control, index) => (
                          <li key={`${control.control_type}-${index}`}>
                            {humanize(control.control_type)}
                            <span className="rp-sub">
                              Design {humanize(control.design_adequacy)} · {humanize(control.operating_effectiveness)}
                            </span>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="rp-empty">No controls mapped.</p>
                    )}
                    {item.control_gaps.length > 0 && (
                      <p className="rp-sub rp-tone-warning">
                        {item.control_gaps.length} open gap{item.control_gaps.length === 1 ? "" : "s"}:{" "}
                        {item.control_gaps.map((gap) => humanize(gap.gap_type)).join(", ")}
                      </p>
                    )}
                  </div>
                  <div>
                    <h4>Open actions</h4>
                    {item.open_actions.length ? (
                      <ul>
                        {item.open_actions.map((action, index) => (
                          <li key={`${action.kind}-${index}`}>
                            {action.title}
                            <span className={`rp-sub ${action.overdue ? "rp-tone-danger" : ""}`}>
                              {action.owner ?? "Unassigned"} · due {formatValue(action.due_date, "date")}
                              {action.overdue ? " · overdue" : ""}
                            </span>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="rp-empty">No open actions.</p>
                    )}
                  </div>
                </div>
              </article>
            ))}
          </div>
        </>
      )}
    </>
  );
}

/* ------------------------------------------------------------------ */
/* R17.3 Governance                                                    */
/* ------------------------------------------------------------------ */

function GovernanceView({ report, onOpen }: { report: GovernanceReport; onOpen: (id: number) => void }) {
  const s = report.summary;
  const ref = refColumn(onOpen);
  return (
    <>
      <StatRow
        stats={[
          { label: "Human overrides", value: s.human_overrides },
          { label: "Unresolved findings", value: s.unresolved_findings, tone: "warning" },
          { label: "Exceptions", value: s.exceptions },
          { label: "Open conditions", value: s.open_approval_conditions, hint: `${s.overdue_approval_conditions ?? 0} overdue` },
          { label: "Reassessments due soon", value: s.reassessments_due_soon, tone: "warning", hint: `${s.reassessments_overdue ?? 0} overdue` },
          { label: "Policy changes", value: s.policy_changes },
        ]}
      />
      <div className="rp-grid">
        <Panel title="Human overrides" subtitle="Original and final values, with the reason recorded." wide>
          <BarList items={report.overrides_by_kind.map((row) => ({ key: row.key, label: humanize(row.key), value: row.count }))} empty="No overrides in this period." />
          <DataTable
            filename="human-overrides.csv"
            rows={report.human_overrides}
            columns={[
              ref,
              { key: "kind", label: "Type", format: "label" },
              { key: "field", label: "Field" },
              { key: "original_value", label: "Original" },
              { key: "final_value", label: "Final" },
              { key: "reason", label: "Reason" },
              { key: "by", label: "By" },
              { key: "at", label: "When", format: "datetime" },
            ]}
            empty="No overrides in this period."
          />
        </Panel>
        <Panel title="Unresolved findings" wide>
          <DataTable
            filename="unresolved-findings.csv"
            rows={report.unresolved_findings}
            columns={[
              ref,
              { key: "kind", label: "Type", format: "label" },
              { key: "category", label: "Category", format: "label" },
              { key: "severity", label: "Severity", format: "label" },
              { key: "description", label: "Description" },
              { key: "since", label: "Open since", format: "datetime" },
            ]}
            empty="No unresolved findings."
          />
        </Panel>
        <Panel title="Exceptions" wide>
          <DataTable
            filename="exceptions.csv"
            rows={report.exceptions}
            columns={[
              ref,
              { key: "kind", label: "Type", format: "label" },
              { key: "description", label: "Exception" },
              { key: "reason", label: "Justification" },
              { key: "by", label: "By" },
              { key: "at", label: "When", format: "datetime" },
            ]}
            empty="No exceptions in this period."
          />
        </Panel>
        <Panel title="Approval conditions" wide>
          <DataTable
            filename="approval-conditions.csv"
            rows={report.approval_conditions}
            columns={[
              ref,
              { key: "description", label: "Condition" },
              { key: "owner", label: "Owner" },
              { key: "priority", label: "Priority", format: "label" },
              { key: "status", label: "Status", format: "label" },
              { key: "due_date", label: "Due", format: "date" },
              { key: "overdue", label: "Overdue", format: "bool" },
            ]}
            empty="No approval conditions."
          />
        </Panel>
        <Panel title="Reassessment due dates" subtitle="From the committee decision: 6 months (critical) to 36 months (low)." wide>
          <DataTable
            filename="reassessment-due-dates.csv"
            rows={report.reassessment_due_dates}
            columns={[
              ref,
              { key: "band", label: "Band", render: (row) => <BandPill band={row.band} /> },
              { key: "decided_at", label: "Decided", format: "datetime" },
              { key: "reassessment_due", label: "Reassess by", format: "date" },
              { key: "days_until_due", label: "Days left", format: "int" },
              { key: "state", label: "State", format: "label" },
            ]}
            empty="No approved assessments to reassess."
          />
        </Panel>
        <Panel title="Policy and methodology changes" wide>
          <DataTable
            filename="policy-changes.csv"
            rows={report.policy_changes}
            columns={[
              { key: "kind", label: "Type", format: "label" },
              { key: "details", label: "Change" },
              { key: "by", label: "By" },
              { key: "at", label: "When", format: "datetime" },
            ]}
            empty="No policy or methodology changes in this period."
          />
        </Panel>
      </div>
    </>
  );
}

/* ------------------------------------------------------------------ */
/* R17.4 AI evaluation                                                 */
/* ------------------------------------------------------------------ */

const AI_METRICS: { key: string; label: string; format: "pct" | "num" | "ms"; detailKey?: string; detailLabel?: string; note?: string }[] = [
  { key: "ai_human_agreement", label: "AI-human agreement", format: "pct", detailKey: "categories_compared", detailLabel: "categories compared" },
  { key: "risk_band_agreement", label: "Risk-band agreement", format: "pct", detailKey: "risk_band_comparable", detailLabel: "assessments compared" },
  { key: "mean_abs_score_difference", label: "Mean score difference", format: "num", note: "risk_band_agreement" },
  { key: "human_override_rate", label: "Human override rate", format: "pct", detailKey: "human_overrides_total", detailLabel: "overrides recorded" },
  { key: "missing_risk_rate", label: "Missing-risk rate", format: "pct", detailKey: "human_added_risks", detailLabel: "risks added by humans" },
  { key: "evidence_groundedness", label: "Evidence-groundedness", format: "pct" },
  { key: "control_mapping_accuracy", label: "Control-mapping accuracy", format: "pct", detailKey: "controls_ai_extracted", detailLabel: "controls extracted" },
  { key: "unsupported_content_rate", label: "Unsupported-content rate", format: "pct", detailKey: "unsupported_findings", detailLabel: "unsupported-conclusion findings" },
  { key: "avg_processing_ms", label: "Avg processing time", format: "ms" },
  { key: "p95_processing_ms", label: "p95 processing time", format: "ms" },
];

function AiView({ report, onOpen }: { report: AiEvaluationReport; onOpen: (id: number) => void }) {
  const m = report.metrics;
  const s = report.summary;
  const ref = refColumn(onOpen);
  const reasonFor = (key: string) =>
    report.not_measurable[key] ?? (key === "mean_abs_score_difference" ? report.not_measurable.score_difference : undefined);

  const usageColumns: Column[] = [
    { key: "key", label: "" },
    { key: "calls", label: "Calls", format: "int" },
    { key: "failures", label: "Failures", format: "int" },
    { key: "total_tokens", label: "Tokens", format: "int" },
    { key: "cost_usd", label: "Cost", format: "usd", render: (row) => `${formatValue(row.cost_usd, "usd")}${row.cost_complete ? "" : " *"}` },
    { key: "avg_duration_ms", label: "Avg latency", format: "ms" },
    { key: "p95_duration_ms", label: "p95 latency", format: "ms" },
  ];

  return (
    <>
      <StatRow
        stats={[
          { label: "AI-assessed", value: s.evaluated_assessments },
          { label: "AI runs in period", value: s.ai_runs_in_period },
          { label: "AI calls", value: s.ai_calls },
          { label: "Failed calls", value: s.ai_call_failures, tone: "danger" },
          { label: "Tokens", value: s.total_tokens },
          { label: "Cost", value: s.total_cost_usd, format: "usd", hint: s.cost_complete ? undefined : "some calls had no cost data" },
        ]}
      />
      <div className="rp-metrics">
        {AI_METRICS.map((metric) => {
          const value = m[metric.key];
          const reason = reasonFor(metric.note ?? metric.key);
          return (
            <div className="rp-metric" key={metric.key} title={report.method_notes[metric.key] ?? undefined}>
              <span>{metric.label}</span>
              <strong>{value == null ? "—" : formatValue(value, metric.format)}</strong>
              {value == null ? (
                <small className="rp-muted">{reason ?? "Not enough human-reviewed data in this period yet."}</small>
              ) : metric.detailKey && m[metric.detailKey] != null ? (
                <small>
                  {formatValue(m[metric.detailKey], "int")} {metric.detailLabel}
                </small>
              ) : null}
            </div>
          );
        })}
      </div>
      <div className="rp-grid">
        <Panel title="Model usage and cost" subtitle="* cost incomplete — provider reported none and no AI_MODEL_PRICING entry." wide>
          <DataTable filename="ai-usage-by-model.csv" rows={report.usage_by_model} columns={[{ ...usageColumns[0], label: "Model" }, ...usageColumns.slice(1)]} empty="No AI calls in this period." />
        </Panel>
        <Panel title="Usage by purpose" wide>
          <DataTable filename="ai-usage-by-purpose.csv" rows={report.usage_by_purpose} columns={[{ ...usageColumns[0], label: "Purpose", format: "label" }, ...usageColumns.slice(1)]} empty="No AI calls in this period." />
        </Panel>
        <Panel title="Usage by prompt version" subtitle="Purpose · prompt template fingerprint. A new fingerprint means the prompt was changed." wide>
          <DataTable filename="ai-usage-by-prompt-version.csv" rows={report.usage_by_prompt_version ?? []} columns={[{ ...usageColumns[0], label: "Prompt version" }, ...usageColumns.slice(1)]} empty="No AI calls in this period." />
        </Panel>
        <Panel title="Per-assessment evaluation" wide>
          <DataTable
            filename="ai-evaluation-per-assessment.csv"
            rows={report.per_assessment}
            columns={[
              ref,
              { key: "model", label: "Model" },
              { key: "ai_available", label: "AI ran", format: "bool" },
              { key: "categories_agreeing", label: "Categories agreeing", render: (row) => (row.categories_compared ? `${row.categories_agreeing} / ${row.categories_compared}` : "Not reviewed") },
              { key: "human_band", label: "Human band", format: "label" },
              { key: "ai_band", label: "AI band", format: "label" },
              { key: "human_added", label: "Added", format: "int" },
              { key: "ai_excluded", label: "Excluded", format: "int" },
              { key: "groundedness", label: "Grounded", format: "pct" },
              { key: "overrides", label: "Overrides", format: "int" },
              { key: "processing_ms", label: "Processing", format: "ms" },
            ]}
            empty="No AI-assessed assessments in this period."
          />
        </Panel>
        <Panel title="How these are measured" wide>
          <dl className="rp-notes">
            {Object.entries(report.method_notes).map(([key, note]) => (
              <div key={key}>
                <dt>{humanize(key)}</dt>
                <dd>{note}</dd>
              </div>
            ))}
          </dl>
        </Panel>
      </div>
    </>
  );
}
