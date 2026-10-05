import { API_BASE_URL } from "./config";
import { authFetch } from "./http";
import type { ChallengeSignoff, ValueComparison } from "./governanceRecords";
import type { ProcessingJob } from "./processing";

// R1.1: additional fields captured on an assessment request, beyond the
// core title/change_type/description/evidence. All optional so a request
// can be saved as an incomplete draft (R1.3).
export interface AssessmentRequestFields {
  product_or_service_name?: string | null;
  business_owner?: string | null;
  legal_entity?: string | null;
  // Stage 19: optional business unit within the legal entity.
  business_unit?: string | null;
  customer_segment?: string | null;
  countries_jurisdictions?: string | null;
  delivery_channels?: string | null;
  expected_transaction_volume?: string | null;
  expected_transaction_value?: string | null;
  transaction_types?: string | null;
  third_party_vendor_usage?: string | null;
  technology_process_changes?: string | null;
  expected_launch_date?: string | null;
  // Flagged as a potential shell entity; null = not answered.
  shell_company_indicator?: boolean | null;
}

// R1.2: the change/request types the workbench supports.
export const CHANGE_TYPES: { value: string; label: string }[] = [
  { value: "NEW_PRODUCT", label: "New Product" },
  { value: "NEW_SERVICE", label: "New Service" },
  { value: "NEW_CUSTOMER_SEGMENT", label: "New Customer Segment" },
  { value: "NEW_GEOGRAPHY", label: "New Country / Geography" },
  { value: "PROCESS_CHANGE", label: "Process Change" },
  { value: "TECHNOLOGY_CHANGE", label: "Technology Change" },
  { value: "THIRD_PARTY_INTRODUCTION", label: "Third-Party Introduction" },
  {
    value: "TRANSACTION_LIMIT_OR_CHANNEL_CHANGE",
    label: "Transaction-Limit or Channel Change",
  },
  { value: "PERIODIC_REASSESSMENT", label: "Periodic Reassessment" },
];

// Older change-type values already stored on existing assessments, kept
// selectable so those records still render correctly in the dropdown.
export const LEGACY_CHANGE_TYPES: { value: string; label: string }[] = [
  { value: "MATERIAL_CHANGE", label: "Material Change (legacy)" },
  { value: "THIRD_PARTY", label: "Third Party / Vendor (legacy)" },
];

export interface Assessment extends AssessmentRequestFields {
  id: number;
  title: string;
  change_type: string;
  description: string | null;
  evidence: string | null;
  // R1.3: true while this request is still a draft (mandatory-field
  // validation was skipped). R1.5: submitted_by/submitted_at are set
  // once, at the moment is_draft first flips to false.
  is_draft: boolean;
  submitted_by?: string | null;
  submitted_at?: string | null;
  status: string;
  overall_score: number | null;
  risk_level: string | null;
  // Frozen snapshot taken on arrival at INHERENT_RISK_ASSESSMENT.
  inherent_score?: number | null;
  inherent_risk_level?: string | null;
  // Frozen snapshot taken on arrival at RESIDUAL_RISK.
  residual_score?: number | null;
  residual_risk_level?: string | null;
  // Intake automation: case reference id, triage, routing.
  reference_id?: string | null;
  priority?: string | null;
  priority_score?: number | null;
  assigned_team?: string | null;
  assigned_queue?: string | null;
  // Raw JSON text of the Manual Scoring Calculator's last-saved draft for
  // this assessment (parsed by the calculator itself). Null until a
  // draft has been saved.
  manual_score_draft?: string | null;
  // AW: hierarchical approval workflow.
  owner_id?: number | null;
  manager_id?: number | null;
  manager_decision?: string | null;
  manager_decided_by_id?: number | null;
  // AW.7: set when a delegate decided for this manager.
  manager_decided_on_behalf_of_id?: number | null;
  manager_delegation_id?: number | null;
  manager_decided_at?: string | null;
  manager_comment?: string | null;
  committee_decision?: string | null;
  committee_decided_by_id?: number | null;
  // AW.7: set when a delegate signed off for this committee member.
  committee_decided_on_behalf_of_id?: number | null;
  committee_delegation_id?: number | null;
  committee_decided_at?: string | null;
  committee_rationale?: string | null;
  committee_conditions?: string | null;
  // Stage 10 (R10.5): "Request more information" flow.
  information_request_target?: string | null;
  information_request_note?: string | null;
  information_requested_by?: string | null;
  information_requested_at?: string | null;
  information_response?: string | null;
  information_responded_at?: string | null;
  pre_information_request_status?: string | null;
  // Stage 14: lifecycle status, SLA and escalation (see api/workflow.ts).
  workflow_status?: string | null;
  workflow_status_label?: string | null;
  sla_state?: "NONE" | "ON_TRACK" | "AT_RISK" | "OVERDUE" | null;
  status_entered_at?: string | null;
  status_due_at?: string | null;
  target_date?: string | null;
  current_assignee_id?: number | null;
  escalation_level?: number | null;
  escalated_at?: string | null;
  escalated_to_id?: number | null;
  escalation_note?: string | null;
  closed_at?: string | null;
  // Degraded-mode contract (backend app/risk_engine/degraded.py): how the
  // latest risk identification was produced. Read these rather than
  // inferring degradation from rationale text.
  assessment_mode?: "ai_assisted" | "rules_only" | "unavailable" | null;
  ai_status?: string | null;
  score_source?: string | null;
  analysis_is_provisional?: boolean;
  requires_human_review?: boolean;
  degraded_reason?: string | null;
  degraded_acknowledged_by?: string | null;
  degraded_acknowledged_at?: string | null;
  // Stage 18: Reassessment and Change Management.
  parent_assessment_id?: number | null;
  next_review_date?: string | null;
  created_at: string;
  updated_at: string;
}

// R1.4: shape of the 422 error detail returned by POST /api/assessments
// and PATCH /api/assessments/{id} when a submission (is_draft=false) is
// missing mandatory fields.
export interface MissingFieldsErrorDetail {
  message: string;
  missing_fields: string[];
}

export function parseMissingFieldsDetail(
  detail: unknown
): MissingFieldsErrorDetail | null {
  if (
    detail &&
    typeof detail === "object" &&
    Array.isArray((detail as { missing_fields?: unknown }).missing_fields)
  ) {
    return detail as MissingFieldsErrorDetail;
  }

  return null;
}


/**
 * Turns a FastAPI `detail` into readable text. Handles the structured
 * shapes the backend uses: {message, missing: [...]} (approval refused for
 * an incomplete decision record), {message, issues: [...]} (mandatory
 * workflow issues), a list of strings (rule/grid validation) and pydantic's
 * list of {loc, msg} validation errors.
 */
export function formatErrorDetail(detail: unknown): string {
  if (typeof detail === "string") {
    return detail;
  }

  if (Array.isArray(detail)) {
    return detail
      .map((item) =>
        typeof item === "string"
          ? item
          : item && typeof item === "object" && "msg" in item
          ? String((item as { msg: unknown }).msg).replace(/^Value error, /, "")
          : JSON.stringify(item)
      )
      .join("; ");
  }

  if (detail && typeof detail === "object") {
    const { message, missing, issues } = detail as {
      message?: unknown;
      missing?: unknown;
      issues?: unknown;
    };
    const list = Array.isArray(missing) ? missing : Array.isArray(issues) ? issues : [];
    const head = typeof message === "string" ? message : "";
    if (head || list.length) {
      return [head, ...list.map((item, index) => `(${index + 1}) ${String(item)}`)]
        .filter(Boolean)
        .join(" ");
    }
  }

  return JSON.stringify(detail);
}

async function readErrorDetail(response: Response): Promise<string> {
  try {
    const text = await response.text();

    if (!text) {
      return "";
    }

    try {
      const parsed = JSON.parse(text);
      return parsed?.detail ? formatErrorDetail(parsed.detail) : text;
    } catch {
      return text;
    }
  } catch {
    return "";
  }
}

// Stage 19 (Scalability): optional server-side filters and paging. With
// no params this returns the full visible list, as before.
export interface AssessmentListParams {
  legal_entity?: string;
  business_unit?: string;
  status?: string;
  search?: string;
  limit?: number;
  offset?: number;
}

function assessmentListQuery(params: AssessmentListParams): string {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      query.set(key, String(value));
    }
  });
  const text = query.toString();
  return text ? `?${text}` : "";
}

export async function getAssessments(
  params: AssessmentListParams = {}
): Promise<Assessment[]> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments${assessmentListQuery(params)}`
  );

  if (!response.ok) {
    throw new Error(
      (await readErrorDetail(response)) || "Failed to fetch assessments"
    );
  }

  return response.json();
}

// Stage 19: one page of assessments plus the total match count (sent by
// the backend in X-Total-Count when a limit is given).
export async function getAssessmentsPage(
  params: AssessmentListParams & { limit: number }
): Promise<{ items: Assessment[]; total: number }> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments${assessmentListQuery(params)}`
  );

  if (!response.ok) {
    throw new Error(
      (await readErrorDetail(response)) || "Failed to fetch assessments"
    );
  }

  const items: Assessment[] = await response.json();
  const total = Number(response.headers.get("X-Total-Count") ?? items.length);
  return { items, total: Number.isFinite(total) ? total : items.length };
}

export interface OrgUnitCount {
  name: string;
  assessment_count: number;
}

export interface OrgUnits {
  legal_entities: OrgUnitCount[];
  business_units: OrgUnitCount[];
}

// Stage 19: legal entities / business units across visible assessments.
export async function getOrgUnits(): Promise<OrgUnits> {
  const response = await authFetch(`${API_BASE_URL}/api/assessments/org-units`);

  if (!response.ok) {
    throw new Error(
      (await readErrorDetail(response)) || "Failed to load legal entities and business units"
    );
  }

  return response.json();
}

// Identity search / de-duplication: existing assessments that plausibly
// describe the same request (same product name + legal entity/owner).
export interface DuplicateMatch {
  id: number;
  reference_id?: string | null;
  title: string;
  change_type: string;
  status: string;
  is_draft: boolean;
  business_owner?: string | null;
  legal_entity?: string | null;
  created_at: string;
}

