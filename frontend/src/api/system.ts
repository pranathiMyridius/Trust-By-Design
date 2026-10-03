import { authFetch } from "./http";
import { API_BASE_URL } from "./config";


// Stage 19: admin-only operational endpoints (performance against service
// targets, backup/recovery, integrity, security posture) and the
// explainability statement breakdown.

async function getJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await authFetch(`${API_BASE_URL}${path}`, init);

  if (!response.ok) {
    let detail = "";
    try {
      const parsed = await response.json();
      detail = typeof parsed?.detail === "string" ? parsed.detail : "";
    } catch {
      // fall through to the generic message
    }
    const error = new Error(detail || `Request failed (HTTP ${response.status}).`);
    (error as Error & { status?: number }).status = response.status;
    throw error;
  }

  return response.json();
}

export interface RoutePerformance {
  method: string;
  route: string;
  category: "PAGE" | "RISK_CALC" | "SUBMIT" | "BACKGROUND_OR_EXTERNAL";
  target_ms: number | null;
  samples: number;
  p50_ms: number;
  p95_ms: number;
  max_ms: number;
  breaches: number;
  within_target: boolean;
  errors: number;
}

export interface PerformanceSummary {
  targets_ms: Record<"PAGE" | "RISK_CALC" | "SUBMIT", number>;
  window: number;
  routes: RoutePerformance[];
  routes_outside_target: number;
}

export interface BackupSummary {
  name: string;
  label: string | null;
  created_at: string | null;
  created_by: string | null;
  database_engine: string | null;
  encrypted: boolean;
  uploaded_file_count: number | null;
  size_bytes: number;
  duration_seconds: number | null;
  valid_manifest: boolean;
}

export interface BackupVerification {
  name: string;
  valid: boolean;
  problems: string[];
}

export interface RecoveryStatus {
  recovery_point_objective_hours: number;
  recovery_time_objective_hours: number;
  backup_interval_hours: number;
  backup_directory: string;
  backups_retained: number;
  latest_backup: BackupSummary | null;
  latest_backup_age_hours: number | null;
  rpo_met: boolean;
  soft_delete_only: boolean;
  audit_log_append_only: boolean;
}

export interface IntegrityReport {
  database_engine: string;
  database_check: string;
  documents_missing_files: { document_id: number; assessment_id: number; filename: string }[];
  healthy: boolean;
}

export interface SecurityPosture {
  jwt_secret_configured: boolean;
  file_encryption_at_rest: boolean;
  https_enforced: boolean;
  database_tls_required: boolean;
  ai_payload_masking: boolean;
  all_api_routes_require_authentication: boolean;
  admin_actions_logged: boolean;
  recommendations: string[];
}

export const getPerformance = () => getJson<PerformanceSummary>("/api/system/performance");
export const getRecoveryStatus = () => getJson<RecoveryStatus>("/api/system/recovery-status");
export const getIntegrity = () => getJson<IntegrityReport>("/api/system/integrity");
export const getSecurityPosture = () => getJson<SecurityPosture>("/api/system/security-posture");
export const listBackups = () => getJson<BackupSummary[]>("/api/system/backups");
export const createBackup = () =>
  getJson<BackupSummary>("/api/system/backups", { method: "POST" });
export const verifyBackup = (name: string) =>
  getJson<BackupVerification>(`/api/system/backups/${encodeURIComponent(name)}/verify`, {
    method: "POST",
  });

// --- Explainability ---------------------------------------------------------

export type StatementKind = "FACT" | "ASSUMPTION" | "RECOMMENDATION" | "DECISION";

export interface ExplainStatement {
  kind: StatementKind;
  statement: string;
  origin: "HUMAN" | "AUTOMATED";
  source: string;
  basis: string | null;
  actor: string | null;
  model_version: string | null;
  recorded_at: string | null;
  review_status: "REVIEWED" | "PENDING_REVIEW" | null;
  reference: { type: string; id: number } | null;
  // P4 (R5.4): what the statement rests on; null for decisions.
  evidence_category?: EvidenceCategory | null;
  location?: Record<string, string | number> | null;
}

export type EvidenceCategory =
  | "DIRECT_EVIDENCE"
  | "EXTRACTED_INFORMATION"
  | "SYSTEM_INTERPRETATION"
  | "ANALYST_COMMENTARY"
  | "ASSUMPTION";

export interface ExplainStatements {
  assessment_id: number;
  advisory_notice: string;
  final_decision_recorded: boolean;
  counts: Record<StatementKind, number>;
  evidence_categories?: Record<EvidenceCategory, string>;
  evidence_category_counts?: Record<EvidenceCategory, number>;
  automated_pending_review: number;
  statements: ExplainStatement[];
}

export const getExplainStatements = (assessmentId: number) =>
  getJson<ExplainStatements>(`/api/assessments/${assessmentId}/explain/statements`);
