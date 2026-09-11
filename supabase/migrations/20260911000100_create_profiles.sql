-- Profiles: application-owned data about an authenticated user.
--
-- auth.users is owned by Supabase Auth and must not be extended directly, so
-- every application-visible attribute of a user lives here instead. The row is
-- created by the signup trigger (see 20260911000500_handle_new_user.sql).

create table public.profiles (
    id uuid primary key references auth.users (id) on delete cascade,
    display_name text not null,
    created_at timestamptz not null default now(),

    constraint profiles_display_name_length
        check (char_length(display_name) between 1 and 100),
    constraint profiles_display_name_not_blank
        check (btrim(display_name) <> '')
);

comment on table public.profiles is
    'Application-owned profile for an auth.users identity.';

alter table public.profiles enable row level security;
alter table public.profiles force row level security;

-- A profile is visible only to the user it describes. Co-member visibility is
-- deliberately not granted yet: nothing in the MVP needs to read another
-- user's profile, and the narrower policy is the safer default to widen later.
create policy "profiles are readable by their owner"
    on public.profiles
    for select
    to authenticated
    using (id = (select auth.uid()));

create policy "profiles are updatable by their owner"
    on public.profiles
    for update
    to authenticated
    using (id = (select auth.uid()))
    with check (id = (select auth.uid()));

-- No insert or delete policy: profiles are created by the signup trigger and
-- removed by the cascade from auth.users. A client may not do either directly.
