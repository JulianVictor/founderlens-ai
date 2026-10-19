/**
 * Presentation helpers for documents.
 *
 * Pure functions, separated from the components that use them so the
 * formatting rules can be tested directly rather than through rendered markup.
 */

export type DocumentStatus = "uploaded" | "processing" | "ready" | "failed";

/** Human label and tone for each status the API can return. */
export const STATUS_LABELS: Record<DocumentStatus, string> = {
  uploaded: "Uploaded",
  processing: "Processing",
  ready: "Ready",
  failed: "Failed",
};

/**
 * Label for a status value.
 *
 * Falls back to the raw value rather than throwing: the API adding a status the
 * frontend has not shipped support for yet should degrade to showing that
 * status, not to a blank cell or a crash.
 */
export function statusLabel(status: string): string {
  return STATUS_LABELS[status as DocumentStatus] ?? status;
}

/** Statuses from which starting (or re-starting) processing makes sense. */
const PROCESSABLE: ReadonlySet<string> = new Set(["uploaded", "failed"]);

/**
 * Whether to offer a process action for a document.
 *
 * Offered for `failed` as well as `uploaded`, because a failure is often
 * transient -- storage was briefly unreachable -- and the backend treats a
 * retry as a fresh attempt rather than refusing one.
 */
export function canProcess(status: string): boolean {
  return PROCESSABLE.has(status);
}

/**
 * Whether any document is mid-flight, and the list is therefore stale.
 *
 * Processing happens after the response, so the only way a client learns it
 * finished is by asking again. Polling stops as soon as nothing is running,
 * rather than running on a timer forever.
 */
export function hasWorkInFlight(
  documents: readonly { readonly status: string }[],
): boolean {
  return documents.some((document) => document.status === "processing");
}

/**
 * Size in units a person reads, using binary multiples to match how the limit
 * is expressed (25 MiB).
 */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "—";
  if (bytes < 1024) return `${bytes} B`;

  const units = ["KB", "MB", "GB"] as const;
  let value = bytes / 1024;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  // One decimal below 10 so "1.2 MB" stays informative, none above so "245 KB"
  // does not pretend to a precision nobody needs.
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unitIndex]}`;
}

/**
 * Upload time as an elapsed interval, falling back to a date once that stops
 * being useful.
 *
 * `now` is injectable so the behaviour is testable without freezing the clock.
 */
export function formatUploadedAt(iso: string, now: Date = new Date()): string {
  const uploaded = new Date(iso);
  if (Number.isNaN(uploaded.getTime())) return "—";

  const seconds = Math.round((now.getTime() - uploaded.getTime()) / 1000);

  // A clock skewed slightly ahead of the server must not render "in 3 seconds".
  if (seconds < 60) return "just now";
  if (seconds < 3600) {
    const minutes = Math.floor(seconds / 60);
    return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
  }
  if (seconds < 86400) {
    const hours = Math.floor(seconds / 3600);
    return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  }
  if (seconds < 604800) {
    const days = Math.floor(seconds / 86400);
    return `${days} day${days === 1 ? "" : "s"} ago`;
  }
  return uploaded.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

/**
 * Turn an API failure into something a person can act on.
 *
 * The server's own `detail` is preferred where it is useful, but the common
 * rejections get a message written for the person uploading rather than for
 * the developer reading a log.
 */
export function uploadErrorMessage(status: number, detail?: string): string {
  switch (status) {
    case 400:
      return "That file is empty.";
    case 401:
      return "Your session has expired. Sign in again.";
    case 404:
      return "This workspace is no longer available to you.";
    case 409:
      return "That document is already in this workspace.";
    case 413:
      return detail ?? "That file is too large.";
    case 415:
      return detail ?? "Only PDF, plain text and Markdown files are accepted.";
    case 502:
      return "Document storage is unavailable. Please try again.";
    default:
      return detail ?? "Upload failed. Please try again.";
  }
}
