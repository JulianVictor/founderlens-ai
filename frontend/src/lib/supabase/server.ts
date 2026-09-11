import { cookies } from "next/headers";

import { createServerClient } from "@supabase/ssr";

import { supabaseAnonKey, supabaseUrl } from "./env";

/**
 * Supabase client for Server Components, Server Actions and Route Handlers.
 *
 * Must be created per request and never hoisted into a module-level singleton:
 * it closes over that request's cookies, so a shared instance would serve one
 * user's session to another.
 */
export async function createClient() {
  const cookieStore = await cookies();

  return createServerClient(supabaseUrl(), supabaseAnonKey(), {
    cookies: {
      getAll() {
        return cookieStore.getAll();
      },
      setAll(cookiesToSet) {
        try {
          cookiesToSet.forEach(({ name, value, options }) =>
            cookieStore.set(name, value, options),
          );
        } catch {
          // Server Components cannot set cookies. That is fine: the middleware
          // refreshes the session on every request, so the only writes lost
          // here are ones that have already happened there.
        }
      },
    },
  });
}
