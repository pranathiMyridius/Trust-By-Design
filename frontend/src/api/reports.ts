// Stage 17: Reporting and Monitoring. See backend/app/services/reporting.py
// for how each figure is computed and backend/app/api/reports.py for access.
import { authFetch } from "./http";
import { API_BASE_URL } from "./config";


export type ReportKind = "operational" | "risk" | "governance" | "ai-evaluation" | "high-risk-portfolio";

export interface ReportPeriod {
  date_from: string | null;
  date_to: string | null;
}

export interface CountRow {
  key: string;
  label: string;
  count: number;
}

// Report rows are heterogeneous per table; each table declares the
// columns it shows (see ReportsPage.tsx).
export type Row = Record<string, unknown>;

export interface ReportBase {
  period: ReportPeriod;
  generated_at: string;
  generated_by: string;
  population: number;
  summary: Record<string, number | null | boolean>;
}

export interface OperationalReport extends ReportBase {
  by_status: CountRow[];
  by_business_unit: CountRow[];
  by_legal_entity: CountRow[];
  by_risk_band: CountRow[];
  time_in_status: Row[];
  completions: Row[];
  overdue: Row[];
  open_conditions: Row[];
  deferred_and_rejected: Row[];
}

export interface RiskReport extends ReportBase {
  high_and_critical_risks: Row[];
  main_risk_drivers: Row[];
  top_indicators: CountRow[];
  by_geography: Row[];
  by_product: Row[];
  by_customer_segment: Row[];
  by_channel: Row[];
  by_typology: Row[];
  control_gaps_by_type: CountRow[];
  control_gaps: Row[];
  residual_above_tolerance: Row[];
}

export interface GovernanceReport extends ReportBase {
  overrides_by_kind: CountRow[];
  human_overrides: Row[];
  unresolved_findings: Row[];
  exceptions: Row[];
  approval_conditions: Row[];
  reassessment_due_dates: Row[];
  policy_changes: Row[];
}

export interface AiEvaluationReport extends ReportBase {
  not_measurable: Record<string, string>;
  metrics: Record<string, number | null>;
  usage_by_model: Row[];
  usage_by_purpose: Row[];
  usage_by_prompt_version?: Row[];
  per_assessment: Row[];
  method_notes: Record<string, string>;
}

export interface PortfolioItem {
  assessment_id: number;
  reference_id: string;
  title: string;
  product: string | null;
  business_unit: string | null;
  geographies: string[];
  customer_segments: string[];
  channels: string[];
  band: string;
  score: number | null;
  workflow_status: string;
  risks: { category: string; band: string; score: number | null; rationale: string }[];
  controls: { control_type: string; owner: string | null; design_adequacy: string; operating_effectiveness: string }[];
  control_gaps: { gap_type: string; description: string | null }[];
  open_actions: {
    kind: string;
    title: string;
    owner: string | null;
    priority: string;
    status: string;
    due_date: string | null;
    overdue: boolean;
  }[];
}

export interface PortfolioReport extends ReportBase {
  products: CountRow[];
  geographies: CountRow[];
  top_risks: CountRow[];
  assessments: PortfolioItem[];
}

export async function getReport<T extends ReportBase>(kind: ReportKind, period: ReportPeriod): Promise<T> {
  const params = new URLSearchParams();
  if (period.date_from) params.set("date_from", period.date_from);
  if (period.date_to) params.set("date_to", period.date_to);
  const query = params.toString();

  const response = await authFetch(`${API_BASE_URL}/api/reports/${kind}${query ? `?${query}` : ""}`, {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    let message = `Failed to load ${kind} report`;
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") message = body.detail;
    } catch {
      // keep default
    }
    throw new Error(message);
  }
  return response.json();
}

// Roles that may open the Reports page / the AI evaluation tab.
export const REPORT_ROLES = ["FCRM_ANALYST", "MANAGER", "COMMITTEE_MEMBER", "ADMIN", "AUDITOR", "EXECUTIVE"];
export const AI_REPORT_ROLES = ["FCRM_ANALYST", "MANAGER", "ADMIN"];