export async function checkDuplicateAssessments(params: {
  product_or_service_name?: string;
  legal_entity?: string;
  business_owner?: string;
  exclude_id?: number;
}): Promise<DuplicateMatch[]> {
  const query = new URLSearchParams();

  if (params.product_or_service_name) {
    query.set("product_or_service_name", params.product_or_service_name);
  }
  if (params.legal_entity) {
    query.set("legal_entity", params.legal_entity);
  }
  if (params.business_owner) {
    query.set("business_owner", params.business_owner);
  }
  if (params.exclude_id != null) {
    query.set("exclude_id", String(params.exclude_id));
  }

  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/check-duplicates?${query.toString()}`
  );

  if (!response.ok) {
    throw new Error("Failed to check for duplicate assessments");
  }

  return response.json();
}

// Fetches a single assessment fresh from the backend. Used when opening
// an assessment from a list that may have been loaded a while ago (e.g.
// the dashboard only refetches on a full page load), so things saved
// since then — like a Manual Scoring Calculator draft, or a status/score
// change — aren't clobbered by stale in-memory data when the workflow
// page mounts.
export async function getAssessment(
  assessmentId: number
): Promise<Assessment> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}`
  );

  if (!response.ok) {
    throw new Error("Failed to fetch assessment");
  }

  return response.json();
}

export async function analyzeAssessment(
  assessmentId: number
): Promise<Assessment> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/analyze`,
    {
      method: "POST",
      headers: {
        Accept: "application/json",
      },
    }
  );

  if (!response.ok) {
    throw new Error("Failed to analyze assessment");
  }

  return response.json();
}
/** A reviewer accepts a provisional rules-only result on the record. */
export async function acknowledgeDegradedResult(
  assessmentId: number
): Promise<Assessment> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/acknowledge-degraded`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    }
  );

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(
      typeof body.detail === "string"
        ? body.detail
        : "The provisional result couldn't be acknowledged."
    );
  }

  return response.json();
}

export interface RiskResult {
  id: number;
  assessment_id: number;
  dimension: string;
  score: number;
  severity: string;
  reason: string;
}

export async function getRiskResults(
  assessmentId: number
): Promise<RiskResult[]> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/risk-results`
  );

  if (!response.ok) {
    throw new Error("Failed to fetch risk results");
  }

  return response.json();
}

// Stage 4 (R4.1-R4.5): the 10 canonical risk categories. Kept in sync
// with backend/app/schemas/risk_factor.py.
export const RISK_CATEGORIES: { value: string; label: string }[] = [
  { value: "PRODUCT_SERVICE_RISK", label: "Product or Service Risk" },
  { value: "CUSTOMER_SEGMENT_RISK", label: "Customer or Segment Risk" },
  { value: "GEOGRAPHIC_RISK", label: "Geographic Risk" },
  { value: "DELIVERY_CHANNEL_RISK", label: "Delivery-Channel Risk" },
  { value: "TRANSACTION_ACTIVITY_RISK", label: "Transaction or Activity Risk" },
  {
    value: "TECHNOLOGY_DEVELOPMENT_RISK",
    label: "Technology or New-Development Risk",
  },
  { value: "THIRD_PARTY_VENDOR_RISK", label: "Third-Party or Vendor Risk" },
  {
    value: "OWNERSHIP_ENTITY_COMPLEXITY_RISK",
    label: "Ownership or Entity-Complexity Risk",
  },
  {
    value: "FINANCIAL_CRIME_TYPOLOGY_RISK",
    label: "Financial-Crime Typology Risk",
  },
  { value: "CONTROL_ENVIRONMENT_RISK", label: "Control-Environment Risk" },
];

// R4.2: the 12 detailed risk indicators.
export const RISK_INDICATORS: { value: string; label: string }[] = [
  { value: "CROSS_BORDER_CAPABILITY", label: "Cross-border capability" },
  { value: "CASH_ACCESS", label: "Cash access" },
  { value: "ANONYMITY", label: "Anonymity" },
  { value: "MULTIPLE_CURRENCIES", label: "Multiple currencies" },
  { value: "UNKNOWN_PARTY_PAYMENTS", label: "Unknown-party payments" },
  { value: "REMOTE_ONBOARDING", label: "Remote onboarding" },
  { value: "TRANSACTION_VELOCITY", label: "Transaction velocity" },
  {
    value: "TRANSACTION_VALUE_VOLUME",
    label: "Transaction value and volume",
  },
  {
    value: "COMPLEX_OWNERSHIP_STRUCTURES",
    label: "Complex ownership structures",
  },
  { value: "SANCTIONS_EXPOSURE", label: "Sanctions exposure" },
  { value: "THIRD_PARTY_DEPENDENCIES", label: "Third-party dependencies" },
  {
    value: "DATA_MONITORING_LIMITATIONS",
    label: "Data and monitoring limitations",
  },
];

export interface RiskFactor {
  id: number;
  assessment_id: number;
  category: string;
  applicable: boolean;
  score: number;
  severity: string;
  // Stage 6 (R6.2/R6.3): the analyst's own likelihood/impact rating --
  // the score above is computed deterministically from these, never by
  // the AI.
  likelihood: number | null;
  impact: number | null;
  rated_by: string | null;
  rated_at: string | null;
  // The AI's suggested rating. It pre-fills the rating form and is kept
  // separate from the analyst's values above, so the record always shows
  // both what the model proposed and what the human decided. A
  // suggestion alone never scores the factor or clears "provisional".
  ai_suggested_likelihood: number | null;
  ai_suggested_impact: number | null;
  ai_suggestion_rationale: string | null;
  ai_suggested_at: string | null;
  rating_source:
    | "ANALYST_CONFIRMED"
    | "ANALYST_OVERRIDE"
    | "ANALYST_RATED"
    | null;
  indicators: string[];
  // Deterministic evidence verification (backend app/risk_engine/evidence.py).
  // null for factors that predate it and for manual factors.
  evidence_status: EvidenceStatus | null;
  evidence: EvidenceRecord[];
  rejected_indicators: { indicator: string; reason: string }[];
  missing_information: string[];
  // P4: fixed Stage 4 rules that required this category.
  rule_triggers?: Stage4RuleTrigger[];
  rationale: string;
  misuse_scenario: string | null;
  source: "AI" | "MANUAL" | "RULES";
  added_by: string | null;
  excluded: boolean;
  exclusion_reason: string | null;
  excluded_by: string | null;
  excluded_at: string | null;
  version: number;
  is_current: boolean;
  created_at: string;
}

export interface Stage4RuleTrigger {
  rule_id: string;
  description: string;
  ruleset_version: string;
  ruleset_status: string;
  effect: "FORCED_APPLICABLE" | "ADDED" | "CONFIRMED";
  signals: { group: string; field: string; keyword: string | null; context: string }[];
  considerations: { indicator: string | null; text: string }[];
}

export type ProvenanceConfidence = "HIGH" | "MEDIUM" | "LOW" | "USER_PROVIDED";

export interface FieldProvenance {
  field: string;
  origin: "AI_EXTRACTION" | "RULE_EXTRACTION" | "INTAKE_FORM" | "USER_CORRECTION";
  extraction_method: string | null;
  extracted_at: string | null;
  value: unknown;
  confidence: ProvenanceConfidence;
  verification: string | null;
  confidence_basis: string;
  document_id: number | null;
  document_version: number | null;
  filename: string | null;
  page: number | null;
  sheet: string | null;
  quote: string | null;
  corrected_by?: string;
  corrected_at?: string;
  replaces?: FieldProvenance | null;
  confirmed_by_user?: boolean;
  confirmed_by?: string | null;
  confirmed_at?: string | null;
}

export type EvidenceStatus =
  | "EVIDENCE_FOUND"
  | "INSUFFICIENT_EVIDENCE"
  | "CONFLICTING_EVIDENCE"
  | "NOT_VERIFIED"
  | "NOT_APPLICABLE";

export interface EvidenceRecord {
  source_id: string;
  source_type: "ASSESSMENT_FIELD" | "DOCUMENT" | null;
  source_label: string | null;
  document_id: number | null;
  document_version: number | null;
  source_checksum: string | null;
  verbatim_quote: string;
  normalized_offset: number | null;
  page: number | null;
  verification: "EXACT_VERIFIED" | "NOT_FOUND" | "UNKNOWN_SOURCE" | "TOO_SHORT";
  quote_verified: boolean;
  indicator: string | null;
}

export interface ReferenceSnapshotRef {
  snapshot_id: number | null;
  source: string;
  as_of?: string;
  checksum?: string;
  attested: boolean;
  attested_by?: string | null;
}

export interface TriggeredRule {
  rule_code: string;
  rule_type: "override" | "minimum_band";
  version: string | null;
  description: string | null;
  min_band: string;
  band_before: string | null;
  band_after: string;
  raised_band: boolean;
  mandatory_review: boolean;
  non_mitigable?: boolean;
  triggered_by: string[];
}

export async function getRiskFactors(
  assessmentId: number
): Promise<RiskFactor[]> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/risk-factors`
  );

  if (!response.ok) {
    throw new Error("Failed to fetch risk factors");
  }

  return response.json();
}

export interface RiskFactorManualCreate {
  category: string;
  applicable?: boolean;
  indicators?: string[];
  rationale: string;
  misuse_scenario?: string | null;
  added_by?: string;
}

export async function addRiskFactor(
  assessmentId: number,
  payload: RiskFactorManualCreate
): Promise<RiskFactor> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/risk-factors`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to add risk factor");
  }

  return response.json();
}

export async function excludeRiskFactor(
  assessmentId: number,
  riskFactorId: number,
  reason: string,
  excludedBy?: string
): Promise<RiskFactor> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/risk-factors/${riskFactorId}/exclude`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason, excluded_by: excludedBy ?? "System" }),
    }
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to exclude risk factor");
  }

  return response.json();
}

export async function rateRiskFactor(
  assessmentId: number,
  riskFactorId: number,
  likelihood: number,
  impact: number,
  // R10.3: required by the backend when the rating differs from the AI's
  // suggestion. The rater is the signed-in user.
  reason?: string
): Promise<RiskFactor> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/risk-factors/${riskFactorId}/rating`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        likelihood,
        impact,
        reason,
      }),
    }
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to rate risk factor");
  }

  return response.json();
}

export interface SuggestRiskFactorRatingsResult {
  factors: RiskFactor[];
  suggested_count: number;
  fallback_count: number;
  // True when at least one factor could not be estimated, so the UI can
  // say so rather than implying every factor now carries a real
  // model-produced suggestion.
  degraded: boolean;
}

export async function suggestRiskFactorRatings(
  assessmentId: number,
  options?: { includeRated?: boolean; requestedBy?: string }
): Promise<SuggestRiskFactorRatingsResult> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/risk-factors/suggest-ratings`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        include_rated: options?.includeRated ?? false,
        requested_by: options?.requestedBy ?? "System",
      }),
    }
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to suggest risk factor ratings");
  }

  return response.json();
}

