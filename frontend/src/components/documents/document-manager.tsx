"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  type ApiDocument,
  deleteDocument,
  listDocuments,
  processDocument,
  uploadDocument,
} from "@/lib/api/documents";
import {
  canProcess,
  formatBytes,
  formatUploadedAt,
  hasWorkInFlight,
  statusLabel,
  uploadErrorMessage,
} from "@/lib/documents/format";
import { createClient } from "@/lib/supabase/client";

type Props = {
  workspaceId: string;
};

type LoadState =
  | { kind: "loading" }
  | { kind: "loaded"; documents: ApiDocument[] }
  | { kind: "error"; message: string };

const ACCEPTED = ".pdf,.txt,.md,.markdown,application/pdf,text/plain,text/markdown";

/** How often to re-check while a document is processing. */
const POLL_INTERVAL_MS = 2000;

export function DocumentManager({ workspaceId }: Props) {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [processingId, setProcessingId] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  /** Current access token, or null if the session has gone. */
  const accessToken = useCallback(async (): Promise<string | null> => {
    const supabase = createClient();
    const { data } = await supabase.auth.getSession();
    return data.session?.access_token ?? null;
  }, []);

  const refresh = useCallback(
    async (signal?: AbortSignal) => {
      const token = await accessToken();
      if (!token) {
        setState({ kind: "error", message: "Your session has expired. Sign in again." });
        return;
      }
      try {
        const documents = await listDocuments(workspaceId, token, signal);
        setState({ kind: "loaded", documents });
      } catch (error) {
        if (signal?.aborted) return;
        const message =
          error instanceof ApiError && error.status === 404
            ? "This workspace is no longer available to you."
            : "Could not load documents. Please try again.";
        setState({ kind: "error", message });
      }
    },
    [accessToken, workspaceId],
  );

  useEffect(() => {
    // Aborted on unmount so a slow response cannot set state on a gone
    // component, and so a workspace switch does not render the previous one's
    // documents when its request happens to land second.
    const controller = new AbortController();
    const load = async () => {
      await refresh(controller.signal);
    };
    void load();
    return () => controller.abort();
  }, [refresh]);

  const inFlight = state.kind === "loaded" && hasWorkInFlight(state.documents);

  useEffect(() => {
    // Processing happens after the response, so the only way to learn it
    // finished is to ask again. The interval is torn down as soon as nothing
    // is running, rather than polling on a timer forever.
    if (!inFlight) return;
    const timer = setInterval(() => {
      void refresh();
    }, POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [inFlight, refresh]);

  async function handleUpload(file: File) {
    setUploadError(null);
    setIsUploading(true);
    try {
      const token = await accessToken();
      if (!token) {
        setUploadError("Your session has expired. Sign in again.");
        return;
      }
      const created = await uploadDocument(workspaceId, token, file);
      setState((current) =>
        current.kind === "loaded"
          ? { kind: "loaded", documents: [created, ...current.documents] }
          : current,
      );
    } catch (error) {
      setUploadError(
        error instanceof ApiError
          ? uploadErrorMessage(error.status, error.detail)
          : "Upload failed. Please check your connection and try again.",
      );
    } finally {
      setIsUploading(false);
      // Cleared so that selecting the same file again still fires a change
      // event -- otherwise a retry after a failure silently does nothing.
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  async function handleDelete(document: ApiDocument) {
    setDeletingId(document.id);
    setUploadError(null);
    try {
      const token = await accessToken();
      if (!token) {
        setUploadError("Your session has expired. Sign in again.");
        return;
      }
      await deleteDocument(workspaceId, token, document.id);
      setState((current) =>
        current.kind === "loaded"
          ? {
              kind: "loaded",
              documents: current.documents.filter((d) => d.id !== document.id),
            }
          : current,
      );
    } catch (error) {
      setUploadError(
        error instanceof ApiError && error.status === 404
          ? "That document has already been deleted."
          : "Could not delete that document. Please try again.",
      );
    } finally {
      setDeletingId(null);
    }
  }

  async function handleProcess(document: ApiDocument) {
    setProcessingId(document.id);
    setUploadError(null);
    try {
      const token = await accessToken();
      if (!token) {
        setUploadError("Your session has expired. Sign in again.");
        return;
      }
      await processDocument(workspaceId, token, document.id);
      // Refreshed rather than patched locally: the server decides what state
      // the document is in, and a job that failed immediately would otherwise
      // be shown as processing until the next poll.
      await refresh();
    } catch (error) {
      setUploadError(
        error instanceof ApiError && error.status === 404
          ? "That document is no longer available."
          : "Could not start processing. Please try again.",
      );
    } finally {
      setProcessingId(null);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h2 className="text-sm font-semibold tracking-tight">Documents</h2>
          <p className="text-xs text-black/50 dark:text-white/50">
            PDF, plain text or Markdown, up to 25 MB. Files are checked by content,
            not by their extension.
          </p>
        </div>

        <label className="cursor-pointer rounded-md bg-foreground px-4 py-2 text-sm font-medium text-background transition-opacity hover:opacity-90">
          {isUploading ? "Uploading…" : "Upload document"}
          <input
            ref={fileInput}
            type="file"
            accept={ACCEPTED}
            disabled={isUploading}
            className="sr-only"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void handleUpload(file);
            }}
          />
        </label>
      </div>

      {uploadError ? (
        <p
          role="alert"
          className="rounded-md border border-red-500/30 bg-red-500/5 px-3 py-2 text-sm text-red-600 dark:text-red-400"
        >
          {uploadError}
        </p>
      ) : null}

      {state.kind === "loading" ? (
        <ul
          aria-busy="true"
          aria-label="Loading documents"
          className="flex flex-col gap-px overflow-hidden rounded-lg border border-black/10 bg-black/10 dark:border-white/10 dark:bg-white/10"
        >
          {[0, 1, 2].map((row) => (
            <li key={row} className="flex items-center gap-4 bg-[var(--background)] px-5 py-4">
              <span className="h-4 w-48 animate-pulse rounded bg-black/10 dark:bg-white/10" />
              <span className="ml-auto h-4 w-20 animate-pulse rounded bg-black/10 dark:bg-white/10" />
            </li>
          ))}
        </ul>
      ) : state.kind === "error" ? (
        <div className="rounded-lg border border-red-500/30 bg-red-500/5 p-6 text-sm">
          <p role="alert" className="text-red-600 dark:text-red-400">
            {state.message}
          </p>
          <button
            type="button"
            onClick={() => {
              setState({ kind: "loading" });
              void refresh();
            }}
            className="mt-3 rounded-md border border-black/15 px-3 py-1.5 text-sm font-medium dark:border-white/15"
          >
            Try again
          </button>
        </div>
      ) : state.documents.length === 0 ? (
        <div className="rounded-lg border border-dashed border-black/15 p-10 text-center dark:border-white/15">
          <p className="text-sm font-medium">No documents yet</p>
          <p className="mx-auto mt-1 max-w-sm text-sm text-black/50 dark:text-white/50">
            Upload a pitch deck, a filing or a set of notes. Once ingestion lands,
            everything here becomes searchable and citable.
          </p>
        </div>
      ) : (
        <ul className="flex flex-col gap-px overflow-hidden rounded-lg border border-black/10 bg-black/10 dark:border-white/10 dark:bg-white/10">
          {state.documents.map((document) => (
            <li
              key={document.id}
              className="flex flex-wrap items-center gap-x-4 gap-y-2 bg-[var(--background)] px-5 py-4"
            >
              <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                <span className="truncate text-sm font-medium">{document.filename}</span>
                <span className="text-xs text-black/50 dark:text-white/50">
                  {formatBytes(document.size_bytes)} ·{" "}
                  <time dateTime={document.created_at}>
                    {formatUploadedAt(document.created_at)}
                  </time>
                </span>
                {document.error_message ? (
                  <span className="text-xs text-red-600 dark:text-red-400">
                    {document.error_message}
                  </span>
                ) : null}
              </div>

              <span
                aria-live={document.status === "processing" ? "polite" : undefined}
                className={`font-mono text-xs uppercase tracking-wider ${
                  document.status === "failed"
                    ? "text-red-600 dark:text-red-400"
                    : document.status === "ready"
                      ? "text-emerald-700 dark:text-emerald-400"
                      : "text-black/40 dark:text-white/40"
                } ${document.status === "processing" ? "animate-pulse" : ""}`}
              >
                {statusLabel(document.status)}
              </span>

              {canProcess(document.status) ? (
                <button
                  type="button"
                  disabled={processingId === document.id}
                  onClick={() => void handleProcess(document)}
                  className="rounded-md border border-black/15 px-2.5 py-1 text-xs font-medium transition-colors hover:border-black/40 disabled:opacity-50 dark:border-white/15 dark:hover:border-white/40"
                >
                  {processingId === document.id
                    ? "Starting…"
                    : document.status === "failed"
                      ? "Retry"
                      : "Process"}
                </button>
              ) : null}

              <button
                type="button"
                disabled={deletingId === document.id}
                onClick={() => void handleDelete(document)}
                className="rounded-md border border-black/15 px-2.5 py-1 text-xs transition-colors hover:border-red-500/50 hover:text-red-600 disabled:opacity-50 dark:border-white/15 dark:hover:text-red-400"
              >
                {deletingId === document.id ? "Deleting…" : "Delete"}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
