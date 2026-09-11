import { createBrowserClient } from "@supabase/ssr";

import { supabaseAnonKey, supabaseUrl } from "./env";

/**
 * Supabase client for Client Components.
 *
 * Reads and writes the session from browser cookies, which is what keeps it in
 * step with the server-side client rather than holding a second, divergent copy
 * of the session in local storage.
 */
export function createClient() {
  return createBrowserClient(supabaseUrl(), supabaseAnonKey());
}
