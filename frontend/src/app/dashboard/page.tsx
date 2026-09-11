import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { createClient } from "@/lib/supabase/server";

export const metadata: Metadata = {
  title: "Workspaces — FounderLens AI",
};

export default async function DashboardPage() {
  const supabase = await createClient();

  // The middleware already redirects anonymous requests, but this page checks
  // again rather than assuming it ran. Middleware is a routing concern and its
  // matcher can be changed; a page that reads user data should not depend on
  // configuration elsewhere for its access control.
  const {
    data: { user },
  } = await supabase.auth.getUser();

  if (!user) {
    redirect("/login?next=/dashboard");
  }

  // Reads go through the browser-side anon key, so row-level security is what
  // scopes this query -- not a filter written here. A user sees their own
  // memberships because the policy says so.
  const { data: memberships, error } = await supabase
    .from("workspace_members")
    .select("role, created_at, workspaces (id, name, created_at)")
    .order("created_at", { ascending: true });

  const profileResult = await supabase
    .from("profiles")
    .select("display_name")
    .eq("id", user.id)
    .maybeSingle();

  const displayName = profileResult.data?.display_name ?? user.email;

  return (
    <div className="flex flex-col gap-8">
      <header className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold tracking-tight">Workspaces</h1>
        <p className="text-sm text-black/60 dark:text-white/60">
          Signed in as {displayName}. A workspace holds a document set, its
          embeddings and its slice of the knowledge graph.
        </p>
      </header>

      {error ? (
        <p
          role="alert"
          className="rounded-lg border border-red-500/30 bg-red-500/5 p-4 text-sm text-red-600 dark:text-red-400"
        >
          Could not load workspaces: {error.message}
        </p>
      ) : memberships && memberships.length > 0 ? (
        <ul className="flex flex-col gap-px overflow-hidden rounded-lg border border-black/10 bg-black/10 dark:border-white/10 dark:bg-white/10">
          {memberships.map((membership, index) => {
            const workspace = Array.isArray(membership.workspaces)
              ? membership.workspaces[0]
              : membership.workspaces;
            return (
              <li
                key={workspace?.id ?? index}
                className="flex items-center justify-between gap-4 bg-[var(--background)] px-5 py-4"
              >
                <span className="font-medium">{workspace?.name}</span>
                <span className="font-mono text-xs uppercase tracking-wider text-black/40 dark:text-white/40">
                  {membership.role}
                </span>
              </li>
            );
          })}
        </ul>
      ) : (
        <div className="rounded-lg border border-dashed border-black/15 p-10 text-center dark:border-white/15">
          <p className="text-sm font-medium">No workspaces yet</p>
          <p className="mx-auto mt-1 max-w-sm text-sm text-black/50 dark:text-white/50">
            A default workspace is created with your account. If you are seeing
            this, the signup trigger may not have run.
          </p>
        </div>
      )}

      <p className="text-sm text-black/50 dark:text-white/50">
        Document upload and the research view arrive in a later phase.
      </p>
    </div>
  );
}
