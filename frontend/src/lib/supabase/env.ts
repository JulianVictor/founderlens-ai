/**
 * Browser-safe Supabase configuration.
 *
 * `NEXT_PUBLIC_*` values are inlined into the client bundle at build time, so
 * only the project URL and the anon key may appear here. The anon key is
 * designed to be public: on its own it grants nothing, because every table is
 * protected by row-level security. The service-role key bypasses RLS entirely
 * and must never be referenced from this directory.
 *
 * The variables are read through literal `process.env.NEXT_PUBLIC_...`
 * expressions because that is the form Next.js substitutes at build time --
 * destructuring or dynamic indexing would leave them undefined in the browser.
 *
 * Validation is deliberately lazy. Throwing at module scope would fail the
 * production build on a machine that has no Supabase credentials, which is the
 * normal case in CI; failing when a client is actually constructed reports the
 * same problem at the point where it matters.
 */

function required(name: string, value: string | undefined): string {
  if (!value) {
    throw new Error(
      `${name} is not set. Copy frontend/.env.example to .env.local and fill it in.`,
    );
  }
  return value;
}

export function supabaseUrl(): string {
  return required(
    "NEXT_PUBLIC_SUPABASE_URL",
    process.env.NEXT_PUBLIC_SUPABASE_URL,
  );
}

export function supabaseAnonKey(): string {
  return required(
    "NEXT_PUBLIC_SUPABASE_ANON_KEY",
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
  );
}
