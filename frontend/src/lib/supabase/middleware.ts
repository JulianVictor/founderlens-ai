import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

import { isProtectedPath, redirectTargetForAuthenticatedUser } from "@/lib/auth/routes";

import { supabaseAnonKey, supabaseUrl } from "./env";

/**
 * Refreshes the Supabase session and enforces route protection.
 *
 * Two things happen here, and the order matters:
 *
 *  1. The session is refreshed. Access tokens are short-lived, so without a
 *     refresh on each request a user would be signed out mid-session. The
 *     refreshed cookies have to be written onto the response that is actually
 *     returned, which is why the response object is rebuilt inside `setAll`.
 *
 *  2. `supabase.auth.getUser()` is called -- not `getSession()`. `getSession()`
 *     reads the cookie and trusts it; `getUser()` revalidates the token with
 *     the Supabase Auth server. Since a cookie is entirely under the client's
 *     control, only the revalidated answer is safe to make a redirect decision
 *     on.
 */
export async function updateSession(request: NextRequest) {
  let supabaseResponse = NextResponse.next({ request });

  const supabase = createServerClient(supabaseUrl(), supabaseAnonKey(), {
    cookies: {
      getAll() {
        return request.cookies.getAll();
      },
      setAll(cookiesToSet) {
        cookiesToSet.forEach(({ name, value }) =>
          request.cookies.set(name, value),
        );
        supabaseResponse = NextResponse.next({ request });
        cookiesToSet.forEach(({ name, value, options }) =>
          supabaseResponse.cookies.set(name, value, options),
        );
      },
    },
  });

  const {
    data: { user },
  } = await supabase.auth.getUser();

  const pathname = request.nextUrl.pathname;

  if (!user && isProtectedPath(pathname)) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    // Remember where they were going so sign-in can return them there.
    url.searchParams.set("next", pathname);
    return NextResponse.redirect(url);
  }

  const target = user ? redirectTargetForAuthenticatedUser(pathname) : null;
  if (target) {
    const url = request.nextUrl.clone();
    url.pathname = target;
    url.search = "";
    return NextResponse.redirect(url);
  }

  return supabaseResponse;
}
