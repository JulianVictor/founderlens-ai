"""Static checks over the SQL migrations.

No database is available in unit-test CI, so these verify what can be verified
without one: that every migration parses with the real PostgreSQL grammar, and
that the invariants the schema depends on are actually written down.

They are not a substitute for applying the migrations against Postgres. They
catch the failure that would otherwise only appear at `supabase db reset`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pglast
import pytest

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "supabase" / "migrations"
MIGRATIONS = sorted(MIGRATIONS_DIR.glob("*.sql"))

#: Tables that hold workspace-scoped or user-scoped data and must be protected.
PROTECTED_TABLES = [
    "profiles",
    "workspaces",
    "workspace_members",
    "documents",
    "ingestion_jobs",
    "document_chunks",
]


def test_migrations_exist() -> None:
    assert MIGRATIONS, f"no migrations found in {MIGRATIONS_DIR}"


@pytest.mark.parametrize("path", MIGRATIONS, ids=lambda p: p.name)
def test_migration_parses(path: Path) -> None:
    """Valid PostgreSQL, checked against libpg_query rather than by eye."""
    pglast.parse_sql(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", MIGRATIONS, ids=lambda p: p.name)
def test_migration_filename_is_ordered_and_named(path: Path) -> None:
    """Supabase applies migrations in filename order, so the timestamp prefix
    is what makes the sequence reproducible."""
    assert re.fullmatch(r"\d{14}_[a-z0-9_]+\.sql", path.name)


def test_migration_timestamps_are_unique() -> None:
    prefixes = [path.name.split("_", 1)[0] for path in MIGRATIONS]
    assert len(prefixes) == len(set(prefixes))


@pytest.fixture(scope="module")
def all_sql() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in MIGRATIONS).lower()


@pytest.mark.parametrize("table", PROTECTED_TABLES)
def test_row_level_security_is_enabled(all_sql: str, table: str) -> None:
    assert f"alter table public.{table} enable row level security" in all_sql


@pytest.mark.parametrize("table", PROTECTED_TABLES)
def test_row_level_security_is_forced(all_sql: str, table: str) -> None:
    """FORCE applies the policies to the table owner too.

    Without it, the role that owns the table bypasses every policy, which
    quietly removes the protection for exactly the connection most likely to be
    used by a misconfigured service.
    """
    assert f"alter table public.{table} force row level security" in all_sql


@pytest.mark.parametrize("table", PROTECTED_TABLES)
def test_protected_table_has_at_least_one_policy(all_sql: str, table: str) -> None:
    assert f"on public.{table}" in all_sql


def test_security_definer_functions_pin_their_search_path(all_sql: str) -> None:
    """A SECURITY DEFINER function without a pinned search_path can be tricked
    into resolving an object the caller controls, while running as its owner.

    Every such function in the schema must set one.
    """
    definers = re.findall(r"create (?:or replace )?function(.*?)\bas \$\$", all_sql, re.DOTALL)
    security_definers = [body for body in definers if "security definer" in body]

    assert security_definers, "expected SECURITY DEFINER helpers to exist"
    for body in security_definers:
        assert "set search_path" in body, body.strip()[:120]


def test_workspace_members_is_indexed_by_user(all_sql: str) -> None:
    """The hot path for listing a user's workspaces, and for every RLS policy."""
    assert "on public.workspace_members (user_id)" in all_sql


def test_workspace_role_enum_matches_the_application(all_sql: str) -> None:
    """The database enum and the Python enum must not drift apart."""
    from app.models.workspace import WorkspaceRole

    match = re.search(r"create type public\.workspace_role as enum \(([^)]*)\)", all_sql)
    assert match is not None
    declared = {value.strip().strip("'") for value in match.group(1).split(",")}

    assert declared == {role.value for role in WorkspaceRole}


def test_signup_trigger_is_installed(all_sql: str) -> None:
    """Default workspace creation happens in the database, so it cannot be
    skipped by a client that forgets to call it."""
    assert "after insert on auth.users" in all_sql
    assert "public.handle_new_user()" in all_sql


