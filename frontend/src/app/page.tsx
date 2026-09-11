const pipeline = [
  {
    stage: "Ingest",
    detail: "Documents are uploaded into a workspace, normalised and chunked.",
  },
  {
    stage: "Extract",
    detail: "Entities and relationships are pulled out as validated structured output.",
  },
  {
    stage: "Index",
    detail: "Chunks are embedded and resolved entities are written to the knowledge graph.",
  },
  {
    stage: "Retrieve",
    detail: "Questions fan out across vector search and workspace-scoped graph traversal.",
  },
  {
    stage: "Ground",
    detail: "Answers cite the evidence behind every claim, or report insufficient evidence.",
  },
] as const;

export default function Home() {
  return (
    <div className="flex flex-col gap-12">
      <section className="flex flex-col gap-4">
        <p className="font-mono text-xs uppercase tracking-widest text-black/40 dark:text-white/40">
          Evidence-grounded startup research
        </p>
        <h1 className="max-w-2xl text-4xl font-semibold tracking-tight text-balance">
          Ask hard questions about a company. Get answers you can check.
        </h1>
        <p className="max-w-2xl text-black/60 dark:text-white/60">
          FounderLens turns a pile of research documents into a retrievable corpus and a
          knowledge graph, then answers questions with citations back to the source text.
        </p>
      </section>

      <section className="flex flex-col gap-4">
        <h2 className="text-sm font-semibold tracking-tight">Pipeline</h2>
        <ol className="grid gap-px overflow-hidden rounded-lg border border-black/10 bg-black/10 sm:grid-cols-2 dark:border-white/10 dark:bg-white/10">
          {pipeline.map((step, index) => (
            <li
              key={step.stage}
              className="flex flex-col gap-1 bg-[var(--background)] p-5"
            >
              <span className="font-mono text-xs text-black/40 dark:text-white/40">
                {String(index + 1).padStart(2, "0")}
              </span>
              <span className="font-medium">{step.stage}</span>
              <span className="text-sm text-black/60 dark:text-white/60">{step.detail}</span>
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}
