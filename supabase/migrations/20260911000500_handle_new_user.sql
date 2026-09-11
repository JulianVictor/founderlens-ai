-- On signup, give the new user a profile and a default workspace they own.
--
-- This runs as a trigger on auth.users rather than as a step in the frontend
-- signup handler, for three reasons:
--
--   1. Atomicity. Account, profile, workspace and membership are committed in
--      one transaction. A client-side sequence can fail between steps and leave
--      an account with no workspace, which is a state nothing else handles.
--   2. Coverage. It fires for every signup path -- email/password, OAuth,
--      magic link, an invite created from the Supabase dashboard -- not just
--      the one form we happen to have written.
--   3. Trust. The frontend cannot be relied on to have run it, so no other code
--      has to defend against its absence.

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_display_name text;
    v_workspace_id uuid;
begin
    -- display_name comes from signup metadata when the form collected one;
    -- otherwise fall back to the local part of the email, then to a constant.
    -- profiles.display_name is NOT NULL and rejects blanks, so this must
    -- always resolve to something non-empty.
    v_display_name := coalesce(
        nullif(btrim(new.raw_user_meta_data ->> 'display_name'), ''),
        nullif(btrim(split_part(coalesce(new.email, ''), '@', 1)), ''),
        'Founder'
    );
    v_display_name := left(v_display_name, 100);

    insert into public.profiles (id, display_name)
    values (new.id, v_display_name)
    on conflict (id) do nothing;

    -- The trigger owns this insert, so it bypasses the "no INSERT policy on
    -- workspaces" rule by design -- it is the other atomic creation path
    -- alongside public.create_workspace().
    insert into public.workspaces (name, created_by)
    values (left(v_display_name || '''s Workspace', 100), new.id)
    returning id into v_workspace_id;

    insert into public.workspace_members (workspace_id, user_id, role)
    values (v_workspace_id, new.id, 'owner');

    return new;
end;
$$;

comment on function public.handle_new_user() is
    'Creates the profile and default owned workspace for a newly signed-up user.';

create trigger on_auth_user_created
    after insert on auth.users
    for each row
    execute function public.handle_new_user();
