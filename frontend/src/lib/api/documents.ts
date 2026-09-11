/**
 * Client for the FounderLens documents API.
 *
 * The browser calls the API directly, carrying the user's Supabase access
 * token. Uploads have to reach the API rather than Supabase Storage, because
 * the API is what inspects the bytes before accepting them -- a direct
 * browser-to-bucket upload would skip exactly that check.
 */

import { config } from "@/lib/config";
import type { DocumentStatus } from "@/lib/documents/format";

export type ApiDocument = {
  readonly id: string;
  readonly workspace_id: string;
  readonly uploaded_by: string;
  readonly filename: string;
  readonly mime_type: string;
  readonly size_bytes: number;
  readonly status: DocumentStatus;
  readonly error_message: string | null;
  readonly created_at: string;
  readonly updated_at: string;
};

/** An API call that failed, carrying enough to render a useful message. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail?: string,
  ) {
    super(detail ?? `Request failed with status ${status}`);
    this.name = "ApiError";
  }
}

async function toApiError(response: Response): Promise<ApiError> {
  let detail: string | undefined;
  try {
    const body: unknown = await response.json();
    if (body && typeof body === "object" && "detail" in body) {
      const value = (body as { detail: unknown }).detail;
      if (typeof value === "string") detail = value;
    }
  } catch {
    // A non-JSON error body (a proxy timeout page, say) is not worth failing
    // over -- the status alone is enough to say something useful.
  }
  return new ApiError(response.status, detail);
}

function documentsUrl(workspaceId: string): string {
  return `${config.apiBaseUrl}/api/v1/workspaces/${workspaceId}/documents`;
}

export async function listDocuments(
  workspaceId: string,
  accessToken: string,
  signal?: AbortSignal,
): Promise<ApiDocument[]> {
  const response = await fetch(documentsUrl(workspaceId), {
    headers: { Authorization: `Bearer ${accessToken}` },
    signal,
  });
  if (!response.ok) throw await toApiError(response);
  const body = (await response.json()) as { documents: ApiDocument[] };
  return body.documents;
}

export async function uploadDocument(
  workspaceId: string,
  accessToken: string,
  file: File,
): Promise<ApiDocument> {
  const form = new FormData();
  form.append("file", file);

  const response = await fetch(documentsUrl(workspaceId), {
    method: "POST",
    // Content-Type is deliberately not set: the browser has to add the
    // multipart boundary, and setting it by hand produces a body the server
    // cannot parse.
    headers: { Authorization: `Bearer ${accessToken}` },
    body: form,
  });
  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as ApiDocument;
}

export async function deleteDocument(
  workspaceId: string,
  accessToken: string,
  documentId: string,
): Promise<void> {
  const response = await fetch(`${documentsUrl(workspaceId)}/${documentId}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!response.ok) throw await toApiError(response);
}
