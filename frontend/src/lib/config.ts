/**
 * Browser-safe configuration.
 *
 * Only `NEXT_PUBLIC_*` variables may be referenced here — anything else would
 * either be `undefined` in the browser or leak a server secret into the bundle.
 */
export const config = {
  apiBaseUrl: process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000",
} as const;
