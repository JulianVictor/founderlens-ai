-- Chunks: the output of deterministic ingestion, held relationally until the
-- graph phase indexes them into Neo4j.
--
-- Postgres is the staging ground rather than the destination. Keeping chunks
-- here means embedding and graph construction can be re-run from stored
-- chunks instead of re-parsing every document, and it means a chunk exists in
-- the system of record before anything derived from it does.

create table public.document_chunks (
    -- The deterministic chunk id: uuid5 over (document, index, content hash).
    -- Supplied by the pipeline, not defaulted, because reproducibility is the
    -- point -- re-ingesting an unchanged document must land on the same ids.
    id uuid primary key,
    workspace_id uuid not null references public.workspaces (id) on delete cascade,
    document_id uuid not null references public.documents (id) on delete cascade,

    chunk_index integer not null,
    text text not null,
    token_count integer not null,

    -- Page the chunk starts on, and every page it covers. Null and empty for
    -- formats without pages.
    page_number integer,
    page_numbers integer[] not null default '{}',

    -- Span in the cleaned document text. Exact: slicing the cleaned text by
    -- these offsets reproduces `text`.
    start_offset integer not null,
    end_offset integer not null,
    -- Span in the text the parser produced -- the provenance anchor a citation
    -- ultimately resolves to.
    source_start_offset integer not null,
    source_end_offset integer not null,

    content_hash text not null,
    -- Which tokenizer produced token_count. Counts from different tokenizers
    -- are not comparable, so the number is meaningless without this.
    tokenizer text not null,

    -- The run that last wrote this row. Nulled rather than cascading if the
    -- job record is ever pruned: losing the audit trail must not delete the
    -- chunk itself.
    ingestion_job_id uuid references public.ingestion_jobs (id) on delete set null,
    created_at timestamptz not null default now(),

    constraint document_chunks_index_non_negative
        check (chunk_index >= 0),
    constraint document_chunks_token_count_non_negative
        check (token_count >= 0),
    constraint document_chunks_text_not_empty
        check (btrim(text) <> ''),
    constraint document_chunks_page_number_positive
        check (page_number is null or page_number >= 1),
    constraint document_chunks_span_ordered
        check (end_offset >= start_offset),
    constraint document_chunks_source_span_ordered
        check (source_end_offset >= source_start_offset),
    constraint document_chunks_checksum_is_sha256
        check (content_hash ~ '^[0-9a-f]{64}$'),
    -- One chunk per position. Together with the deterministic id this is what
    -- makes reprocessing replace a document's chunks rather than accumulate
    -- them: a second run cannot add a rival chunk at index 3.
    constraint document_chunks_unique_position
        unique (document_id, chunk_index)
);

comment on table public.document_chunks is
    'Chunks produced by deterministic ingestion, pending graph indexing.';

-- Reading a document's chunks in order: the shape every consumer wants.
create index document_chunks_document_index_idx
    on public.document_chunks (document_id, chunk_index);

create index document_chunks_workspace_idx
    on public.document_chunks (workspace_id);

-- Finding repeated content across a workspace -- boilerplate that appears in
-- many documents and should not be counted as independent evidence.
create index document_chunks_content_hash_idx
    on public.document_chunks (workspace_id, content_hash);

alter table public.document_chunks enable row level security;
alter table public.document_chunks force row level security;

create policy "chunks are readable by workspace members"
    on public.document_chunks
    for select
    to authenticated
    using (public.is_workspace_member(workspace_id));

-- No write policies. Chunks are derived data with a deterministic producer;
-- a client writing one directly would be asserting evidence that no document
-- supports.
