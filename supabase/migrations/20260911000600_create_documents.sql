-- Documents: metadata for one uploaded research file.
--
-- The bytes live in Supabase Storage. This row is authoritative for everything
-- about them -- who uploaded it, what it actually is, where it sits in the
-- bucket, and how far ingestion has got. Storage holds no metadata of its own
-- that we rely on.

create type public.document_status as enum (
    'uploaded',    -- accepted and stored; nothing derived from it yet
    'processing',  -- an ingestion job is running over it
    'ready',       -- ingested; chunks, embeddings and graph data exist
    'failed'       -- ingestion failed; error_message says why
);

comment on type public.document_status is
    'Lifecycle of a document through the ingestion pipeline.';

create table public.documents (
    id uuid primary key default gen_random_uuid(),
    workspace_id uuid not null references public.workspaces (id) on delete cascade,
    -- Historical record of who uploaded it. Access is decided by workspace
    -- membership, never by this column: a document belongs to the workspace,
    -- not to the person who happened to add it.
    uploaded_by uuid not null references auth.users (id) on delete restrict,

    -- The name the user's file had. Display only. It is never used to build a
    -- storage path and never trusted to say what the file contains.
    filename text not null,
    -- Object key within the research-documents bucket. Derived from ids and the
    -- *detected* content type, so it cannot contain anything user-controlled.
    storage_path text not null,
    -- The content type the server determined by inspecting the bytes, not the
    -- one the client claimed.
    mime_type text not null,
    size_bytes bigint not null,
    -- Dedup key and provenance anchor: identifies the exact bytes ingested,
    -- independently of what the file was called.
    checksum_sha256 text not null,

    status public.document_status not null default 'uploaded',
    error_message text,

    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),

    constraint documents_filename_length
        check (char_length(filename) between 1 and 255),
    constraint documents_filename_not_blank
        check (btrim(filename) <> ''),
    constraint documents_size_positive
        check (size_bytes > 0),
    constraint documents_checksum_is_sha256
        check (checksum_sha256 ~ '^[0-9a-f]{64}$'),
    -- Keeps error_message meaningful: a row that is not failed must not carry a
    -- stale explanation from an earlier attempt.
    constraint documents_error_only_when_failed
        check (status = 'failed' or error_message is null),
    -- The same bytes uploaded twice into one workspace resolve to the existing
    -- document rather than duplicating the corpus, which would skew retrieval
    -- by making one source count twice.
    constraint documents_unique_content_per_workspace
        unique (workspace_id, checksum_sha256),
    -- One object per document. Enforced globally, not per workspace, because a
    -- storage key collision across workspaces would cross the isolation
    -- boundary.
    constraint documents_storage_path_unique
        unique (storage_path)
);

comment on table public.documents is
    'Metadata for an uploaded research document. Bytes live in Supabase Storage.';

-- The listing query: a workspace's documents, newest first.
create index documents_workspace_created_idx
    on public.documents (workspace_id, created_at desc);

-- Supports "which documents still need ingesting", the query a future worker
-- will run. Partial, because the terminal states are the overwhelming majority.
create index documents_pending_idx
    on public.documents (workspace_id, status)
    where status in ('uploaded', 'processing');

-- updated_at must not be client-supplied: a row whose timestamp says whatever
-- the writer wanted is useless for ordering or for cache invalidation.
create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at := now();
    return new;
end;
$$;

create trigger documents_set_updated_at
    before update on public.documents
    for each row
    execute function public.set_updated_at();

alter table public.documents enable row level security;
alter table public.documents force row level security;

-- Every policy resolves through workspace membership, so a document is
-- reachable exactly when its workspace is.
create policy "documents are readable by workspace members"
    on public.documents
    for select
    to authenticated
    using (public.is_workspace_member(workspace_id));

-- Inserts do not come from the browser: upload goes through the API, which
-- inspects the bytes before writing anything. The policy exists so that a
-- direct insert is still constrained if it ever becomes a supported path --
-- a member may only record a document as themselves, in their own workspace.
create policy "documents are insertable by workspace members as themselves"
    on public.documents
    for insert
    to authenticated
    with check (
        public.is_workspace_member(workspace_id)
        and uploaded_by = (select auth.uid())
    );

create policy "documents are updatable by workspace members"
    on public.documents
    for update
    to authenticated
    using (public.is_workspace_member(workspace_id))
    with check (public.is_workspace_member(workspace_id));

-- Any member may delete a document. The workspace owns its corpus; restricting
-- deletion to the uploader would strand documents when someone leaves.
create policy "documents are deletable by workspace members"
    on public.documents
    for delete
    to authenticated
    using (public.is_workspace_member(workspace_id));