export interface InherentRiskFactorBreakdown {
  category: string;
  weight: number;
  likelihood: number | null;
  impact: number | null;
  score: number;
  rated: boolean;
  evidence_status?: EvidenceStatus | null;
}

export interface InherentRiskCalculation {
  id: number;
  assessment_id: number;
  methodology_id: number | null;
  methodology_name: string | null;
  calculation_method: string;
  inputs: InherentRiskFactorBreakdown[];
  weights: Record<string, number>;
  thresholds: Record<string, number>;
  risk_bands: { name: string; min: number; max: number }[];
  escalation_rules: Record<string, unknown>[];
  // null while applicable factors exist but none is rated yet;
  // risk_band is then "UNRATED" unless a policy rule has set one.
  final_score: number | null;
  risk_band: string;
  is_provisional: boolean;
  rated_factor_count: number;
  applicable_factor_count: number;
  escalated: boolean;
  escalation_reasons: string[];
  triggered_rules: TriggeredRule[];
  mandatory_review: boolean;
  methodology_version: string | null;
  methodology_fingerprint: string | null;
  reference_data: {
    snapshots_used?: ReferenceSnapshotRef[];
    snapshots_skipped?: (ReferenceSnapshotRef & { would_match: string[] })[];
    unresolved_countries?: string[];
  };
  jurisdiction_matches: {
    iso_code: string;
    name: string;
    tier: string;
    source: string;
    as_of: string;
    snapshot_id: number | null;
  }[];
  calculated_by: string | null;
  calculated_at: string;
  overridden: boolean;
  calculated_score: number | null;
  calculated_band: string | null;
  override_value: number | null;
  override_band: string | null;
  override_reason: string | null;
  override_by: string | null;
  override_at: string | null;
  version: number;
  is_current: boolean;
}

// Residual = inherent band x control rating through the methodology's
// residual grid, held up by any non-mitigable rule. residual_band is the
// result; residual_score only places it within the band.
export interface ResidualRiskCalculation {
  id: number | null;
  assessment_id: number;
  methodology_version: string | null;
  methodology_fingerprint: string | null;
  inherent_band: string | null;
  inherent_score: number | null;
  control_rating: "WEAK" | "PARTIAL" | "EFFECTIVE" | null;
  control_ratings: {
    risk_factor_id: number;
    category: string;
    control_count: number;
    rating: "WEAK" | "PARTIAL" | "EFFECTIVE";
  }[];
  control_reduction: number | null;
  residual_grid: {
    version: string;
    cells: Record<string, Record<string, string>>;
  };
  grid_version: string | null;
  grid_band: string | null;
  floors_applied: { rule_code: string; band_before: string; band_after: string }[];
  residual_band: string | null;
  residual_score: number | null;
  reason: string | null;
  // R8: the human-confirmed residual risk (frozen calculation only).
  confirmed_band?: string | null;
  confirmed_score?: number | null;
  confirmation_reason?: string | null;
  confirmed_by?: string | null;
  confirmed_at?: string | null;
  confirmation_differs?: boolean;
  confirmed_score_difference?: number | null;
  frozen: boolean;
  calculated_at: string | null;
}

// R8: confirm or adjust the calculated residual risk, with a reason.
export async function confirmResidualRisk(
  assessmentId: number,
  payload: { band: string; score?: number | null; reason: string }
): Promise<ResidualRiskCalculation> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/residual-risk/confirm`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to confirm the residual risk");
  }

  return response.json();
}

// R8.6: a condition recommended for elevated residual risk.
export interface RecommendedCondition {
  id: number;
  assessment_id: number;
  condition_type: string;
  condition_label: string;
  recommended_text: string;
  rationale: string;
  status: "PROPOSED" | "ACCEPTED" | "MODIFIED" | "REJECTED";
  final_text: string | null;
  decision_reason: string | null;
  decided_by: string | null;
  decided_at: string | null;
  created_at: string;
}

export async function getRecommendedConditions(assessmentId: number): Promise<RecommendedCondition[]> {
  const response = await authFetch(`${API_BASE_URL}/api/assessments/${assessmentId}/recommended-conditions`);

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to fetch recommended conditions");
  }

  return response.json();
}

export async function decideRecommendedCondition(
  assessmentId: number,
  conditionId: number,
  payload: { decision: "accept" | "modify" | "reject"; reason: string; text?: string }
): Promise<RecommendedCondition> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/recommended-conditions/${conditionId}/decision`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to record the decision");
  }

  return response.json();
}

export async function getResidualRisk(
  assessmentId: number
): Promise<ResidualRiskCalculation> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/residual-risk`
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to fetch residual risk");
  }

  return response.json();
}

export async function getDecisionReadiness(
  assessmentId: number
): Promise<{ ready: boolean; missing: string[] }> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/decision-record/readiness`
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to fetch decision readiness");
  }

  return response.json();
}

export async function getInherentRiskCalculation(
  assessmentId: number
): Promise<InherentRiskCalculation> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/inherent-risk`
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to fetch inherent risk calculation");
  }

  return response.json();
}

// R6.5: every inherent-risk calculation version, newest first.
export async function getInherentRiskHistory(
  assessmentId: number
): Promise<InherentRiskCalculation[]> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/inherent-risk/history`
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to fetch inherent risk history");
  }

  return response.json();
}

export async function overrideInherentRisk(
  assessmentId: number,
  overrideValue: number,
  reason: string,
  overrideBand?: string,
  actor?: string
): Promise<InherentRiskCalculation> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/inherent-risk/override`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        override_value: overrideValue,
        override_band: overrideBand ?? null,
        reason,
        actor: actor ?? "System",
      }),
    }
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to override inherent risk calculation");
  }

  return response.json();
}

// ---------------------------------------------------------------------
// Stage 9 (R9.1-R9.4): structured, decision-ready assessment draft.
// ---------------------------------------------------------------------

export interface RiskStatement {
  category: string;
  statement: string;
}

export interface AssessmentDraft {
  id: number;
  assessment_id: number;
  executive_summary: string;
  business_change_description: string;
  business_profile: Record<string, unknown>;
  applicable_risk_categories: string[];
  risk_indicators: string[];
  risk_statements: RiskStatement[];
  inherent_risk: Record<string, unknown>;
  evidence_references: Record<string, unknown>[];
  mapped_controls: Record<string, unknown>[];
  control_effectiveness: Record<string, unknown>;
  residual_risk: Record<string, unknown>;
  risk_gaps: Record<string, unknown>[];
  assumptions: string[];
  missing_information: string[];
  recommended_conditions: string[];
  analyst_recommendation: string;
  // Stage 19 (Explainability): always "ADVISORY" -- a recommendation is
  // never a decision; show recommendation_notice next to it.
  recommendation_status?: "ADVISORY";
  recommendation_notice?: string;
  required_approvals: string[];
  uncertainty: {
    low_confidence_items?: string[];
    unsupported_conclusions?: string[];
    conflicting_evidence?: Record<string, unknown>[];
    unresolved_questions?: string[];
  };
  generated_at: string;
  generation_method: string;
  model_version: string | null;
  config_version: string | null;
  source_evidence: Record<string, unknown>[];
  generated_by: string | null;
  is_edited: boolean;
  edited_by: string | null;
  edited_at: string | null;
  accepted_by: string | null;
  accepted_at: string | null;
  version: number;
  is_current: boolean;
  created_at: string;
}

export interface AssessmentDraftVersionSummary {
  id: number;
  version: number;
  is_current: boolean;
  generated_at: string;
  is_edited: boolean;
  edited_by: string | null;
  edited_at: string | null;
  accepted_by: string | null;
  accepted_at: string | null;
}

export async function getAssessmentDraft(
  assessmentId: number
): Promise<AssessmentDraft> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/draft`
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to fetch assessment draft");
  }

  return response.json();
}

export async function generateAssessmentDraft(
  assessmentId: number
): Promise<AssessmentDraft> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/draft/generate`,
    { method: "POST" }
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to generate assessment draft");
  }

  return response.json();
}

export async function getAssessmentDraftVersions(
  assessmentId: number
): Promise<AssessmentDraftVersionSummary[]> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/draft/versions`
  );

  if (!response.ok) {
    throw new Error("Failed to fetch assessment draft versions");
  }

  return response.json();
}

// R9.3: one draft version in full (e.g. the original generated content).
export async function getAssessmentDraftVersion(
  assessmentId: number,
  draftId: number
): Promise<AssessmentDraft> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/draft/versions/${draftId}`
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to fetch assessment draft version");
  }

  return response.json();
}

export interface AssessmentDraftUpdatePayload {
  executive_summary?: string;
  business_change_description?: string;
  risk_statements?: RiskStatement[];
  assumptions?: string[];
  missing_information?: string[];
  recommended_conditions?: string[];
  analyst_recommendation?: string;
  required_approvals?: string[];
  edited_by: string;
}

export async function updateAssessmentDraft(
  assessmentId: number,
  payload: AssessmentDraftUpdatePayload
): Promise<AssessmentDraft> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/draft`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to update assessment draft");
  }

  return response.json();
}

export async function acceptAssessmentDraft(
  assessmentId: number,
  acceptedBy: string
): Promise<AssessmentDraft> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/draft/accept`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ accepted_by: acceptedBy }),
    }
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to accept assessment draft");
  }

  return response.json();
}

export async function updateAssessmentStatus(
  assessmentId: number,
  status: string
): Promise<Assessment> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/status?status=${encodeURIComponent(
      status
    )}`,
    {
      method: "PATCH",
      headers: {
        Accept: "application/json",
      },
    }
  );

  if (!response.ok) {
    // Surface the backend's actual reason (e.g. FastAPI 422 listing the
    // allowed enum values) instead of a generic message, so invalid status
    // strings are easy to diagnose from the UI.
    const detail = await readErrorDetail(response);

    throw new Error(
      detail
        ? `Failed to update assessment status (${response.status}): ${detail}`
        : `Failed to update assessment status (${response.status})`
    );
  }

  return response.json();
}
// The 7 tracked AI-analysis pipeline stages, in order. Kept in sync with
// backend/app/api/assessments.py's STAGE_ORDER. "Business Request" (the
// CreateAssessment form) precedes all of these and is not a status value.
// HUMAN_REVIEW is the last stage this endpoint covers -- from there, the
// AW approval workflow takes over (see submitToManager below).
export const STAGE_ORDER: string[] = [
  "INTAKE",
  "EVIDENCE_COLLECTION",
  "RISK_IDENTIFICATION",
  "INHERENT_RISK_ASSESSMENT",
  "CONTROL_ASSESSMENT",
  "RESIDUAL_RISK",
  "HUMAN_REVIEW",
];

