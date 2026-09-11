# Supabase

Supabase provides Auth, the application Postgres database and Storage for
uploaded documents.

```bash
supabase init     # only if config.toml is absent
supabase start    # local stack; prints the anon and service-role keys
supabase stop
```

## Migrations

SQL migrations live in `migrations/` and are applied in filename order. Every
migration must be reproducible from an empty database — no manual dashboard
edits.

```bash
supabase migration new <name>
supabase db reset     # re-applies every migration from scratch
```

Schema work (workspaces, documents, chunks and their row-level security
policies) lands in a later phase; this directory holds the layout and
conventions.

## Rules

- Every workspace-owned table carries `workspace_id` and is protected by RLS.
- The service-role key is backend-only. The browser sees the anon key alone.
