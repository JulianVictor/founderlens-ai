import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Workspaces — FounderLens AI",
};

export default function DashboardPage() {
  return (
    <div className="flex flex-col gap-8">
      <header className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold tracking-tight">Workspaces</h1>
        <p className="text-sm text-black/60 dark:text-white/60">
          A workspace holds a document set, its embeddings and its slice of the knowledge
          graph. Everything is scoped to the workspace it belongs to.
        </p>
      </header>

      <div className="rounded-lg border border-dashed border-black/15 p-10 text-center dark:border-white/15">
        <p className="text-sm font-medium">No workspaces yet</p>
        <p className="mx-auto mt-1 max-w-sm text-sm text-black/50 dark:text-white/50">
          Workspace creation, document upload and the research view arrive in a later
          phase. This shell exists so the routing and layout are already in place.
        </p>
      </div>
    </div>
  );
}