/**
 * The single, server-validated way to move an assessment forward one
 * stage in the AI-analysis pipeline (INTAKE through HUMAN_REVIEW). Every
 * stage transition — including the ones that trigger a computation (e.g.
 * running the risk engine on entering RISK_IDENTIFICATION) — happens only
 * because of an explicit call to this function from a button click;
 * nothing advances automatically. Once at HUMAN_REVIEW, use
 * submitToManager instead to enter the AW approval workflow.
 */
export async function advanceAssessmentStage(
  assessmentId: number
): Promise<Assessment> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/advance-stage`,
    {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify({ decision: null }),
    }
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);

    throw new Error(
      detail
        ? `Failed to advance stage (${response.status}): ${detail}`
        : `Failed to advance stage (${response.status})`
    );
  }

  return response.json();
}

// R2.1: document categories the intake/evidence-collection stages
// accept. Kept in sync with backend/app/schemas/assessment_document.py.
export const DOCUMENT_TYPES: { value: string; label: string }[] = [
  { value: "BUSINESS_REQUIREMENT", label: "Business Requirement Document" },
  { value: "PRODUCT_SPECIFICATION", label: "Product Specification" },
  { value: "PROCESS_MAP", label: "Process Map" },
  { value: "CUSTOMER_INFORMATION", label: "Customer Information" },
  { value: "VENDOR_DOCUMENT", label: "Vendor Document" },
  { value: "CONTROL_DESCRIPTION", label: "Control Description" },
  { value: "REGULATORY_POLICY_REFERENCE", label: "Regulatory / Policy Reference" },
  { value: "PREVIOUS_ASSESSMENT", label: "Previous Assessment" },
  { value: "OTHER", label: "Other" },
];

// R2.2: confidentiality classifications.
export const CONFIDENTIALITY_LEVELS: { value: string; label: string }[] = [
  { value: "PUBLIC", label: "Public" },
  { value: "INTERNAL", label: "Internal" },
  { value: "CONFIDENTIAL", label: "Confidential" },
  { value: "RESTRICTED", label: "Restricted" },
];

export interface AssessmentDocument {
  id: number;
  assessment_id: number;
  filename: string;
  file_type: string;
  extracted_text: string;
  // R2.2: classification metadata.
  document_type: string;
  document_owner: string | null;
  source: string | null;
  effective_date: string | null;
  expiry_date: string | null;
  confidentiality: string | null;
  // R2.6: versioning.
  version: number;
  is_current: boolean;
  supersedes_id: number | null;
  superseded_at: string | null;
  created_at: string;
  // Stage 19: extracted_text is masked for a CONFIDENTIAL/RESTRICTED
  // document the viewer isn't entitled to see raw.
  is_masked?: boolean;
  // R15.3: false when the viewer may not open the original file (a
  // CONFIDENTIAL/RESTRICTED document they only see masked).
  can_open_original?: boolean;
  // Stage 19: latest background text-extraction job for this document.
  processing?: ProcessingJob | null;
}

// R2.2/R2.6: metadata a document upload can carry. All optional.
export interface DocumentUploadMetadata {
  document_type?: string;
  document_owner?: string;
  source?: string;
  effective_date?: string;
  expiry_date?: string;
  confidentiality?: string;
  // Set when this upload is a new version of an existing document.
  supersedes_id?: number;
}

// Stage 19 (Security): the document file endpoint now requires the
// bearer token, so a plain <a href> can't open it. Fetch it with the
// token and hand the browser a short-lived object URL instead.
export async function openDocumentFile(
  documentId: number,
  filename: string,
  mode: "view" | "download" = "view"
): Promise<void> {
  // Open the tab synchronously (inside the click) so popup blockers allow it.
  const viewer = mode === "view" ? window.open("", "_blank") : null;

  // R15.5: the server logs a view (inline) and a download (attachment)
  // separately.
  const disposition = mode === "view" ? "inline" : "attachment";
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/documents/${documentId}/file?disposition=${disposition}`
  );

  if (!response.ok) {
    viewer?.close();
    throw new Error(
      (await readErrorDetail(response)) || "The document could not be opened."
    );
  }

  const url = URL.createObjectURL(await response.blob());

  if (viewer) {
    viewer.location.href = url;
  } else {
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
  }

  window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

export async function getAssessmentDocuments(
  assessmentId: number
): Promise<AssessmentDocument[]> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/documents`
  );

  if (!response.ok) {
    throw new Error(
      "Failed to fetch assessment documents"
    );
  }

  return response.json();
}
// R3.2: a detected contradiction between the intake form and the
// structured profile.
export interface ConsistencyConflict {
  field: string;
  // R3.2: FORM_VS_PROFILE, or DOCUMENTS when two uploaded documents disagree.
  source?: "FORM_VS_PROFILE" | "DOCUMENTS";
  form_value: string | null;
  extracted_value: string | null;
  documents?: { document_id: number; filename: string; monthly_value: number; passage: string }[];
  message: string;
}

export interface AssessmentIntelligence {
  id: number;
  assessment_id: number;
  business_line: string | null;

  // R3.1: structured business/product profile fields.
  customer_type: string | null;
  transaction_origin: string | null;
  transaction_destination: string | null;
  transaction_frequency: string | null;
  payment_methods: string[];
  onboarding_approach: string | null;
  ownership_entity_structure: string | null;

  channels: string[];
  countries: string[];
  customer_segments: string[];

  transaction_volume: string | null;
  average_transaction_size: string | null;
  maximum_transaction_limit: string | null;

  third_party_vendors: string[];
  data_shared: string[];
  technologies: string[];
  regulatory_considerations: string[];
  existing_controls: string[];
  additional_risk_factors: string[];

  // R2.4: which document this was extracted from, if any.
  source_document_id: number | null;
  // R2.3: whether a human has reviewed/confirmed this extracted info.
  confirmed: boolean;
  confirmed_by: string | null;
  confirmed_at: string | null;

  // R3.2: contradictions found between the intake form and this profile.
  conflicts: ConsistencyConflict[];
  // R2.3: the AI's original value of every field since corrected.
  original_values?: Record<string, unknown>;
  // P4 (R2.4): per-field provenance with a deterministic confidence.
  field_provenance?: Record<string, FieldProvenance>;
  provenance_summary?: Record<string, number>;
  extraction_method?: "AI" | "RULES" | "INTAKE_FORM" | null;
  extracted_at?: string | null;
  source_document_ids?: number[];

  created_at: string;
}

// R2.3/R3.1: fields that can be corrected. Only the ones sent are
// changed; correcting anything clears `confirmed`.
export type AssessmentIntelligenceCorrection = Partial<
  Pick<
    AssessmentIntelligence,
    | "business_line"
    | "customer_type"
    | "transaction_origin"
    | "transaction_destination"
    | "transaction_frequency"
    | "payment_methods"
    | "onboarding_approach"
    | "ownership_entity_structure"
    | "channels"
    | "countries"
    | "customer_segments"
    | "transaction_volume"
    | "average_transaction_size"
    | "maximum_transaction_limit"
    | "third_party_vendors"
    | "data_shared"
    | "technologies"
    | "regulatory_considerations"
    | "existing_controls"
    | "additional_risk_factors"
  >
> & {
  // R3.4: required by the backend only when correcting an
  // already-confirmed/validated profile.
  changed_by?: string;
  change_reason?: string;
};

export async function getConsistencyCheck(
  assessmentId: number
): Promise<ConsistencyConflict[]> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/consistency-check`
  );

  if (!response.ok) {
    throw new Error("Failed to check profile consistency");
  }

  return response.json();
}

export async function updateAssessmentIntelligence(
  assessmentId: number,
  corrections: AssessmentIntelligenceCorrection
): Promise<AssessmentIntelligence> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/intelligence`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(corrections),
    }
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to save the correction");
  }

  return response.json();
}

export async function confirmAssessmentIntelligence(
  assessmentId: number,
  confirmedBy?: string
): Promise<AssessmentIntelligence> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/intelligence/confirm`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirmed_by: confirmedBy ?? "System" }),
    }
  );

  if (!response.ok) {
    throw new Error("Failed to confirm the extracted information");
  }

  return response.json();
}

// R2.5: a missing-evidence/information-request checklist item.
export interface EvidenceIssue {
  code: string;
  title: string;
  description: string;
  missing: boolean;
}

// R2.6: a warning about a current document (e.g. expired).
export interface DocumentWarning {
  document_id: number;
  filename: string;
  reason: string;
  // P4 (R2.6): whether the document may be used as evidence.
  document_version?: number | null;
  expiry_date?: string | null;
  condition?: "EXPIRED" | "INVALID_EXPIRY_DATE" | null;
  state?: "ACKNOWLEDGEMENT_REQUIRED" | "ACKNOWLEDGED_FOR_USE" | "EXCLUDED_FROM_EVIDENCE" | null;
  usable_as_evidence?: boolean;
  acknowledgement?: {
    id: number;
    decision: "USE_AS_EVIDENCE" | "EXCLUDE_FROM_EVIDENCE";
    reason: string;
    acknowledged_by: string;
    acknowledged_at: string | null;
  } | null;
}

export interface EvidenceGaps {
  assessment_id: number;
  issues: EvidenceIssue[];
  open_count: number;
  document_warnings: DocumentWarning[];
  acknowledgements_required?: number;
}

export async function getEvidenceGaps(
  assessmentId: number
): Promise<EvidenceGaps> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/evidence-gaps`
  );

  if (!response.ok) {
    throw new Error("Failed to fetch evidence gaps");
  }

  return response.json();
}

export async function getAssessmentIntelligence(
  assessmentId: number
): Promise<AssessmentIntelligence | null> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/intelligence`
  );

  // Intelligence is optional for older/manual assessments.
  if (response.status === 404) {
    return null;
  }

  if (!response.ok) {
    throw new Error("Failed to fetch assessment intelligence");
  }

  return response.json();
}
export interface AuditEvent {
  id: number;
  // null for standalone Risk Calculator activity not tied to one assessment.
  assessment_id: number | null;
  action: string;
  previous_status: string | null;
  new_status: string | null;
  details: string | null;
  actor: string | null;
  created_at: string;
}
export async function getAssessmentAudit(
  assessmentId: number
): Promise<AuditEvent[]> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/audit`
  );

  if (!response.ok) {
    throw new Error("Failed to fetch assessment audit");
  }

  return response.json();
}
export async function getAllAuditEvents(): Promise<AuditEvent[]> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/audit/all`
  );

  if (!response.ok) {
    throw new Error("Failed to fetch audit history");
  }

  return response.json();
}

