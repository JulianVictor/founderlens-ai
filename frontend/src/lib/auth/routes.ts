/**
 * Which routes require a session, and where to send people who are in the
 * wrong place.
 *
 * Pure functions with no framework imports, so the redirect rules can be tested
 * directly rather than by driving a request through the middleware.
 */

/** Prefixes that require an authenticated user. */
const PROTECTED_PREFIXES = ["/dashboard"] as const;

/** Routes that only make sense when signed out. */
const AUTH_ROUTES = ["/login", "/signup"] as const;

/** Where a freshly signed-in user lands when no explicit target is given. */
export const DEFAULT_AUTHENTICATED_PATH = "/dashboard";

export function isProtectedPath(pathname: string): boolean {
  return PROTECTED_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}

export function isAuthRoute(pathname: string): boolean {
  return AUTH_ROUTES.some((route) => pathname === route);
}

/**
 * Where an already-authenticated user hitting `pathname` should be sent, or
 * `null` to let the request through.
 *
 * Showing a sign-in form to somebody who is already signed in is a dead end
 * that invites them to re-authenticate for no reason.
 */
export function redirectTargetForAuthenticatedUser(
  pathname: string,
): string | null {
  return isAuthRoute(pathname) ? DEFAULT_AUTHENTICATED_PATH : null;
}

/**
 * Sanitise a `?next=` value before redirecting to it.
 *
 * An unchecked redirect target is an open-redirect vulnerability: a link to
 * `/login?next=https://evil.example` would bounce the user to an attacker's
 * site carrying our domain's credibility. Only same-site absolute paths are
 * accepted, and `//host` is rejected because a browser reads it as
 * protocol-relative and leaves the site.
 */
export function safeRedirectPath(
  next: string | null | undefined,
  fallback: string = DEFAULT_AUTHENTICATED_PATH,
): string {
  if (!next) return fallback;
  if (!next.startsWith("/")) return fallback;
  if (next.startsWith("//")) return fallback;
  // Backslashes are normalised to forward slashes by some browsers, so "/\evil"
  // can escape the site the same way "//evil" does.
  if (next.startsWith("/\\")) return fallback;
  if (isAuthRoute(next)) return fallback;
  return next;
}
