-- Ingestion jobs: one run of the pipeline over one document.
--
-- Exists so that a failure is diagnosable and resumable rather than an opaque
-- document status. A job records how far it got, what went wrong, and which
-- attempt it was, which is the difference between "ingestion failed" and
-- "extraction failed on attempt 2 because the PDF is password protected".

create type public.ingestion_stage as enum (
    'upload',   -- acquiring the stored bytes
    'extract',  -- bytes to text
    'clean',    -- normalising text, preserving offsets
    'chunk',    -- splitting into retrievable units
    'persist'   -- writing chunks
    -- 'embed' and 'graph' are deliberately absent: adding a value to an enum
    -- is a migration, and inventing stages the pipeline cannot reach would
    -- make current_stage a claim rather than a fact.
);

comment on type public.ingestion_stage is
    'Stages of the deterministic ingestion pipeline, in execution order.';

create type public.ingestion_job_status as enum (
    'queued',
    'running',
    'succeeded',
    'failed',
    'cancelled'
);

create table public.ingestion_jobs (
    id uuid primary key default gen_random_uuid(),
    workspace_id uuid not null references public.workspaces (id) on delete cascade,
    document_id uuid not null references public.documents (id) on delete cascade,

    status public.ingestion_job_status not null default 'queued',
    -- Furthest stage reached. Starts at the first stage rather than being
    -- nullable: a queued job has not run any stage, and 'upload' is where it
    -- will begin.
    current_stage public.ingestion_stage not null default 'upload',

    -- Which attempt this is for the document. A retry is a new row, not a
    -- mutated one, so the history of what was tried survives.
    attempt integer not null default 1,
    error_message text,
    -- Populated on success, so "how many chunks did this produce" does not
    -- require counting rows in another table.
    chunk_count integer,
    -- Which code produced the result. Answers "what needs re-running after
    -- this change" without guessing from timestamps.
    pipeline_version text not null,

    started_at timestamptz,
    completed_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),

    constraint ingestion_jobs_attempt_positive
        check (attempt > 0),
    constraint ingestion_jobs_chunk_count_non_negative
        check (chunk_count is null or chunk_count >= 0),
    -- Keeps error_message meaningful: a job that is not failed must not carry
    -- an explanation left over from an earlier state.
    constraint ingestion_jobs_error_only_when_failed
        check (status = 'failed' or error_message is null),
    -- completed_at is set exactly when the job reaches a terminal state, so
    -- "is it finished" has one answer rather than two that can disagree.
    constraint ingestion_jobs_completed_iff_terminal
        check (
            (status in ('succeeded', 'failed', 'cancelled'))
            = (completed_at is not null)
        ),
    constraint ingestion_jobs_started_before_completed
        check (
            completed_at is null
            or started_at is null
            or completed_at >= started_at
        )
);

comment on table public.ingestion_jobs is
    'One run of the ingestion pipeline over one document.';

-- **The idempotency guard.** At most one job per document may be queued or
-- running at a time. Enforced here rather than by a check in application code
-- because two concurrent process requests can both read "no active job" before
-- either writes one; only the database can settle that race.
create unique index ingestion_jobs_one_active_per_document
    on public.ingestion_jobs (document_id)
    where status in ('queued', 'running');

-- "What is the latest job for this document", which is what the status
-- endpoint asks on every poll.
create index ingestion_jobs_document_created_idx
    on public.ingestion_jobs (document_id, created_at desc);

create index ingestion_jobs_workspace_created_idx
    on public.ingestion_jobs (workspace_id, created_at desc);

create trigger ingestion_jobs_set_updated_at
    before update on public.ingestion_jobs
    for each row
    execute function public.set_updated_at();

alter table public.ingestion_jobs enable row level security;
alter table public.ingestion_jobs force row level security;

-- Members may watch progress. Writes come only from the API, which runs the
-- pipeline with the service role; there is deliberately no insert, update or
-- delete policy, because a client inventing or editing a job record would be
-- describing work that nothing is doing.
create policy "ingestion jobs are readable by workspace members"
    on public.ingestion_jobs
    for select
    to authenticated
    using (public.is_workspace_member(workspace_id));
