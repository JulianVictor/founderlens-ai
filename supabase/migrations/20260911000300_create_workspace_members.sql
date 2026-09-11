-- Workspace membership: the single authority for workspace access.
--
-- Every authorization decision in the system resolves to a lookup in this
-- table. Nothing else -- not workspaces.created_by, not a JWT claim -- may be
-- substituted for it.

create table public.workspace_members (
    workspace_id uuid not null references public.workspaces (id) on delete cascade,
    user_id uuid not null references auth.users (id) on delete cascade,
    role public.workspace_role not null default 'member',
    created_at timestamptz not null default now(),

    primary key (workspace_id, user_id)
);

comment on table public.workspace_members is
    'Which users may access which workspaces, and at what role.';

-- The primary key already indexes (workspace_id, user_id), which serves
-- "who is in this workspace" and the membership point-lookup. This index
-- serves the other direction -- "which workspaces does this user belong to" --
-- which is the hot path for GET /api/v1/workspaces and for every RLS policy
-- that runs as the current user.
create index workspace_members_user_id_idx on public.workspace_members (user_id);

-- Partial index supporting the last-owner check below, which is the only
-- query that filters on role.
create index workspace_members_owner_idx
    on public.workspace_members (workspace_id)
    where role = 'owner';

alter table public.workspace_members enable row level security;
alter table public.workspace_members force row level security;

-- A workspace must never be left without an owner: an ownerless workspace is
-- unadministrable, and its members can never be changed again.
create or replace function public.prevent_last_owner_removal()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_owner_count integer;
begin
    -- Demotion or removal of a non-owner can never strand a workspace.
    if tg_op = 'UPDATE' and (old.role <> 'owner' or new.role = 'owner') then
        return new;
    end if;

    if tg_op = 'DELETE' and old.role <> 'owner' then
        return old;
    end if;

    -- Deleting the workspace itself cascades to its memberships. That is not a
    -- stranding, so let it through: by the time the cascade reaches this row,
    -- the parent workspace is already gone.
    if tg_op = 'DELETE'
       and not exists (select 1 from public.workspaces w where w.id = old.workspace_id)
    then
        return old;
    end if;

    select count(*)
      into v_owner_count
      from public.workspace_members m
     where m.workspace_id = old.workspace_id
       and m.role = 'owner';

    if v_owner_count <= 1 then
        raise exception 'workspace % must retain at least one owner', old.workspace_id
            using errcode = 'check_violation';
    end if;

    if tg_op = 'DELETE' then
        return old;
    end if;

    return new;
end;
$$;

create trigger workspace_members_prevent_last_owner_removal
    before update or delete on public.workspace_members
    for each row
    execute function public.prevent_last_owner_removal();
