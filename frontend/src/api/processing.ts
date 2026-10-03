import { authFetch } from "./http";
import { API_BASE_URL } from "./config";


// Stage 19 (Performance / Reliability): background processing jobs --
// document text extraction after upload, and risk analysis started with
// analyzeAssessmentAsync. The UI polls these for progress instead of
// waiting on one long request.
export type ProcessingJobStatus =
  | "QUEUED"
  | "RUNNING"
  | "SUCCEEDED"
  | "PARTIAL"
  | "FAILED";

export interface ProcessingJob {
  id: number;
  job_type: "DOCUMENT_EXTRACTION" | "RISK_ANALYSIS" | "STAGE_ADVANCE" | "RISK_IDENTIFICATION" | string;
  status: ProcessingJobStatus;
  assessment_id: number | null;
  document_id: number | null;
  progress: number;
  stage_message: string | null;
  // Plain-language message for the user; error_detail is technical.
  error_message: string | null;
  error_detail: string | null;
  // Finished, but part of the output is missing -- never shown as complete.
  is_incomplete: boolean;
  incomplete_reasons: string[];
  attempts: number;
  max_attempts: number;
  is_retryable: boolean;
  is_finished: boolean;
  requested_by: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  // STAGE_ADVANCE jobs: the stage being left, and every step of the move.
  from_status?: string | null;
  stages?: ProcessingStage[];
  // FAILED because a stage gate refused the move (not because it broke).
  refused?: boolean;
}

export interface ProcessingStage {
  key: string;
  label: string;
  status: "queued" | "running" | "completed" | "warning" | "skipped" | "failed";
  started_at: string | null;
  finished_at: string | null;
}

async function readErrorDetail(response: Response): Promise<string> {
  try {
    const parsed = await response.json();
    if (typeof parsed?.detail === "string") return parsed.detail;
    // A structured refusal, e.g. P4's {code, message, documents}.
    return typeof parsed?.detail?.message === "string" ? parsed.detail.message : "";
  } catch {
    return "";
  }
}

export async function getProcessingJob(jobId: number): Promise<ProcessingJob> {
  const response = await authFetch(`${API_BASE_URL}/api/processing-jobs/${jobId}`);

  if (!response.ok) {
    throw new Error(
      (await readErrorDetail(response)) || "Couldn't check the processing status."
    );
  }

  return response.json();
}

export async function listProcessingJobs(
  assessmentId: number
): Promise<ProcessingJob[]> {
  const response = await authFetch(
    `${API_BASE_URL}/api/processing-jobs?assessment_id=${assessmentId}`
  );

  if (!response.ok) {
    throw new Error(
      (await readErrorDetail(response)) || "Couldn't load processing status."
    );
  }

  return response.json();
}

export async function retryProcessingJob(jobId: number): Promise<ProcessingJob> {
  const response = await authFetch(
    `${API_BASE_URL}/api/processing-jobs/${jobId}/retry`,
    { method: "POST" }
  );

  if (!response.ok) {
    throw new Error(
      (await readErrorDetail(response)) || "The retry couldn't be started."
    );
  }

  return response.json();
}

export async function analyzeAssessmentAsync(
  assessmentId: number
): Promise<ProcessingJob> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/analyze-async`,
    { method: "POST" }
  );

  if (!response.ok) {
    throw new Error(
      (await readErrorDetail(response)) || "Risk analysis couldn't be started."
    );
  }

  return response.json();
}

// PATCH /advance-stage as a background job with per-step progress (POST
// /advance-stage-async). Refusals the API can check up front (permission,
// an unconfirmed business profile) are thrown straight away; a stage gate
// hit during the move finishes the job FAILED with `refused` set.
export async function startStageAdvance(
  assessmentId: number
): Promise<ProcessingJob> {
  const response = await authFetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/advance-stage-async`,
    { method: "POST" }
  );

  if (!response.ok) {
    throw new Error(
      (await readErrorDetail(response)) || "The stage move couldn't be started."
    );
  }

  return response.json();
}

// Polls a job until it finishes, reporting each update. Resolves with the
// final job; never rejects on a FAILED job (that's a normal outcome the
// caller displays) -- only on being unable to reach the server.
export async function waitForProcessingJob(
  jobId: number,
  onUpdate: (job: ProcessingJob) => void,
  { intervalMs = 1000, signal }: { intervalMs?: number; signal?: AbortSignal } = {}
): Promise<ProcessingJob> {
  for (;;) {
    const job = await getProcessingJob(jobId);
    onUpdate(job);

    if (job.is_finished || signal?.aborted) {
      return job;
    }

    await new Promise((resolve) => window.setTimeout(resolve, intervalMs));
  }
}