export interface CalculatorAuditPayload {
  // Set when the calculator is embedded on a specific assessment's
  // detail page; omit/null for the standalone Risk Calculator page.
  assessment_id: number | null;
  weighted_total: number;
  risk_level: string;
  // Dimension -> score, for every factor currently toggled "included".
  included_factors: Record<string, number>;
  // Dimension -> weight actually used (0-1), including any manual
  // override of the engine's default weight.
  factor_weights?: Record<string, number>;
  actor?: string | null;
}

export async function logCalculatorAudit(
  payload: CalculatorAuditPayload
): Promise<AuditEvent> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/audit/calculator`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }
  );

  if (!response.ok) {
    throw new Error("Failed to log calculator audit event");
  }

  return response.json();
}

// Recorded as an R6.7 override of the calculated inherent risk: the
// backend derives the band and the user itself, and requires a reason.
export interface ManualScoreOverridePayload {
  weighted_total: number;
  reason: string;
  included_factors: Record<string, number>;
  factor_weights?: Record<string, number>;
}

// Thrown by applyManualScoreOverride when the backend refuses the
// override (e.g. the assessment has a final decision or is in a locked
// status) — lets callers show that message as-is.
export class ManualScoreOverrideError extends Error {}

export async function applyManualScoreOverride(
  assessmentId: number,
  payload: ManualScoreOverridePayload
): Promise<Assessment> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/manual-score`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }
  );

  if (!response.ok) {
    let detail = "Failed to apply the manual score override.";

    try {
      const body = await response.json();
      if (typeof body?.detail === "string") {
        detail = body.detail;
      }
    } catch {
      // Response body wasn't JSON — fall back to the generic message.
    }

    throw new ManualScoreOverrideError(detail);
  }

  return response.json();
}

export interface ManualScoreDraftPayload {
  scores: Record<string, number>;
  included: Record<string, boolean>;
  weights: Record<string, number>;
  likelihood?: Record<string, number>;
  impact?: Record<string, number>;
}

// Persists the calculator's current inputs for this assessment so they
// survive navigating away and back — independent of
// applyManualScoreOverride, and allowed regardless of status.
export async function saveManualScoreDraft(
  assessmentId: number,
  payload: ManualScoreDraftPayload
): Promise<Assessment> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/manual-score/draft`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }
  );

  if (!response.ok) {
    throw new Error("Failed to save the calculator draft");
  }

  return response.json();
}

// The standalone Risk Calculator page isn't tied to an assessment, so its
// last-used inputs are saved per user instead (null = nothing saved yet).
export async function getCalculatorDraft(): Promise<ManualScoreDraftPayload | null> {
  const response = await authFetch(`${API_BASE_URL}/api/risk-calculator/draft`);

  if (!response.ok) {
    throw new Error(
      (await readErrorDetail(response)) || "Failed to load the saved calculator values"
    );
  }

  const body: { draft: ManualScoreDraftPayload | null } = await response.json();
  return body.draft;
}

export async function saveCalculatorDraft(
  payload: ManualScoreDraftPayload
): Promise<void> {
  const response = await authFetch(`${API_BASE_URL}/api/risk-calculator/draft`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    throw new Error(
      (await readErrorDetail(response)) || "Failed to save the calculator values"
    );
  }
}

export async function updateAssessment(
  assessmentId: number,
  data: {
    title: string;
    change_type: string;
    description: string;
    evidence: string;
    // R1.3/R1.4: pass is_draft=false to submit a previously-saved draft
    // (validated server-side); omit/true to just edit fields in place.
    is_draft?: boolean;
    submitted_by?: string | null;
  } & AssessmentRequestFields
): Promise<Assessment> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}`,
    {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify(data),
    }
  );

  if (!response.ok) {
    let detail: unknown = null;

    try {
      detail = (await response.json())?.detail ?? null;
    } catch {
      // Response body wasn't JSON — fall back to the generic message.
    }

    const missingFields = parseMissingFieldsDetail(detail);

    if (missingFields) {
      throw new Error(
        `${missingFields.message} Missing: ${missingFields.missing_fields.join(", ")}.`
      );
    }

    throw new Error(
      typeof detail === "string" ? detail : "Failed to update assessment"
    );
  }

  return response.json();
}

export async function uploadAssessmentDocument(
  assessmentId: number,
  file: File,
  metadata: DocumentUploadMetadata = {}
): Promise<AssessmentDocument> {
  const formData = new FormData();
  formData.append("file", file);

  Object.entries(metadata).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      formData.append(key, String(value));
    }
  });

  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/documents`,
    {
      method: "POST",
      headers: {
        Accept: "application/json",
      },
      body: formData,
    }
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);

    throw new Error(
      detail ? `Failed to upload document: ${detail}` : "Failed to upload document"
    );
  }

  return response.json();
}
export interface AssessmentChallenge {
  id: number;
  assessment_id: number;
  challenge_id: string;
  status: string;
  outcome: string | null;
  comment: string | null;
  challenged_by: string | null;
  created_at: string;
  updated_at: string;
  findings: { title: string; severity: string; finding: string }[];
  inherent_score: number;
  // Preview through the residual grid. null (with residual_reason) when
  // the inherent result is provisional, or when a policy rule set the
  // band so no score applies.
  residual_score: number | null;
  residual_level: string | null;
  residual_reason?: string | null;
  control_rating?: "WEAK" | "PARTIAL" | "EFFECTIVE" | null;
  control_reduction: number;
}

export async function getAssessmentChallenge(
  assessmentId: number
): Promise<AssessmentChallenge> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/challenge`
  );

  if (!response.ok) {
    throw new Error("Failed to fetch control assessment (challenge)");
  }

  return response.json();
}

export async function updateAssessmentChallenge(
  assessmentId: number,
  data: { outcome: string; comment: string }
): Promise<AssessmentChallenge> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/challenge`,
    {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify(data),
    }
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);

    throw new Error(
      detail
        ? `Failed to save control assessment outcome (${response.status}): ${detail}`
        : `Failed to save control assessment outcome (${response.status})`
    );
  }

  return response.json();
}

// Stage 7 (R7.1): the control library. Kept in sync with
// backend/app/control_engine/library.py.
export const CONTROL_LIBRARY: { value: string; label: string }[] = [
  { value: "KYC_CUSTOMER_DUE_DILIGENCE", label: "KYC / Customer Due Diligence" },
  { value: "ENHANCED_DUE_DILIGENCE", label: "Enhanced Due Diligence" },
  {
    value: "BENEFICIAL_OWNERSHIP_VERIFICATION",
    label: "Beneficial Ownership Verification",
  },
  { value: "SANCTIONS_SCREENING", label: "Sanctions Screening" },
  { value: "TRANSACTION_MONITORING", label: "Transaction Monitoring" },
  { value: "FRAUD_MONITORING", label: "Fraud Monitoring" },
  { value: "TRANSACTION_LIMITS", label: "Transaction Limits" },
  { value: "GEOGRAPHIC_RESTRICTIONS", label: "Geographic Restrictions" },
  { value: "CUSTOMER_RISK_MONITORING", label: "Customer-Risk Monitoring" },
  { value: "VENDOR_DUE_DILIGENCE", label: "Vendor Due Diligence" },
  { value: "INVESTIGATION_PROCESSES", label: "Investigation Processes" },
  { value: "REGULATORY_REPORTING", label: "Regulatory Reporting" },
];

export const DESIGN_ADEQUACY_VALUES = ["ADEQUATE", "INADEQUATE", "NOT_ASSESSED"] as const;
export const OPERATING_EFFECTIVENESS_VALUES = [
  "EFFECTIVE",
  "PARTIALLY_EFFECTIVE",
  "INEFFECTIVE",
  "UNVERIFIED",
] as const;
export const CONTROL_CONDITION_STATUSES = [
  "OPEN",
  "IN_PROGRESS",
  "COMPLETED",
  "CANCELLED",
] as const;

export interface Control {
  id: number;
  assessment_id: number;
  risk_factor_id: number;
  control_type: string;
  description: string | null;
  owner: string | null;
  performing_department: string | null;
  frequency: string | null;
  trigger: string | null;
  scope: string | null;
  evidence_source: string | null;
  operating_status: string;
  added_by: string | null;
  version: number;
  is_current: boolean;
  superseded_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ControlCreatePayload {
  risk_factor_id: number;
  control_type: string;
  description?: string | null;
  owner?: string | null;
  performing_department?: string | null;
  frequency?: string | null;
  trigger?: string | null;
  scope?: string | null;
  evidence_source?: string | null;
  operating_status?: string;
  added_by?: string;
}

// A reason is required; risk_factor_id remaps the control. Every change is
// recorded as a new version (see api/governanceRecords.ts changeControl).
export interface ControlUpdatePayload {
  reason: string;
  risk_factor_id?: number;
  description?: string | null;
  owner?: string | null;
  performing_department?: string | null;
  frequency?: string | null;
  trigger?: string | null;
  scope?: string | null;
  evidence_source?: string | null;
  operating_status?: string;
}

export interface ControlAssessmentRecord {
  id: number;
  control_id: number;
  assessment_id: number;
  design_adequacy: string;
  design_rationale: string | null;
  operating_effectiveness: string;
  effectiveness_rationale: string | null;
  has_evidence: boolean;
  coverage_complete: boolean;
  depends_on_unavailable_data: boolean;
  assessed_by: string | null;
  assessed_at: string;
  version: number;
  is_current: boolean;
}

export interface ControlAssessmentPayload {
  design_adequacy: string;
  design_rationale?: string | null;
  operating_effectiveness: string;
  effectiveness_rationale?: string | null;
  has_evidence: boolean;
  coverage_complete?: boolean;
  depends_on_unavailable_data?: boolean;
  assessed_by?: string;
}

export interface ControlCondition {
  id: number;
  control_id: number;
  assessment_id: number;
  description: string;
  status: string;
  due_date: string | null;
  owner: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

export interface ControlConditionPayload {
  description: string;
  due_date?: string | null;
  owner?: string | null;
  created_by?: string;
}

export interface ControlGap {
  id: number;
  assessment_id: number;
  risk_factor_id: number | null;
  control_id: number | null;
  gap_type: string;
  description: string | null;
  detected_at: string;
  resolved: boolean;
  resolved_at: string | null;
}

export interface AssessmentControlSummary {
  assessment_id: number;
  controls: Control[];
  control_assessments: Record<number, ControlAssessmentRecord>;
  gaps: ControlGap[];
  conditions: ControlCondition[];
  control_reduction: number;
  risk_factor_count: number;
}

export async function getControlSummary(
  assessmentId: number
): Promise<AssessmentControlSummary> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/control-summary`
  );

  if (!response.ok) {
    throw new Error("Failed to fetch control summary");
  }

  return response.json();
}

