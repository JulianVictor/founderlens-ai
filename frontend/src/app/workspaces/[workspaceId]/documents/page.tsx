import type { Metadata } from "next";
import Link from "next/link";
import { notFound, redirect } from "next/navigation";

import { DocumentManager } from "@/components/documents/document-manager";
import { createClient } from "@/lib/supabase/server";

export const metadata: Metadata = {
  title: "Documents — FounderLens AI",
};

export default async function DocumentsPage({
  params,
}: PageProps<"/workspaces/[workspaceId]/documents">) {
  const { workspaceId } = await params;
  const supabase = await createClient();

  const {
    data: { user },
  } = await supabase.auth.getUser();

  if (!user) {
    redirect(`/login?next=/workspaces/${workspaceId}/documents`);
  }

  // Row-level security is what scopes this: a workspace the user is not a
  // member of returns no row, so a guessed id renders the same 404 as an id
  // that never existed. The API enforces the same rule independently for every
  // call the client component makes.
  const { data: workspace } = await supabase
    .from("workspaces")
    .select("id, name")
    .eq("id", workspaceId)
    .maybeSingle();

  if (!workspace) {
    notFound();
  }

  return (
    <div className="flex flex-col gap-8">
      <header className="flex flex-col gap-2">
        <Link
          href="/dashboard"
          className="font-mono text-xs uppercase tracking-widest text-black/40 transition-colors hover:text-black dark:text-white/40 dark:hover:text-white"
        >
          ← Workspaces
        </Link>
        <h1 className="text-2xl font-semibold tracking-tight">{workspace.name}</h1>
        <p className="text-sm text-black/60 dark:text-white/60">
          The document set this workspace researches over.
        </p>
      </header>

      <DocumentManager workspaceId={workspaceId} />
    </div>
  );
}
