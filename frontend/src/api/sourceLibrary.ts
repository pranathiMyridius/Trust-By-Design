import { API_BASE_URL } from "./config";
import { authFetch } from "./http";

// Source Library: governed regulatory and internal-policy sources, their
// versions, review workflow and audit trail (backend: /api/source-library).

export type VersionStatus = "DRAFT" | "IN_REVIEW" | "APPROVED" | "REJECTED" | "SUPERSEDED" | "RETIRED";
export type LibraryStatus = VersionStatus;

export interface SourceVersion {
  id: string;
  version_number: number;
  version_label: string;
  status: VersionStatus;
  effective_date: string | null;
  review_date: string | null;
  retrieved_date: string | null;
  source_url: string | null;
  change_summary: string | null;
  has_file: boolean;
  original_filename: string | null;
  file_size: number | null;
  file_sha256: string | null;
  page_count: number | null;
  processing_status: "NONE" | "PENDING" | "PROCESSED" | "FAILED";
  processing_error: string | null;
  scan_status: "NOT_SCANNED" | "CLEAN" | "INFECTED" | "ERROR";
  scan_detail: string | null;
  chunk_count: number;
  embedding_status: "NONE" | "COMPLETE" | "PARTIAL" | "UNAVAILABLE";
  outdated: boolean;
  created_by: string | null;
  created_at: string;
  submitted_by: string | null;
  submitted_at: string | null;
  decided_by: string | null;
  decided_at: string | null;
  decision_comment: string | null;
  superseded_at: string | null;
  migrated: boolean;
}

export interface SourceRecord {
  id: string;
  source_code: string;
  title: string;
  authority: string;
  category: string;
  category_label: string;
  jurisdiction: string | null;
  applicable_entity: string | null;
  source_url: string | null;
  description: string | null;
  topics: string[];
  owner: string | null;
  review_frequency: string | null;
  status: "ACTIVE" | "RETIRED";
  library_status: LibraryStatus;
  outdated: boolean;
  version_count: number;
  current_version: SourceVersion | null;
  pending_version: SourceVersion | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface SourceRecordDetail extends SourceRecord {
  versions: SourceVersion[];
}

export interface RecordPage {
  items: SourceRecord[];
  total: number;
  page: number;
  page_size: number;
  summary: { total: number; approved: number; in_review: number; draft: number; retired: number; outdated: number };
  jurisdictions: string[];
  authorities: string[];
}

export interface ApprovalEntry {
  id: string;
  version_id: string;
  version_label: string | null;
  version_number: number | null;
  action: string;
  from_status: string | null;
  to_status: string;
  actor: string;
  comment: string | null;
  created_at: string;
}

export interface AuditEntry {
  id: string;
  version_id: string | null;
  version_label: string | null;
  event_type: string;
  actor: string;
  details: string | null;
  event_data: Record<string, unknown> | null;
  created_at: string;
}

export interface AuditPage {
  items: AuditEntry[];
  total: number;
  page: number;
  page_size: number;
}

export interface Chunk {
  id: string;
  chunk_index: number;
  page_start: number | null;
  page_end: number | null;
  section: string | null;
  text: string;
}

export interface ChunkPage {
  items: Chunk[];
  total: number;
  page: number;
  page_size: number;
}

export interface Passage {
  chunk_id: string;
  record_id: string;
  version_id: string;
  source_code: string;
  title: string;
  authority: string;
  category: string;
  jurisdiction: string | null;
  version_label: string;
  effective_date: string | null;
  review_date: string | null;
  outdated: boolean;
  source_url: string | null;
  page_start: number | null;
  page_end: number | null;
  section: string | null;
  location: string;
  score: number;
  method: string;
  text: string;
}

export interface LibraryMeta {
  categories: { value: string; label: string }[];
  version_statuses: VersionStatus[];
  max_upload_mb: number;
  accepted_types: string[];
  home_jurisdiction: string | null;
  min_comment_length: number;
  reviewer_rule: string;
  policy_status: string;
  malware_scan_required: boolean;
  antivirus_configured: boolean;
  can_maintain: boolean;
  can_review: boolean;
  can_view_all: boolean;
  user_id: number;
}

export interface RecordFilters {
  q?: string;
  status?: string;
  category?: string;
  jurisdiction?: string;
  authority?: string;
  topic?: string;
  sort?: "title" | "updated" | "code";
  page?: number;
  page_size?: number;
}

export interface RecordFields {
  source_code?: string;
  title: string;
  authority: string;
  category: string;
  jurisdiction: string;
  applicable_entity: string;
  source_url: string;
  description: string;
  topics: string;
  owner: string;
  review_frequency: string;
}

export interface VersionFields {
  version_label: string;
  effective_date: string;
  review_date: string;
  retrieved_date: string;
  change_summary: string;
  source_url: string;
}

const BASE = `${API_BASE_URL}/api/source-library`;

/** A request error that keeps the backend's stable code (e.g. SEPARATION_OF_DUTIES). */
export class LibraryError extends Error {
  code: string | null;
  status: number;
  constructor(message: string, status: number, code: string | null) {
    super(message);
    this.name = "LibraryError";
    this.status = status;
    this.code = code;
  }
}

async function failure(response: Response, fallback: string): Promise<LibraryError> {
  let message = "";
  let code: string | null = null;
  try {
    const body = await response.json();
    const detail = body?.detail;
    if (typeof detail === "string") message = detail;
    else if (detail && typeof detail === "object" && !Array.isArray(detail)) {
      message = typeof detail.message === "string" ? detail.message : "";
      code = typeof detail.code === "string" ? detail.code : null;
    } else if (Array.isArray(detail)) {
      message = detail.map((item: { msg?: string }) => item.msg).filter(Boolean).join(" ");
    }
  } catch {
    // not JSON
  }
  return new LibraryError(message || `${fallback} (${response.status})`, response.status, code);
}

async function request<T>(path: string, init?: RequestInit, fallback = "The request failed"): Promise<T> {
  const response = await authFetch(`${BASE}${path}`, {
    ...init,
    headers: { Accept: "application/json", ...(init?.body && !(init.body instanceof FormData) ? { "Content-Type": "application/json" } : {}), ...(init?.headers ?? {}) },
  });
  if (!response.ok) throw await failure(response, fallback);
  return response.json();
}

function query(params: Record<string, string | number | undefined | null>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && String(value) !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export const getMeta = () => request<LibraryMeta>("/meta", undefined, "The library settings couldn't be loaded");

export const listRecords = (filters: RecordFilters) =>
  request<RecordPage>(`/records${query({ ...filters })}`, undefined, "The library couldn't be loaded");

export const getRecord = (id: string) => request<SourceRecordDetail>(`/records/${id}`, undefined, "The source couldn't be loaded");

function formFrom(values: Record<string, string | undefined>, file?: File | null): FormData {
  const body = new FormData();
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined && value !== "") body.append(key, value);
  }
  if (file) body.append("file", file);
  return body;
}