export async function addControl(
  assessmentId: number,
  payload: ControlCreatePayload
): Promise<Control> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/controls`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to add control");
  }

  return response.json();
}

export async function updateControl(
  assessmentId: number,
  controlId: number,
  payload: ControlUpdatePayload
): Promise<Control> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/controls/${controlId}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to update control");
  }

  return response.json();
}

export async function assessControl(
  assessmentId: number,
  controlId: number,
  payload: ControlAssessmentPayload
): Promise<ControlAssessmentRecord> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/controls/${controlId}/assessments`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to save control assessment");
  }

  return response.json();
}

export async function addControlCondition(
  assessmentId: number,
  controlId: number,
  payload: ControlConditionPayload
): Promise<ControlCondition> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/controls/${controlId}/conditions`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to add control condition");
  }

  return response.json();
}

export async function updateControlCondition(
  assessmentId: number,
  conditionId: number,
  payload: { status?: string; description?: string; due_date?: string | null; owner?: string | null }
): Promise<ControlCondition> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/control-conditions/${conditionId}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to update control condition");
  }

  return response.json();
}

export async function identifyControlsForRisk(
  assessmentId: number,
  riskFactorId: number,
  autoCreate: boolean = false
): Promise<{ suggested_controls: string[]; reasoning: string }> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/risk-factors/${riskFactorId}/identify-controls`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ risk_factor_id: riskFactorId, auto_create: autoCreate }),
    }
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to identify controls");
  }

  return response.json();
}

export async function assessControlDesign(
  assessmentId: number,
  controlId: number
): Promise<{ design_adequacy: string; rationale: string }> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/controls/${controlId}/assess-design`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    }
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to assess control design");
  }

  return response.json();
}

export interface FcrmReview {
  assessment_id: number;
  justification: string;
  human_ratings: Record<number, string>;
  reviewed_by: string | null;
  updated_at: string | null;
}

export async function getFcrmReview(
  assessmentId: number
): Promise<FcrmReview> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/fcrm-review`
  );

  if (!response.ok) {
    throw new Error("Failed to fetch FCRM review");
  }

  return response.json();
}

// AW: hierarchical approval workflow. These replace the tail of the old
// 9-stage pipeline (HUMAN_REVIEW -> COMMITTEE_DECISION -> AUDIT/REJECTED)
// with an explicit Business User -> Manager -> Committee chain. Kept in
// sync with backend/app/api/approvals.py.
export const APPROVAL_STATUSES = {
  SUBMITTED_TO_MANAGER: "SUBMITTED_TO_MANAGER",
  RETURNED_BY_MANAGER: "RETURNED_BY_MANAGER",
  MANAGER_REJECTED: "MANAGER_REJECTED",
  READY_FOR_COMMITTEE: "READY_FOR_COMMITTEE",
  APPROVED: "APPROVED",
  APPROVED_WITH_CONDITIONS: "APPROVED_WITH_CONDITIONS",
  DEFERRED: "DEFERRED",
  REJECTED: "REJECTED",
} as const;

export async function submitToManager(assessmentId: number): Promise<Assessment> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/submit-to-manager`,
    { method: "POST", headers: { Accept: "application/json" } }
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to submit assessment to manager");
  }

  return response.json();
}

export type ManagerDecision = "approve" | "reject" | "return";

export async function submitManagerDecision(
  assessmentId: number,
  decision: ManagerDecision,
  comment?: string
): Promise<Assessment> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/manager-decision`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ decision, comment: comment ?? null }),
    }
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to record manager decision");
  }

  return response.json();
}

export type CommitteeDecisionOption =
  | "approve"
  | "approve_with_conditions"
  | "defer"
  | "reject";

// Stage 12 (R12.4): a structured condition of an "Approve with
// Conditions" decision.
export interface CommitteeConditionInput {
  description: string;
  owner: string;
  due_date: string; // ISO date (yyyy-mm-dd)
  priority: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
}

export async function submitCommitteeDecision(
  assessmentId: number,
  decision: CommitteeDecisionOption,
  rationale: string,
  structuredConditions?: CommitteeConditionInput[]
): Promise<Assessment> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/committee-decision`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({
        decision,
        rationale,
        structured_conditions: structuredConditions ?? [],
      }),
    }
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to record committee decision");
  }

  return response.json();
}

// ---------------------------------------------------------------------
// Stage 12 (R12.2, R12.4, R12.6): decision package, structured
// conditions tracked to completion, per-member votes, and the
// controlled-amendment reopen path.
// ---------------------------------------------------------------------

export interface CommitteeConditionRecord {
  id: number;
  assessment_id: number;
  description: string;
  owner: string;
  due_date: string;
  priority: string;
  status: string;
  completion_evidence: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

export interface CommitteeVoteRecord {
  id: number;
  assessment_id: number;
  member_id: number;
  member_name: string | null;
  // AW.7: set when a delegate cast this vote in member_id's seat.
  delegate_id?: number | null;
  delegate_name?: string | null;
  delegation_id?: number | null;
  vote: "APPROVE" | "DISSENT" | "ABSTAIN";
  comment: string | null;
  voted_at: string;
  // R12.6 history (append-only votes).
  version: number;
  is_current: boolean;
  superseded_at: string | null;
  superseded_by_id: number | null;
  cast_by_id: number | null;
  recast_reason: string | null;
}

export interface DecisionPackage {
  assessment_id: number;
  draft: AssessmentDraft | null;
  challenge_findings: Record<string, unknown>[];
  committee_conditions: CommitteeConditionRecord[];
  committee_votes: CommitteeVoteRecord[];
  open_issues: string[];
  assessment_history: Record<string, unknown>[];
  // R12.2: the highest-scoring rated factors.
  main_risk_drivers?: {
    category: string;
    score: number | null;
    severity: string | null;
    likelihood: number | null;
    impact: number | null;
    rationale: string | null;
  }[];
  // R10.4 / R6.7: calculated values with human values beside them.
  value_comparisons?: ValueComparison[];
  // R11: the current mandatory challenge-review sign-off.
  challenge_signoff?: ChallengeSignoff | null;
}

export async function getDecisionPackage(
  assessmentId: number
): Promise<DecisionPackage> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/decision-package`
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to fetch decision package");
  }

  return response.json();
}

export async function castCommitteeVote(
  assessmentId: number,
  vote: "APPROVE" | "DISSENT" | "ABSTAIN",
  comment?: string,
  recastReason?: string
): Promise<CommitteeVoteRecord> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/committee-votes`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // R12.6: a re-cast needs a reason; the earlier vote is kept.
      body: JSON.stringify({ vote, comment: comment ?? null, recast_reason: recastReason ?? null }),
    }
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to record vote");
  }

  return response.json();
}

export async function getCommitteeVotes(
  assessmentId: number
): Promise<CommitteeVoteRecord[]> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/committee-votes`
  );

  if (!response.ok) {
    throw new Error("Failed to fetch committee votes");
  }

  return response.json();
}

export async function getCommitteeConditions(
  assessmentId: number
): Promise<CommitteeConditionRecord[]> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/committee-conditions`
  );

  if (!response.ok) {
    throw new Error("Failed to fetch committee conditions");
  }

  return response.json();
}

export async function updateCommitteeCondition(
  assessmentId: number,
  conditionId: number,
  payload: { status?: string; completion_evidence?: string }
): Promise<CommitteeConditionRecord> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/committee-conditions/${conditionId}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to update committee condition");
  }

  return response.json();
}

export async function amendAssessment(
  assessmentId: number,
  reason: string,
  requestedBy?: string
): Promise<Assessment> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/amend`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason, requested_by: requestedBy ?? "System" }),
    }
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to open amendment");
  }

  return response.json();
}

// ---------------------------------------------------------------------
// Stage 18 (R18.1-R18.4): reassessment triggers and change comparison.
// ---------------------------------------------------------------------

export interface ReassessmentTrigger {
  id: number;
  assessment_id: number;
  trigger_type: string;
  description: string;
  detected_at: string;
  detected_by: string | null;
  status: string;
  dismissed_reason: string | null;
  resolved_by: string | null;
  resolved_at: string | null;
  reassessment_id: number | null;
}

export const REASSESSMENT_TRIGGER_TYPES = [
  "MAJOR_PRODUCT_CHANGE",
  "NEW_GEOGRAPHY",
  "NEW_CUSTOMER_SEGMENT",
  "NEW_VENDOR",
  "MATERIAL_TRANSACTION_VOLUME_CHANGE",
  "NEW_DELIVERY_CHANNEL",
  "NEW_TECHNOLOGY",
  "REGULATORY_POLICY_CHANGE",
  "SIGNIFICANT_CONTROL_FAILURE",
  "EXPIRY",
  "PERIODIC_REVIEW",
];

export async function checkReassessmentTriggers(
  assessmentId: number
): Promise<ReassessmentTrigger[]> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/reassessment/check-triggers`
  );

  if (!response.ok) {
    throw new Error("Failed to check reassessment triggers");
  }

  return response.json();
}

export async function getReassessmentTriggers(
  assessmentId: number
): Promise<ReassessmentTrigger[]> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/reassessment/triggers`
  );

  if (!response.ok) {
    throw new Error("Failed to fetch reassessment triggers");
  }

  return response.json();
}

