import Link from "next/link";

import { signOut } from "@/app/login/actions";
import { createClient } from "@/lib/supabase/server";

export async function SiteHeader() {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  return (
    <header className="border-b border-black/10 dark:border-white/10">
      <nav className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
        <Link href="/" className="font-mono text-sm font-semibold tracking-tight">
          FounderLens<span className="text-black/40 dark:text-white/40"> AI</span>
        </Link>

        <ul className="flex items-center gap-6 text-sm">
          {user ? (
            <>
              <li>
                <Link
                  href="/dashboard"
                  className="text-black/60 transition-colors hover:text-black dark:text-white/60 dark:hover:text-white"
                >
                  Workspaces
                </Link>
              </li>
              <li>
                <form action={signOut}>
                  <button
                    type="submit"
                    className="text-black/60 transition-colors hover:text-black dark:text-white/60 dark:hover:text-white"
                  >
                    Sign out
                  </button>
                </form>
              </li>
            </>
          ) : (
            <>
              <li>
                <Link
                  href="/login"
                  className="text-black/60 transition-colors hover:text-black dark:text-white/60 dark:hover:text-white"
                >
                  Sign in
                </Link>
              </li>
              <li>
                <Link
                  href="/signup"
                  className="rounded-md bg-foreground px-3 py-1.5 text-background transition-opacity hover:opacity-90"
                >
                  Get started
                </Link>
              </li>
            </>
          )}
        </ul>
      </nav>
    </header>
  );
}