export function createRecord(record: RecordFields, version: VersionFields, file: File | null): Promise<SourceRecordDetail> {
  const { source_url: versionUrl, ...versionRest } = version;
  const body = formFrom(
    {
      ...record,
      ...versionRest,
      version_source_url: versionUrl,
    },
    file,
  );
  return request("/records", { method: "POST", body }, "The source couldn't be saved");
}

export function updateRecord(
  id: string,
  changes: Partial<Omit<RecordFields, "topics">> & { topics?: string[]; change_reason?: string },
): Promise<SourceRecordDetail> {
  return request(`/records/${id}`, { method: "PATCH", body: JSON.stringify(changes) }, "The changes couldn't be saved");
}

export function addVersion(id: string, version: VersionFields, file: File | null): Promise<SourceRecordDetail> {
  return request(`/records/${id}/versions`, { method: "POST", body: formFrom({ ...version }, file) }, "The version couldn't be added");
}

export function updateVersion(id: string, versionId: string, changes: Partial<VersionFields>): Promise<SourceVersion> {
  const body: Record<string, string | null> = {};
  for (const [key, value] of Object.entries(changes)) body[key] = value === "" ? null : (value as string);
  return request(`/records/${id}/versions/${versionId}`, { method: "PATCH", body: JSON.stringify(body) }, "The version couldn't be saved");
}

export function replaceFile(id: string, versionId: string, file: File): Promise<SourceVersion> {
  return request(`/records/${id}/versions/${versionId}/file`, { method: "POST", body: formFrom({}, file) }, "The document couldn't be uploaded");
}

export function reprocessVersion(id: string, versionId: string): Promise<SourceVersion> {
  return request(`/records/${id}/versions/${versionId}/reprocess`, { method: "POST" }, "The document couldn't be reprocessed");
}

export type VersionAction = "submit" | "withdraw" | "approve" | "reject" | "retire";

export function actOnVersion(id: string, versionId: string, action: VersionAction, note = ""): Promise<SourceRecordDetail> {
  const body = action === "retire" ? { reason: note } : { comment: note };
  return request(`/records/${id}/versions/${versionId}/${action}`, { method: "POST", body: JSON.stringify(body) }, `The ${action} failed`);
}

export const retireRecord = (id: string, reason: string) =>
  request<SourceRecordDetail>(`/records/${id}/retire`, { method: "POST", body: JSON.stringify({ reason }) }, "The source couldn't be retired");

export const getApprovals = (id: string) => request<ApprovalEntry[]>(`/records/${id}/approvals`, undefined, "The approval history couldn't be loaded");

export const getAudit = (id: string, page: number, pageSize = 25, eventType?: string) =>
  request<AuditPage>(`/records/${id}/audit${query({ page, page_size: pageSize, event_type: eventType })}`, undefined, "The audit timeline couldn't be loaded");

export const getChunks = (id: string, versionId: string, page: number, pageSize = 10) =>
  request<ChunkPage>(`/records/${id}/versions/${versionId}/chunks${query({ page, page_size: pageSize })}`, undefined, "The extracted text couldn't be loaded");

export const searchPassages = (q: string, jurisdictions?: string, category?: string) =>
  request<Passage[]>(`/passages${query({ q, jurisdictions, category, limit: 10 })}`, undefined, "The search failed");

export const seedCatalog = () =>
  request<{ created: number; existing: number; source_codes: string[] }>("/catalog/seed", { method: "POST" }, "The starter catalogue couldn't be loaded");

/** Downloads a version's PDF (the download is audited). */
export async function downloadVersionFile(id: string, versionId: string, filename: string): Promise<void> {
  const response = await authFetch(`${BASE}/records/${id}/versions/${versionId}/file`);
  if (!response.ok) throw await failure(response, "The download failed");
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
