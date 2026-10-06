import type { VersionStatus } from "../api/sourceLibrary";

export const STATUS_LABEL: Record<VersionStatus, string> = {
  DRAFT: "Draft",
  IN_REVIEW: "In review",
  APPROVED: "Approved",
  REJECTED: "Rejected",
  SUPERSEDED: "Superseded",
  RETIRED: "Retired",
};

export const ACTION_LABEL: Record<string, string> = {
  SUBMITTED: "Submitted for review",
  WITHDRAWN: "Withdrawn from review",
  APPROVED: "Approved",
  REJECTED: "Rejected",
  REOPENED: "Reopened as draft",
  SUPERSEDED: "Superseded",
  RETIRED: "Retired",
};

export const EVENT_LABEL: Record<string, string> = {
  RECORD_CREATED: "Source created",
  RECORD_UPDATED: "Source details changed",
  RECORD_RETIRED: "Source retired",
  VERSION_CREATED: "Version created",
  VERSION_UPDATED: "Version edited",
  FILE_UPLOADED: "Document uploaded",
  FILE_DOWNLOADED: "Document downloaded",
  UPLOAD_REJECTED: "Upload rejected",
  REPROCESSED: "Document reprocessed",
  SUBMITTED: "Submitted for review",
  WITHDRAWN: "Withdrawn from review",
  APPROVED: "Approved",
  REJECTED: "Rejected",
  SUPERSEDED: "Superseded",
  RETIRED: "Retired",
  ACTION_DENIED: "Action refused",
  MIGRATED: "Migrated from the earlier library",
};

export function formatSize(bytes: number | null | undefined): string {
  if (!bytes) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value.length <= 10 ? `${value}T00:00:00` : value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  // The API sends UTC timestamps without a zone marker.
  const date = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(value) ? value : `${value}Z`);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export const PROCESSING_LABEL: Record<string, string> = {
  NONE: "Link only",
  PENDING: "Processing",
  PROCESSED: "Text extracted",
  FAILED: "Extraction failed",
};

export const EMBEDDING_LABEL: Record<string, string> = {
  NONE: "Keyword search only",
  COMPLETE: "Semantic search ready",
  PARTIAL: "Partly embedded",
  UNAVAILABLE: "Embeddings unavailable",
};

export const SCAN_LABEL: Record<string, string> = {
  NOT_SCANNED: "Not scanned",
  CLEAN: "Malware scan passed",
  INFECTED: "Threat detected",
  ERROR: "Scan failed",
};