def test_signup_creates_profile_workspace_and_owner_membership(all_sql: str) -> None:
    trigger_body = all_sql.split("create or replace function public.handle_new_user()")[1]
    trigger_body = trigger_body.split("$$;")[0]

    assert "insert into public.profiles" in trigger_body
    assert "insert into public.workspaces" in trigger_body
    assert "insert into public.workspace_members" in trigger_body
    assert "'owner'" in trigger_body


def enum_values(all_sql: str, type_name: str) -> set[str]:
    """Values declared for a Postgres enum.

    Parsed by splitting rather than by regex so the comments inside the
    declarations do not have to be matched around.
    """
    marker = f"create type public.{type_name} as enum ("
    start = all_sql.index(marker) + len(marker)
    body = all_sql[start : all_sql.index(")", start)]

    values: set[str] = set()
    for line in body.splitlines():
        for part in line.split("--")[0].split(","):
            cleaned = part.strip().strip("'")
            if cleaned:
                values.add(cleaned)

    # Values added by a later migration are part of the type too. Reading only
    # the CREATE would report a type that no longer exists.
    added = f"alter type public.{type_name} add value"
    for line in all_sql.splitlines():
        stripped = line.split("--")[0].strip()
        if stripped.startswith(added):
            quoted = [part for part in stripped.split("'") if part.strip()]
            # "... add value if not exists 'embed' after 'persist'" -> the
            # value is the first quoted token; the rest positions it.
            for part in quoted:
                if part.strip() and " " not in part.strip():
                    values.add(part.strip())
                    break
    return values


def test_only_one_ingestion_job_may_be_active_per_document(all_sql: str) -> None:
    """The real idempotency guard.

    Two concurrent process requests can both read "no active job" before
    either writes one, so only a unique index can settle that. Application
    code cannot.
    """
    assert "unique index ingestion_jobs_one_active_per_document" in all_sql
    assert "where status in ('queued', 'running')" in all_sql


def test_chunks_are_unique_per_position(all_sql: str) -> None:
    """With deterministic ids, this is what makes reprocessing replace a
    document's chunks rather than accumulate them."""
    assert "unique (document_id, chunk_index)" in all_sql


def test_ingestion_stage_enum_matches_the_application(all_sql: str) -> None:
    from app.models.ingestion import IngestionStage

    assert enum_values(all_sql, "ingestion_stage") == {stage.value for stage in IngestionStage}


def test_job_status_enum_matches_the_application(all_sql: str) -> None:
    from app.models.ingestion import IngestionJobStatus

    assert enum_values(all_sql, "ingestion_job_status") == {
        status.value for status in IngestionJobStatus
    }


def test_document_status_enum_matches_the_application(all_sql: str) -> None:
    from app.models.document import DocumentStatus

    assert enum_values(all_sql, "document_status") == {status.value for status in DocumentStatus}


def test_new_stages_are_positioned_in_execution_order(all_sql: str) -> None:
    """IngestionStage.order depends on declaration order, so a value appended
    rather than positioned would make the database and the application
    disagree about what "further along" means."""
    assert "add value if not exists 'embed' after 'persist'" in all_sql
    assert "add value if not exists 'index' after 'embed'" in all_sql


def test_entity_stages_do_not_exist_yet(all_sql: str) -> None:
    """A stage the pipeline cannot reach would make current_stage a claim
    rather than a fact."""
    stages = enum_values(all_sql, "ingestion_stage")

    assert "resolve" not in stages
    assert "graph" not in stages


def test_a_job_is_completed_exactly_when_it_is_terminal(all_sql: str) -> None:
    """Otherwise "is it finished" has two answers that can disagree."""
    assert "ingestion_jobs_completed_iff_terminal" in all_sql


def test_chunks_survive_losing_their_job_record(all_sql: str) -> None:
    """Pruning the audit trail must not delete the evidence."""
    assert "references public.ingestion_jobs (id) on delete set null" in all_sql


def test_derived_tables_cascade_from_their_document(all_sql: str) -> None:
    """Deleting a document must not leave chunks or jobs behind describing
    text that no longer exists."""
    assert all_sql.count("references public.documents (id) on delete cascade") >= 2
