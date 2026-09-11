-- Workspaces: the isolation boundary for all FounderLens data.
--
-- Every document, chunk, entity and research run added in later phases hangs
-- off a workspace, and every access check resolves to "is this user a member of
-- this workspace". Row-level security policies are added in
-- 20260911000400_workspace_access_policies.sql, once workspace_members exists
-- for them to consult.

create type public.workspace_role as enum ('owner', 'member');

comment on type public.workspace_role is
    'MVP role set. owner may administer members; member may use the workspace.';

create table public.workspaces (
    id uuid primary key default gen_random_uuid(),
    name text not null,
    -- Historical record of who created the workspace. Deliberately not an
    -- authorization input: membership in workspace_members is the only
    -- authority, so transferring ownership never requires rewriting this.
    created_by uuid not null references auth.users (id) on delete restrict,
    created_at timestamptz not null default now(),

    constraint workspaces_name_length
        check (char_length(name) between 1 and 100),
    constraint workspaces_name_not_blank
        check (btrim(name) <> '')
);

comment on table public.workspaces is
    'A research workspace. The isolation boundary for every other table.';

create index workspaces_created_by_idx on public.workspaces (created_by);

alter table public.workspaces enable row level security;
alter table public.workspaces force row level security;