export async function flagReassessmentTrigger(
  assessmentId: number,
  triggerType: string,
  description: string,
  flaggedBy?: string
): Promise<ReassessmentTrigger> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/reassessment/flag-trigger`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        trigger_type: triggerType,
        description,
        flagged_by: flaggedBy ?? "System",
      }),
    }
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to flag reassessment trigger");
  }

  return response.json();
}

export async function resolveReassessmentTrigger(
  assessmentId: number,
  triggerId: number,
  status: string,
  dismissedReason?: string,
  resolvedBy?: string
): Promise<ReassessmentTrigger> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/reassessment/triggers/${triggerId}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        status,
        dismissed_reason: dismissedReason ?? null,
        resolved_by: resolvedBy ?? "System",
      }),
    }
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to resolve reassessment trigger");
  }

  return response.json();
}

export interface ProposedChangeFieldsInput {
  product_or_service_name?: string;
  business_owner?: string;
  legal_entity?: string;
  customer_segment?: string;
  countries_jurisdictions?: string;
  delivery_channels?: string;
  expected_transaction_volume?: string;
  expected_transaction_value?: string;
  transaction_types?: string;
  third_party_vendor_usage?: string;
  technology_process_changes?: string;
}

export interface ProposeChangeResult {
  reassessment_id: number;
  triggers: ReassessmentTrigger[];
}

export async function proposeAssessmentChange(
  assessmentId: number,
  changes: ProposedChangeFieldsInput,
  reason: string,
  proposedBy?: string
): Promise<ProposeChangeResult> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/reassessment/propose-change`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ changes, reason, proposed_by: proposedBy ?? "System" }),
    }
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to propose assessment change");
  }

  return response.json();
}

export interface ReassessmentComparison {
  parent_assessment_id: number;
  reassessment_id: number;
  fields_changed: { field: string; old_value: string | null; new_value: string | null }[];
  reused_field_sources: Record<string, { source_assessment_id: number; source_date: string | null }>;
  parent: Record<string, unknown>;
  reassessment: Record<string, unknown>;
}

export async function getReassessmentComparison(
  assessmentId: number
): Promise<ReassessmentComparison> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/reassessment/compare`
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to fetch reassessment comparison");
  }

  return response.json();
}

// R10.1: sections a comment can be linked to (kept in sync with
// backend/app/schemas/assessment_comment.py's COMMENT_SECTIONS).
export const COMMENT_SECTIONS = [
  { value: "INTAKE", label: "Intake Information" },
  { value: "EVIDENCE", label: "Evidence" },
  { value: "RISK_FACTORS", label: "Risk Factors" },
  { value: "SCORES", label: "Scores" },
  { value: "RISK_RATIONALE", label: "Risk Rationale" },
  { value: "CONTROL_MAPPINGS", label: "Control Mappings" },
  { value: "RESIDUAL_RISK", label: "Residual Risk" },
  { value: "CONDITIONS", label: "Conditions" },
  { value: "MISSING_INFORMATION", label: "Missing Information" },
] as const;

export interface AssessmentComment {
  id: number;
  assessment_id: number;
  author_id: number;
  author_name: string | null;
  author_role: string | null;
  body: string;
  visibility: "ALL" | "MANAGER_AND_ABOVE" | "COMMITTEE_ONLY";
  section: string | null;
  related_entity_id: string | null;
  resolved: boolean;
  resolved_by: string | null;
  resolved_at: string | null;
  exception_reason: string | null;
  created_at: string;
}

export async function getAssessmentComments(
  assessmentId: number
): Promise<AssessmentComment[]> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/comments`
  );

  if (!response.ok) {
    throw new Error("Failed to fetch comments");
  }

  return response.json();
}

export async function postAssessmentComment(
  assessmentId: number,
  body: string,
  visibility: AssessmentComment["visibility"] = "ALL",
  section?: string | null,
  relatedEntityId?: string | null
): Promise<AssessmentComment> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/comments`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({
        body,
        visibility,
        section: section ?? null,
        related_entity_id: relatedEntityId ?? null,
      }),
    }
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to post comment");
  }

  return response.json();
}

export async function resolveAssessmentComment(
  assessmentId: number,
  commentId: number,
  options: { resolved?: boolean; exceptionReason?: string | null } = {}
): Promise<AssessmentComment> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/comments/${commentId}/resolve`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({
        resolved: options.resolved ?? true,
        exception_reason: options.exceptionReason ?? null,
      }),
    }
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to update comment");
  }

  return response.json();
}

// Stage 10 (R10.2/R10.3/R10.4): analyst overrides of AI-generated values.
export const OVERRIDE_SECTIONS = [
  { value: "INTAKE_FIELD", label: "Extracted Field" },
  { value: "RISK_CATEGORY", label: "Risk Category" },
  { value: "FACTOR_RATING", label: "Factor Rating" },
  { value: "RISK_RATIONALE", label: "Risk Rationale" },
  { value: "CONTROL_MAPPING", label: "Control Mapping" },
  { value: "CONTROL_EFFECTIVENESS", label: "Control Effectiveness" },
  { value: "RESIDUAL_RISK", label: "Residual Risk" },
  { value: "CONDITION", label: "Recommended Condition" },
] as const;

export interface AssessmentOverride {
  id: number;
  assessment_id: number;
  section: string;
  field_name: string;
  entity_id: string | null;
  ai_value: string | null;
  human_value: string;
  reason: string;
  overridden_by: string | null;
  overridden_by_id: number | null;
  created_at: string;
  // P2: SYSTEM when the server read ai_value from the record.
  ai_value_source?: "SYSTEM" | null;
  review_status?: "APPLIED" | "PROPOSED" | "CONFIRMED" | "REJECTED" | null;
  reviewed_by?: string | null;
  reviewed_at?: string | null;
  review_note?: string | null;
}

// The system value (ai_value) is read from the record by the server; it is
// not part of the request.
export interface OverrideCreatePayload {
  section: string;
  field_name: string;
  entity_id?: string | null;
  human_value: string;
  reason: string;
}

export async function getAssessmentOverrides(
  assessmentId: number
): Promise<AssessmentOverride[]> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/overrides`
  );

  if (!response.ok) {
    throw new Error("Failed to fetch overrides");
  }

  return response.json();
}

export async function createAssessmentOverride(
  assessmentId: number,
  payload: OverrideCreatePayload
): Promise<AssessmentOverride> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/overrides`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(payload),
    }
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to save override");
  }

  return response.json();
}

// Stage 10 (R10.5): request more information, returning the assessment
// to the business owner or another team for clarification.
export async function requestAssessmentInformation(
  assessmentId: number,
  target: string,
  note: string
): Promise<Assessment> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/request-information`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ target, note }),
    }
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to request information");
  }

  return response.json();
}

export async function provideAssessmentInformation(
  assessmentId: number,
  response_: string
): Promise<Assessment> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/provide-information`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ response: response_ }),
    }
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to submit the requested information");
  }

  return response.json();
}

export async function saveFcrmReview(
  assessmentId: number,
  data: {
    justification: string;
    human_ratings: Record<number, string>;
  }
): Promise<FcrmReview> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/fcrm-review`,
    {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify(data),
    }
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);

    throw new Error(
      detail
        ? `Failed to save FCRM review (${response.status}): ${detail}`
        : `Failed to save FCRM review (${response.status})`
    );
  }

  return response.json();
}

// Stage 11 (R11.1-R11.6): Conditional Challenge Review.
export interface ChallengeTrigger {
  name: string;
  label: string;
  fired: boolean;
}

export interface ChallengeFindingRecord {
  id: number;
  assessment_id: number;
  category: string;
  description: string;
  related_section: string;
  severity: string;
  supporting_evidence: string | null;
  recommended_action: string | null;
  resolution_status: "OPEN" | "RESOLVED" | "ACCEPTED";
  resolved_by: string | null;
  resolved_at: string | null;
  resolution_note: string | null;
  accepted_by: string | null;
  accepted_at: string | null;
  accepted_reason: string | null;
  // "COMMITTEE" for a documented Committee exception; null for an
  // acceptance under the earlier rule, which no longer counts (G-4).
  acceptance_authority?: string | null;
  detected_at: string;
}

export interface ChallengeReview {
  assessment_id: number;
  triggered: boolean;
  triggers: ChallengeTrigger[];
  findings: ChallengeFindingRecord[];
  high_severity_open_count: number;
}

export async function getChallengeReview(assessmentId: number): Promise<ChallengeReview> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/challenge-review`
  );

  if (!response.ok) {
    throw new Error("Failed to fetch challenge review");
  }

  return response.json();
}

export async function resolveChallengeFinding(
  assessmentId: number,
  findingId: number,
  resolutionNote: string,
  resolvedBy?: string
): Promise<ChallengeFindingRecord> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/challenge-findings/${findingId}/resolve`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ resolution_note: resolutionNote, resolved_by: resolvedBy }),
    }
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to resolve challenge finding");
  }

  return response.json();
}

export async function acceptChallengeFinding(
  assessmentId: number,
  findingId: number,
  reason: string,
  acceptedBy?: string
): Promise<ChallengeFindingRecord> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/challenge-findings/${findingId}/accept`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason, accepted_by: acceptedBy }),
    }
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to accept challenge finding");
  }

  return response.json();
}

export interface ChallengeTriggerConfig {
  id: number;
  name: string;
  is_active: boolean;
  trigger_risk_levels: string[];
  residual_risk_tolerance: number;
  rating_mismatch_enabled: boolean;
  low_confidence_enabled: boolean;
  high_risk_jurisdictions: string[];
  high_risk_technologies: string[];
  // R11.1: switched-off triggers, and which ones can/can't be switched off.
  disabled_triggers: string[];
  configurable_triggers: string[];
  mandatory_triggers: string[];
  created_at: string;
  updated_at: string;
}

export async function getChallengeTriggerConfig(): Promise<ChallengeTriggerConfig> {
  const response = await authFetch(`${API_BASE_URL}/api/challenge-triggers`);

  if (!response.ok) {
    throw new Error("Failed to fetch challenge trigger configuration");
  }

  return response.json();
}

export async function updateChallengeTriggerConfig(
  payload: Partial<
    Pick<
      ChallengeTriggerConfig,
      | "name"
      | "trigger_risk_levels"
      | "residual_risk_tolerance"
      | "rating_mismatch_enabled"
      | "low_confidence_enabled"
      | "high_risk_jurisdictions"
      | "high_risk_technologies"
      | "disabled_triggers"
    >
  >
): Promise<ChallengeTriggerConfig> {
  const response = await authFetch(`${API_BASE_URL}/api/challenge-triggers`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to update challenge trigger configuration");
  }

  return response.json();
}

// ---------------------------------------------------------------------
// Stage 13 (R13.1-R13.5): trackable action items -- unifies control
// gaps, committee conditions, missing evidence, policy exceptions,
// vendor remediation, and monitoring enhancements into one owner/
// due-date/priority/status/evidence shape, with escalation and a
// closure-approval workflow for the higher-stakes sources.
// ---------------------------------------------------------------------

