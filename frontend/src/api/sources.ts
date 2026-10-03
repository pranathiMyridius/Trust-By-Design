import { API_BASE_URL } from "./config";
import { authFetch } from "./http";

// R5.1-R5.3: the approved evidence-source library and evidence retrieval.

export const SOURCE_TYPES: { value: string; label: string }[] = [
  { value: "INTERNAL_POLICY", label: "Internal policy" },
  { value: "PROCEDURE", label: "Procedure" },
  { value: "CONTROL_STANDARD", label: "Control standard" },
  { value: "REGULATORY_GUIDANCE", label: "Regulatory guidance" },
  { value: "RISK_FRAMEWORK", label: "Approved risk framework" },
  { value: "PREVIOUS_ASSESSMENT", label: "Previous assessment" },
  { value: "VENDOR_CONTROL_DOCUMENTATION", label: "Vendor-control documentation" },
];

export interface SourceFields {
  title: string;
  source_type: string;
  issuer: string | null;
  version: string;
  effective_date: string | null;
  review_date: string | null;
  reference: string | null;
  content: string;
}

export interface ApprovedSource extends SourceFields {
  id: number;
  source_type_label: string;
  status: "DRAFT" | "APPROVED" | "RETIRED";
  outdated: boolean;
  // The original uploaded document, if the source was created from one.
  has_file?: boolean;
  original_filename?: string | null;
  file_content_type?: string | null;
  file_size?: number | null;
  file_sha256?: string | null;
  approved_by: string | null;
  approved_at: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface SourceSearchResult {
  source_id: number;
  source_title: string;
  source_type: string;
  source_type_label: string;
  issuer: string | null;
  source_version: string;
  effective_date: string | null;
  review_date: string | null;
  reference: string | null;
  passage: string;
  matched_terms: string[];
  score: number;
  outdated: boolean;
  retrieved_at: string;
}

export interface SourceEvidenceLink {
  id: number;
  assessment_id: number;
  risk_factor_id: number | null;
  source_id: number;
  source_title: string;
  source_type: string;
  source_version: string;
  effective_date: string | null;
  reference: string | null;
  passage: string;
  retrieved_at: string;
  retrieved_by: string | null;
  outdated: boolean;
  source_status: string | null;
  // P4: past its review date when attached, and the acknowledgement given.
  outdated_at_attach?: boolean;
  outdated_acknowledgement_reason?: string | null;
}

export interface FactorSourceEvidence {
  risk_factor_id: number;
  linked: SourceEvidenceLink[];
  suggestions: SourceSearchResult[];
  supported: boolean;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await authFetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", Accept: "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    let detail = "";
    try {
      const body = await response.json();
      detail =
        typeof body?.detail === "string"
          ? body.detail
          : typeof body?.detail?.message === "string"
          ? body.detail.message
          : Array.isArray(body?.detail)
          ? body.detail.map((item: { msg?: string }) => item.msg).filter(Boolean).join(" ")
          : "";
    } catch {
      // not JSON
    }
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return response.json();
}

export interface SourceExtraction {
  filename: string;
  title: string;
  content: string;
  characters: number;
}

export const SOURCE_UPLOAD_ACCEPT = ".pdf,.docx,.doc,.xlsx,.txt,.csv";

/** R5.1: read an uploaded document's text for the "Add source" form (nothing is stored). */
export async function extractSourceFile(file: File): Promise<SourceExtraction> {
  const body = new FormData();
  body.append("file", file);
  // No Content-Type header: the browser sets the multipart boundary.
  const response = await authFetch(`${API_BASE_URL}/api/sources/extract`, { method: "POST", body });
  if (!response.ok) {
    let detail = "";
    try {
      const payload = await response.json();
      detail = typeof payload?.detail === "string" ? payload.detail : "";
    } catch {
      // not JSON
    }
    throw new Error(detail || `Upload failed (${response.status})`);
  }
  return response.json();
}

async function failure(response: Response, fallback: string): Promise<Error> {
  try {
    const payload = await response.json();
    if (typeof payload?.detail === "string") return new Error(payload.detail);
    if (Array.isArray(payload?.detail)) {
      return new Error(payload.detail.map((item: { msg?: string }) => item.msg).filter(Boolean).join(" ") || fallback);
    }
  } catch {
    // not JSON
  }
  return new Error(fallback);
}

/** A draft source with its original document kept (encrypted at rest). */
export async function createSourceWithFile(fields: SourceFields, file: File): Promise<ApprovedSource> {
  const body = new FormData();
  Object.entries(fields).forEach(([key, value]) => {
    if (value !== null && value !== undefined && value !== "") body.append(key, String(value));
  });
  body.append("file", file);
  const response = await authFetch(`${API_BASE_URL}/api/sources/with-file`, { method: "POST", body });
  if (!response.ok) throw await failure(response, `Saving failed (${response.status})`);
  return response.json();
}

/** Downloads a source's original document (the download is audited). */
export async function downloadSourceFile(sourceId: number, filename: string): Promise<void> {
  const response = await authFetch(`${API_BASE_URL}/api/sources/${sourceId}/file`);
  if (!response.ok) throw await failure(response, `Download failed (${response.status})`);
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function listSources(): Promise<ApprovedSource[]> {
  return request("/api/sources");
}

export function createSource(fields: SourceFields): Promise<ApprovedSource> {
  return request("/api/sources", { method: "POST", body: JSON.stringify(fields) });
}

export function updateSource(id: number, fields: Partial<SourceFields>): Promise<ApprovedSource> {
  return request(`/api/sources/${id}`, { method: "PATCH", body: JSON.stringify(fields) });
}

export function approveSource(id: number, reason: string): Promise<ApprovedSource> {
  return request(`/api/sources/${id}/approve`, { method: "POST", body: JSON.stringify({ reason }) });
}

export function retireSource(id: number, reason: string): Promise<ApprovedSource> {
  return request(`/api/sources/${id}/retire`, { method: "POST", body: JSON.stringify({ reason }) });
}

export function searchSources(query: string): Promise<SourceSearchResult[]> {
  return request(`/api/sources/search?q=${encodeURIComponent(query)}`);
}

export function getFactorSourceEvidence(assessmentId: number, riskFactorId: number): Promise<FactorSourceEvidence> {
  return request(`/api/assessments/${assessmentId}/risk-factors/${riskFactorId}/source-evidence`);
}

export function attachSourceEvidence(
  assessmentId: number,
  riskFactorId: number,
  sourceId: number,
  passage: string,
  // P4 (Stage 5 AC): required when the source is past its review date.
  outdatedAcknowledgement?: string
): Promise<SourceEvidenceLink> {
  return request(`/api/assessments/${assessmentId}/risk-factors/${riskFactorId}/source-evidence`, {
    method: "POST",
    body: JSON.stringify({
      source_id: sourceId,
      passage,
      acknowledge_outdated: Boolean(outdatedAcknowledgement),
      acknowledgement_reason: outdatedAcknowledgement ?? null,
    }),
  });
}
