import { useEffect, useRef, useState } from "react";
import {
  getProcessingJob,
  retryProcessingJob,
  type ProcessingJob,
} from "../api/processing";
import "./NonFunctional.css";

/*
 * Stage 19 (Performance / Reliability): shows a background job's state --
 * progress while it runs (without blocking anything else on the page), a
 * plain-language error with a Retry action if it failed, and a clear
 * "incomplete" marker with reasons if it finished with partial results.
 * Polls on its own while the job is unfinished. Render it with
 * key={job.id} so a new job starts from fresh state.
 */

const STATUS_TEXT: Record<string, { icon: string; label: string }> = {
  QUEUED: { icon: "⏳", label: "Waiting to start" },
  RUNNING: { icon: "⟳", label: "Processing" },
  SUCCEEDED: { icon: "✓", label: "Processed" },
  PARTIAL: { icon: "⚠", label: "Processed — incomplete" },
  FAILED: { icon: "✕", label: "Processing failed" },
};

export default function ProcessingStatus({
  job: initialJob,
  subject = "document",
  onFinished,
  compact = false,
}: {
  job: ProcessingJob;
  subject?: string;
  onFinished?: (job: ProcessingJob) => void;
  compact?: boolean;
}) {
  const [job, setJob] = useState<ProcessingJob>(initialJob);
  const [retrying, setRetrying] = useState(false);
  const [pollError, setPollError] = useState("");
  const [showDetail, setShowDetail] = useState(false);
  // Bumped after a failed poll so the effect below schedules another try.
  const [pollAttempt, setPollAttempt] = useState(0);
  const onFinishedRef = useRef(onFinished);

  useEffect(() => {
    onFinishedRef.current = onFinished;
  });

  useEffect(() => {
    if (job.is_finished) {
      return;
    }

    let cancelled = false;
    const timer = window.setTimeout(async () => {
      try {
        const next = await getProcessingJob(job.id);
        if (cancelled) return;
        setPollError("");
        setJob(next);
        if (next.is_finished) {
          onFinishedRef.current?.(next);
        }
      } catch {
        if (!cancelled) {
          setPollError(
            "Can't reach the server to check progress. Processing continues in the background; we'll keep trying."
          );
          setPollAttempt((attempt) => attempt + 1);
        }
      }
    }, 1200);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [job, pollAttempt]);

  async function handleRetry() {
    setRetrying(true);
    setPollError("");
    try {
      setJob(await retryProcessingJob(job.id));
    } catch (err) {
      setPollError(err instanceof Error ? err.message : "The retry couldn't be started.");
    } finally {
      setRetrying(false);
    }
  }

  const status = STATUS_TEXT[job.status] ?? { icon: "•", label: job.status };
  const running = !job.is_finished;

  if (compact && job.status === "SUCCEEDED") {
    return null;
  }

  return (
    <div
      className={`processing-status processing-status--${job.status.toLowerCase()}`}
      role={job.status === "FAILED" ? "alert" : "status"}
      aria-live="polite"
    >
      <div className="processing-status__header">
        <span className="processing-status__icon" aria-hidden="true">
          {status.icon}
        </span>
        <strong>{status.label}</strong>
        {running && job.stage_message && (
          <span className="processing-status__step"> — {job.stage_message}</span>
        )}
      </div>

      {running && (
        <div
          className="processing-status__bar"
          role="progressbar"
          aria-label={`${subject} processing progress`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={job.progress}
          aria-valuetext={`${job.progress}% — ${job.stage_message ?? status.label}`}
        >
          <span style={{ width: `${Math.max(4, job.progress)}%` }} />
        </div>
      )}

      {running && (
        <p className="processing-status__hint">
          You can keep working — this runs in the background.
        </p>
      )}

      {job.status === "PARTIAL" && job.incomplete_reasons.length > 0 && (
        <div className="processing-status__incomplete">
          <p>
            <strong>Results are incomplete.</strong> Treat this {subject} as partially
            processed:
          </p>
          <ul>
            {job.incomplete_reasons.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </div>
      )}

      {job.status === "FAILED" && (
        <div className="processing-status__error">
          <p>{job.error_message ?? "Processing failed."}</p>
          {job.error_detail && (
            <>
              <button
                type="button"
                className="link-button"
                aria-expanded={showDetail}
                onClick={() => setShowDetail((value) => !value)}
              >
                {showDetail ? "Hide technical details" : "Show technical details"}
              </button>
              {showDetail && <pre className="processing-status__detail">{job.error_detail}</pre>}
            </>
          )}
        </div>
      )}

      {job.is_retryable && (
        <button
          type="button"
          className="secondary-button"
          onClick={handleRetry}
          disabled={retrying}
        >
          {retrying ? "Retrying…" : `Retry (attempt ${job.attempts + 1} of ${job.max_attempts})`}
        </button>
      )}

      {job.is_finished && !job.is_retryable && job.status === "FAILED" && (
        <p className="processing-status__hint">
          The maximum number of attempts has been reached. Upload a different version of
          the file, or contact support.
        </p>
      )}

      {pollError && <p className="processing-status__poll-error">{pollError}</p>}
    </div>
  );
}

/*
 * Stage 19: a document's processing state as a badge -- replaces the old
 * hard-coded "PARSED", which claimed success even when extraction had
 * failed or found nothing. Text + symbol, not colour alone.
 */
export function DocumentProcessingBadge({ job }: { job?: ProcessingJob | null }) {
  if (!job || job.status === "SUCCEEDED") {
    return (
      <span className="parsed-badge">
        <span aria-hidden="true">✓ </span>PARSED
      </span>
    );
  }

  if (!job.is_finished) {
    return (
      <span className="parsed-badge processing-badge--running">
        <span aria-hidden="true">⟳ </span>PROCESSING {job.progress}%
      </span>
    );
  }

  if (job.status === "PARTIAL") {
    return (
      <span className="parsed-badge processing-badge--partial" title="Processed with incomplete results">
        <span aria-hidden="true">⚠ </span>INCOMPLETE
      </span>
    );
  }

  return (
    <span className="parsed-badge processing-badge--failed">
      <span aria-hidden="true">✕ </span>NOT PARSED
    </span>
  );
}