export const ACTION_ITEM_SOURCE_TYPES = [
  { value: "CONTROL_GAP", label: "Control Gap" },
  { value: "COMMITTEE_CONDITION", label: "Committee Condition" },
  { value: "MISSING_EVIDENCE", label: "Missing Evidence" },
  { value: "POLICY_EXCEPTION", label: "Policy Exception" },
  { value: "VENDOR_REMEDIATION", label: "Vendor Remediation" },
  { value: "MONITORING_ENHANCEMENT", label: "Monitoring Enhancement" },
] as const;

export const ACTION_ITEM_PRIORITIES = ["LOW", "MEDIUM", "HIGH", "CRITICAL"] as const;

export interface ActionItem {
  id: number;
  assessment_id: number;
  source_type: string;
  risk_factor_id: number | null;
  control_id: number | null;
  control_gap_id: number | null;
  committee_condition_id: number | null;
  source_reference: string | null;
  title: string;
  description: string | null;
  owner: string | null;
  department: string | null;
  due_date: string | null;
  priority: string;
  status: string;
  completion_evidence: string | null;
  escalated: boolean;
  escalated_at: string | null;
  escalation_note: string | null;
  closure_requested_by: string | null;
  closure_requested_at: string | null;
  closure_decision: string | null;
  closure_decided_by: string | null;
  closure_decided_at: string | null;
  closure_decision_note: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

export interface ActionItemCreatePayload {
  source_type: string;
  title: string;
  description?: string | null;
  risk_factor_id?: number | null;
  control_id?: number | null;
  control_gap_id?: number | null;
  source_reference?: string | null;
  owner?: string | null;
  department?: string | null;
  due_date?: string | null;
  priority?: string;
}

export interface ActionItemUpdatePayload {
  title?: string;
  description?: string | null;
  owner?: string | null;
  department?: string | null;
  due_date?: string | null;
  priority?: string;
  status?: string;
}

export async function getActionItems(
  assessmentId: number,
  status: "open" | "closed" | "all" = "all"
): Promise<ActionItem[]> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/action-items?status=${status}`
  );

  if (!response.ok) {
    throw new Error("Failed to fetch action items");
  }

  return response.json();
}

export async function createActionItem(
  assessmentId: number,
  payload: ActionItemCreatePayload
): Promise<ActionItem> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/action-items`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to create action item");
  }

  return response.json();
}

export async function updateActionItem(
  assessmentId: number,
  actionItemId: number,
  payload: ActionItemUpdatePayload
): Promise<ActionItem> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/action-items/${actionItemId}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to update action item");
  }

  return response.json();
}

export async function requestActionItemClosure(
  assessmentId: number,
  actionItemId: number,
  completionEvidence: string
): Promise<ActionItem> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/action-items/${actionItemId}/request-closure`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ completion_evidence: completionEvidence }),
    }
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to request action item closure");
  }

  return response.json();
}

export async function decideActionItemClosure(
  assessmentId: number,
  actionItemId: number,
  decision: "approve" | "reject",
  note?: string
): Promise<ActionItem> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/action-items/${actionItemId}/closure-decision`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision, note: note ?? null }),
    }
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to record closure decision");
  }

  return response.json();
}

export async function syncActionItems(
  assessmentId: number
): Promise<{ created: number; items: ActionItem[] }> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/action-items/sync`,
    { method: "POST" }
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to sync action items");
  }

  return response.json();
}

// Stage 16 (R16.1-R16.4): Audit Trail and Explainability.
export interface AssessmentExplanationSection {
  rating: string;
  value: number | null;
  level: string | null;
  explanation: string;
  [key: string]: unknown;
}

export interface AssessmentExplanationOverride {
  section: string;
  field_name: string;
  entity_id: string | null;
  ai_value: string | null;
  human_value: string;
  reason: string;
  overridden_by: string | null;
  created_at: string;
}

export interface AssessmentExplanationFinding {
  category: string;
  severity: string;
  description: string;
  resolution_status: string;
}

export interface AssessmentExplanation {
  assessment_id: number;
  sections: AssessmentExplanationSection[];
  overrides: AssessmentExplanationOverride[];
  challenge_findings: AssessmentExplanationFinding[];
}

export async function getAssessmentExplanation(
  assessmentId: number
): Promise<AssessmentExplanation> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/explain`
  );

  if (!response.ok) {
    throw new Error("Failed to load rating explanation");
  }

  return response.json();
}

export async function downloadAuditExport(assessmentId: number): Promise<Blob> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/audit-export`
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to export audit package");
  }

  return response.blob();
}

export interface AssessmentRetentionInfo {
  assessment_id: number;
  legal_hold: boolean;
  legal_hold_reason: string | null;
  legal_hold_set_by: string | null;
  legal_hold_set_at: string | null;
  is_deleted: boolean;
  deleted_by: string | null;
  deleted_at: string | null;
  deletion_reason: string | null;
  eligible_for_deletion_at: string | null;
  retention_expired: boolean;
  // P5: the server's eligibility result, hold history (retention
  // administrators only) and what the signed-in user may do.
  eligibility: import("./retention").Eligibility & { policy_status: string };
  policy_version_id: number | null;
  policy_version: number | null;
  retention_days: number | null;
  retention_policy_version_id: number | null;
  hold_detail_visible: boolean;
  hold_history: import("./retention").HoldEvent[];
  actions: {
    set_hold?: { allowed: boolean; reason: string | null };
    release_hold?: { allowed: boolean; reason: string | null };
    soft_delete?: { allowed: boolean; reason: string | null };
  };
  policy_status: string;
}

export async function getAssessmentRetention(
  assessmentId: number
): Promise<AssessmentRetentionInfo> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/retention`
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to load retention status");
  }

  return response.json();
}

export async function setAssessmentLegalHold(
  assessmentId: number,
  hold: boolean,
  reason: string,
  matterReference?: string
): Promise<AssessmentRetentionInfo> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/retention/legal-hold`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ hold, reason, matter_reference: matterReference || null }),
    }
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to update legal hold");
  }

  return response.json();
}

export async function softDeleteAssessment(
  assessmentId: number,
  reason: string
): Promise<AssessmentRetentionInfo> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/soft-delete`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason }),
    }
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to delete assessment");
  }

  return response.json();
}

export interface RetentionPolicy {
  id: number;
  name: string;
  is_active: boolean;
  default_retention_days: number;
  created_at: string;
  updated_at: string;
  // P5: read through to the active version; changes go through a proposal.
  active_version_id?: number | null;
  active_version?: number | null;
  pending_version_id?: number | null;
  pending_retention_days?: number | null;
  policy_status?: string;
}

export async function getRetentionPolicy(): Promise<RetentionPolicy> {
  const response = await authFetch(`${API_BASE_URL}/api/retention-policy`);

  if (!response.ok) {
    throw new Error("Failed to load retention policy");
  }

  return response.json();
}

export async function updateRetentionPolicy(
  // P5: creates a proposal that needs independent approval; change_reason is required.
  payload: { default_retention_days: number; change_reason: string }
): Promise<RetentionPolicy> {
  const response = await authFetch(`${API_BASE_URL}/api/retention-policy`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to update retention policy");
  }

  return response.json();
}

// ---------------------------------------------------------------------------
// OCC supervisory risk categories (OCC NR 96-2a, "Categories of Risk").
//
// A read-only lens over the factors already rated under RISK_CATEGORIES
// above -- analysts never rate these directly, and nothing here affects an
// assessment's score. Kept in sync with
// backend/app/schemas/risk_factor.py.
// ---------------------------------------------------------------------------

export interface OccContributingFactor {
  risk_factor_id: number;
  category: string;
  score: number;
  severity: string;
  rated: boolean;
  excluded: boolean;
}

export interface OccRiskCategoryRollup {
  occ_category: string;
  label: string;
  definition: string;
  // False for OCC categories nothing in the assessed taxonomy measures
  // (Interest Rate, Price). Those carry a null score rather than 0.
  covered: boolean;
  score: number | null;
  risk_band: string | null;
  max_factor_score: number | null;
  is_provisional: boolean;
  factor_count: number;
  contributing_factors: OccContributingFactor[];
}

export interface OccRiskProfile {
  assessment_id: number;
  categories: OccRiskCategoryRollup[];
  uncovered_categories: string[];
  is_provisional: boolean;
  source: string;
}

export async function getOccRiskProfile(
  assessmentId: number
): Promise<OccRiskProfile> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/occ-risk-profile`
  );

  if (!response.ok) {
    throw new Error("Failed to fetch OCC risk profile");
  }

  return response.json();
}

// AI evidence check: suggestions that uploaded documents support a control.
// The AI only suggests; accepting one records evidence on the control.
export interface EvidenceLink {
  id: number;
  control_id: number;
  document_id: number | null;
  document_name: string | null;
  document_version: number | null;
  // False once the document has been replaced by a newer version.
  document_is_current: boolean;
  support_level: "SUPPORTED" | "PARTIAL" | "NONE";
  confidence: "LOW" | "MEDIUM" | "HIGH" | null;
  quote: string | null;
  rationale: string | null;
  shortfalls: string[];
  suggested_effectiveness: string | null;
  status: "SUGGESTED" | "ACCEPTED" | "REJECTED";
  model: string | null;
  checked_at: string;
  decided_by: string | null;
  decided_at: string | null;
  decision_note: string | null;
}

export interface EvidenceCheckSummary {
  status: "OK" | "NO_DOCUMENTS" | "AI_UNAVAILABLE" | "NO_CONTROLS";
  controls_checked: number;
  controls_failed: number;
  links: EvidenceLink[];
}

export async function listEvidenceLinks(assessmentId: number): Promise<EvidenceLink[]> {
  const response = await authFetch(`${API_BASE_URL}/api/assessments/${assessmentId}/evidence-links`);
  if (!response.ok) {
    throw new Error("Failed to fetch evidence suggestions");
  }
  return response.json();
}

export async function runEvidenceCheck(assessmentId: number): Promise<EvidenceCheckSummary> {
  const response = await authFetch(`${API_BASE_URL}/api/assessments/${assessmentId}/evidence-check`, {
    method: "POST",
  });
  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "The evidence check couldn't be run");
  }
  return response.json();
}

export async function decideEvidenceLink(
  assessmentId: number,
  linkId: number,
  decision: "ACCEPT" | "REJECT",
  note?: string
): Promise<EvidenceLink> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/evidence-links/${linkId}/decision`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision, note: note || null }),
    }
  );
  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "The decision couldn't be recorded");
  }
  return response.json();
}
