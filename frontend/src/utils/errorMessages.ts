/*
 * Stage 19 (accessibility / usability): turn raw technical errors into
 * plain-language messages that say what happened and what to do next.
 *
 * The API helpers in src/api/* throw plain `Error`s whose message is
 * either the backend's `detail` text, a generic "Failed to ..." string,
 * or a string that embeds the HTTP status, e.g.
 * "Failed to advance stage (409): <detail>". A browser-level network
 * failure surfaces as a `TypeError("Failed to fetch")`. friendlyError()
 * normalises all of those into something a business user can act on.
 */

const NETWORK_MESSAGE =
  "We couldn't reach the server. Check your internet connection (or VPN) and try again.";
const SESSION_EXPIRED_MESSAGE =
  "Your session has expired — please sign in again.";
const FORBIDDEN_MESSAGE = "You don't have permission to do this.";
const NOT_FOUND_MESSAGE =
  "We couldn't find that item. It may have been removed — refresh the page and try again.";
const CONFLICT_MESSAGE =
  "This record was changed by someone else or is no longer in a state that allows this action. Refresh the page and try again.";
const TOO_LARGE_MESSAGE =
  "The file is too large to upload. Try a smaller file.";
const SERVER_MESSAGE =
  "The server hit a problem. Your data has been kept — please try again.";

function extractStatus(err: unknown, message: string): number | null {
  if (err && typeof err === "object") {
    const candidate =
      (err as { status?: unknown }).status ??
      (err as { response?: { status?: unknown } }).response?.status;
    if (typeof candidate === "number") {
      return candidate;
    }
  }

  // "Failed to advance stage (409): ..." / "HTTP 500" / "status 403"
  const match =
    message.match(/\((\d{3})\)/) ??
    message.match(/\b(?:HTTP|status)\s*:?\s*(\d{3})\b/i);
  return match ? Number(match[1]) : null;
}

// The backend detail following "(<status>): " when present.
function extractDetail(message: string): string {
  const match = message.match(/\(\d{3}\)\s*:?\s*(.*)$/s);
  return (match ? match[1] : message).trim();
}

function isGenericMessage(message: string): boolean {
  // e.g. "Failed to fetch assessments" / "Failed to update user" with no
  // extra detail -- not useful to show as-is.
  return /^(failed|unable) to [a-z\s-]+\.?$/i.test(message.trim());
}

function isNetworkFailure(err: unknown, message: string): boolean {
  if (err instanceof TypeError) {
    return true;
  }
  return /failed to fetch|networkerror|network request failed|load failed|err_connection/i.test(
    message
  );
}

export function friendlyError(err: unknown, fallback: string): string {
  const message =
    err instanceof Error
      ? err.message
      : typeof err === "string"
        ? err
        : "";

  if (isNetworkFailure(err, message)) {
    return NETWORK_MESSAGE;
  }

  const status = extractStatus(err, message);
  const detail = extractDetail(message);
  const usefulDetail = detail && !isGenericMessage(detail) ? detail : "";

  if (
    status === 401 ||
    /not authenticated|could not validate credentials|token (has )?expired/i.test(message)
  ) {
    return SESSION_EXPIRED_MESSAGE;
  }

  if (status === 403 || /^forbidden$/i.test(detail)) {
    return usefulDetail && !/^forbidden$/i.test(usefulDetail)
      ? `${FORBIDDEN_MESSAGE} ${usefulDetail}`
      : FORBIDDEN_MESSAGE;
  }

  if (status === 404) {
    return NOT_FOUND_MESSAGE;
  }

  if (status === 409) {
    return usefulDetail ? `${usefulDetail} Refresh the page and try again.` : CONFLICT_MESSAGE;
  }

  if (status === 413) {
    return TOO_LARGE_MESSAGE;
  }

  if (status === 400 || status === 422) {
    // Validation errors: the backend detail explains which field is wrong.
    return usefulDetail || `${fallback} Please check the highlighted fields and try again.`;
  }

  if (status !== null && status >= 500) {
    return SERVER_MESSAGE;
  }

  // No status: keep a descriptive backend message, otherwise fall back.
  if (usefulDetail) {
    return usefulDetail;
  }

  return fallback;
}
