-- Row-level security for workspaces and workspace_members.
--
-- The guarantee this migration exists to provide: a user can never read or
-- write a workspace they are not a member of, regardless of what the client
-- asks for.
--
-- Why the helpers are SECURITY DEFINER
-- ------------------------------------
-- The natural policy for workspace_members -- "you may see rows of workspaces
-- you belong to" -- has to query workspace_members to decide. Evaluating that
-- subquery re-triggers the same policy, and Postgres rejects it with an
-- infinite-recursion error. A SECURITY DEFINER function runs as its owner and
-- is therefore not subject to the caller's policies, which breaks the cycle.
--
-- SECURITY DEFINER is dangerous when careless, so both helpers are:
--   * STABLE and side-effect free -- they only ever read;
--   * pinned to an explicit search_path, so a caller cannot shadow `public`
--     with their own objects and change what the function resolves to;
--   * parameterised only by workspace, never by user -- they always test
--     auth.uid(), so a caller cannot ask "is some *other* user a member".

create or replace function public.is_workspace_member(p_workspace_id uuid)
returns boolean
language sql
security definer
stable
set search_path = public, pg_temp
as $$
    select exists (
        select 1
          from public.workspace_members m
         where m.workspace_id = p_workspace_id
           and m.user_id = auth.uid()
    );
$$;

comment on function public.is_workspace_member(uuid) is
    'True when the calling user is a member of the workspace, in any role.';

create or replace function public.is_workspace_owner(p_workspace_id uuid)
returns boolean
language sql
security definer
stable
set search_path = public, pg_temp
as $$
    select exists (
        select 1
          from public.workspace_members m
         where m.workspace_id = p_workspace_id
           and m.user_id = auth.uid()
           and m.role = 'owner'
    );
$$;

comment on function public.is_workspace_owner(uuid) is
    'True when the calling user owns the workspace.';

revoke all on function public.is_workspace_member(uuid) from public;
revoke all on function public.is_workspace_owner(uuid) from public;
grant execute on function public.is_workspace_member(uuid) to authenticated;
grant execute on function public.is_workspace_owner(uuid) to authenticated;

-- Workspaces -----------------------------------------------------------------

create policy "workspaces are readable by their members"
    on public.workspaces
    for select
    to authenticated
    using (public.is_workspace_member(id));

create policy "workspaces are updatable by their owners"
    on public.workspaces
    for update
    to authenticated
    using (public.is_workspace_owner(id))
    with check (public.is_workspace_owner(id));

create policy "workspaces are deletable by their owners"
    on public.workspaces
    for delete
    to authenticated
    using (public.is_workspace_owner(id));

-- Deliberately no INSERT policy. Inserting a workspace row directly would
-- produce a workspace with no members -- invisible to its own creator, because
-- the SELECT policy above requires membership. Creation must be atomic with the
-- owner membership, so it goes through public.create_workspace() below.

-- Workspace members ----------------------------------------------------------

create policy "memberships are readable by workspace members"
    on public.workspace_members
    for select
    to authenticated
    using (public.is_workspace_member(workspace_id));

create policy "memberships are insertable by workspace owners"
    on public.workspace_members
    for insert
    to authenticated
    with check (public.is_workspace_owner(workspace_id));

create policy "memberships are updatable by workspace owners"
    on public.workspace_members
    for update
    to authenticated
    using (public.is_workspace_owner(workspace_id))
    with check (public.is_workspace_owner(workspace_id));

-- An owner may remove anyone; a member may remove themselves (leave). The
-- last-owner trigger still applies and will reject a departure that would
-- strand the workspace.
create policy "memberships are deletable by owners or by the member themselves"
    on public.workspace_members
    for delete
    to authenticated
    using (
        public.is_workspace_owner(workspace_id)
        or user_id = (select auth.uid())
    );

-- Workspace creation ---------------------------------------------------------

-- Creates a workspace and its owner membership in one transaction, so a
-- workspace can never exist without an owner. SECURITY DEFINER because the
-- membership insert would otherwise fail its own policy: the caller is not yet
-- an owner of a workspace that does not yet exist.
create or replace function public.create_workspace(p_name text)
returns public.workspaces
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_user_id uuid := auth.uid();
    v_workspace public.workspaces;
begin
    if v_user_id is null then
        raise exception 'authentication required' using errcode = 'insufficient_privilege';
    end if;

    insert into public.workspaces (name, created_by)
    values (btrim(p_name), v_user_id)
    returning * into v_workspace;

    insert into public.workspace_members (workspace_id, user_id, role)
    values (v_workspace.id, v_user_id, 'owner');

    return v_workspace;
end;
$$;

comment on function public.create_workspace(text) is
    'Creates a workspace owned by the calling user. The only supported way to '
    'create one: guarantees the owner membership exists atomically.';

revoke all on function public.create_workspace(text) from public;
grant execute on function public.create_workspace(text) to authenticated;
