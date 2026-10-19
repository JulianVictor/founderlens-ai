"""Shared pytest fixtures.

Tests run against in-memory fakes rather than a live Postgres. The fakes
implement the repository ``Protocol``s structurally, so if a protocol and an
implementation drift apart, mypy says so. What is being tested here is the
authentication and authorization logic, which is ours; that ``select`` returns
rows is Postgres's job.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import replace
from typing import Any
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.ai.ingestion import DocumentChunk
from app.api.deps import (
    get_chunk_repository,
    get_document_repository,
    get_document_storage,
    get_ingestion_job_repository,
    get_profile_repository,
    get_task_runner,
    get_workspace_repository,
)
from app.core.background import TaskFactory, TaskRunner
from app.core.config import Environment, Settings, get_settings
from app.core.exceptions import DocumentStorageError
from app.main import create_app
from app.models.document import Document, DocumentStatus, NewDocument
from app.models.ingestion import IngestionJob, IngestionJobStatus, IngestionStage
from app.models.profile import Profile
from app.models.workspace import Workspace, WorkspaceMembership, WorkspaceRole
from app.repositories.chunk_repository import ChunkRepository
from app.repositories.document_repository import DocumentRepository
from app.repositories.document_storage import DocumentStorage
from app.repositories.ingestion_job_repository import IngestionJobRepository
from app.repositories.profile_repository import ProfileRepository
from app.repositories.workspace_repository import WorkspaceRepository
from tests import probes

JWT_SECRET = "test-jwt-secret-not-used-anywhere-real"
JWT_AUDIENCE = "authenticated"

#: Upload limit used throughout the tests.
MAX_DOCUMENT_BYTES = 4096

# Fixed identities so that assertions can name them.
OWNER_ID = UUID("00000000-0000-4000-8000-000000000001")
MEMBER_ID = UUID("00000000-0000-4000-8000-000000000002")
OUTSIDER_ID = UUID("00000000-0000-4000-8000-000000000003")

WORKSPACE_ID = UUID("00000000-0000-4000-8000-0000000000a1")
OTHER_WORKSPACE_ID = UUID("00000000-0000-4000-8000-0000000000a2")
UNKNOWN_WORKSPACE_ID = UUID("00000000-0000-4000-8000-0000000000ff")

_CREATED_AT = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


class FakeWorkspaceRepository:
    """In-memory :class:`WorkspaceRepository`."""

    def __init__(self, memberships: list[WorkspaceMembership]) -> None:
        self.memberships = memberships

    async def list_memberships(self, user_id: UUID) -> list[WorkspaceMembership]:
        return [m for m in self.memberships if m.user_id == user_id]

    async def get_membership(self, workspace_id: UUID, user_id: UUID) -> WorkspaceMembership | None:
        for m in self.memberships:
            if m.workspace.id == workspace_id and m.user_id == user_id:
                return m
        return None


class FakeProfileRepository:
    """In-memory :class:`ProfileRepository`."""

    def __init__(self, profiles: dict[UUID, Profile]) -> None:
        self.profiles = profiles

    async def get(self, user_id: UUID) -> Profile | None:
        return self.profiles.get(user_id)


@pytest.fixture
def settings() -> Iterator[Settings]:
    """Isolated settings that ignore any developer ``.env`` on disk."""
    get_settings.cache_clear()
    yield Settings(
        _env_file=None,
        environment=Environment.TEST,
        supabase_jwt_secret=JWT_SECRET,  # type: ignore[arg-type]
        supabase_jwt_audience=JWT_AUDIENCE,
        supabase_jwt_leeway_seconds=0,
        # Small enough that the oversize path can be exercised with a tiny
        # body instead of a real 25 MiB one.
        max_document_bytes=MAX_DOCUMENT_BYTES,
    )
    get_settings.cache_clear()


@pytest.fixture
def workspaces() -> dict[UUID, Workspace]:
    return {
        WORKSPACE_ID: Workspace(
            id=WORKSPACE_ID,
            name="Acme Research",
            created_by=OWNER_ID,
            created_at=_CREATED_AT,
        ),
        OTHER_WORKSPACE_ID: Workspace(
            id=OTHER_WORKSPACE_ID,
            name="Someone Else's Workspace",
            created_by=OUTSIDER_ID,
            created_at=_CREATED_AT,
        ),
    }


@pytest.fixture
def memberships(workspaces: dict[UUID, Workspace]) -> list[WorkspaceMembership]:
    """OWNER owns WORKSPACE_ID, MEMBER is a plain member of it, OUTSIDER owns
    OTHER_WORKSPACE_ID and belongs to nothing else."""
    return [
        WorkspaceMembership(
            workspace=workspaces[WORKSPACE_ID],
            user_id=OWNER_ID,
            role=WorkspaceRole.OWNER,
            created_at=_CREATED_AT,
        ),
        WorkspaceMembership(
            workspace=workspaces[WORKSPACE_ID],
            user_id=MEMBER_ID,
            role=WorkspaceRole.MEMBER,
            created_at=_CREATED_AT,
        ),
        WorkspaceMembership(
            workspace=workspaces[OTHER_WORKSPACE_ID],
            user_id=OUTSIDER_ID,
            role=WorkspaceRole.OWNER,
            created_at=_CREATED_AT,
        ),
    ]


@pytest.fixture
def profiles() -> dict[UUID, Profile]:
    return {
        OWNER_ID: Profile(id=OWNER_ID, display_name="Ada", created_at=_CREATED_AT),
        MEMBER_ID: Profile(id=MEMBER_ID, display_name="Grace", created_at=_CREATED_AT),
        OUTSIDER_ID: Profile(id=OUTSIDER_ID, display_name="Mallory", created_at=_CREATED_AT),
    }


@pytest.fixture
def workspace_repository(
    memberships: list[WorkspaceMembership],
) -> FakeWorkspaceRepository:
    return FakeWorkspaceRepository(memberships)


@pytest.fixture
def profile_repository(profiles: dict[UUID, Profile]) -> FakeProfileRepository:
    return FakeProfileRepository(profiles)


@pytest.fixture
def app(
    settings: Settings,
    workspace_repository: FakeWorkspaceRepository,
    profile_repository: FakeProfileRepository,
    document_repository: FakeDocumentRepository,
    document_storage: FakeDocumentStorage,
    ingestion_job_repository: FakeIngestionJobRepository,
    chunk_repository: FakeChunkRepository,
    task_runner: DeferredTaskRunner,
) -> FastAPI:
    """Application wired to the in-memory repositories.

    Overriding at the repository boundary rather than the service boundary keeps
    the real services -- and therefore the real authorization logic -- in the
    path under test.
    """
    application = create_app(settings)
    application.dependency_overrides[get_settings] = lambda: settings
    application.dependency_overrides[get_workspace_repository] = lambda: workspace_repository
    application.dependency_overrides[get_profile_repository] = lambda: profile_repository
    application.dependency_overrides[get_document_repository] = lambda: document_repository
    application.dependency_overrides[get_document_storage] = lambda: document_storage
    application.dependency_overrides[get_ingestion_job_repository] = lambda: (
        ingestion_job_repository
    )
    application.dependency_overrides[get_chunk_repository] = lambda: chunk_repository
    application.dependency_overrides[get_task_runner] = lambda: task_runner
    application.include_router(probes.router)
    return application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Unauthenticated HTTP client bound directly to the ASGI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as async_client:
        yield async_client


TokenFactory = Callable[..., str]


@pytest.fixture
def make_token() -> TokenFactory:
    """Mint Supabase-shaped access tokens, valid or deliberately not."""

    def factory(
        user_id: UUID = OWNER_ID,
        *,
        secret: str = JWT_SECRET,
        audience: str | None = JWT_AUDIENCE,
        algorithm: str = "HS256",
        expires_in: dt.timedelta = dt.timedelta(hours=1),
        email: str | None = "ada@example.com",
        role: str | None = "authenticated",
        subject: str | None = None,
        omit_exp: bool = False,
    ) -> str:
        now = dt.datetime.now(tz=dt.UTC)
        claims: dict[str, Any] = {
            "sub": subject if subject is not None else str(user_id),
            "iat": now,
        }
        if not omit_exp:
            claims["exp"] = now + expires_in
        if audience is not None:
            claims["aud"] = audience
        if email is not None:
            claims["email"] = email
        if role is not None:
            claims["role"] = role
        return jwt.encode(claims, secret, algorithm=algorithm)

    return factory


@pytest.fixture
def auth_headers(make_token: TokenFactory) -> Callable[..., dict[str, str]]:
    """Authorization header for a given user."""

    def factory(user_id: UUID = OWNER_ID, **kwargs: Any) -> dict[str, str]:
        return {"Authorization": f"Bearer {make_token(user_id, **kwargs)}"}

    return factory


# --- Documents ---------------------------------------------------------------

DOCUMENT_ID = UUID("00000000-0000-4000-8000-0000000000d1")
OTHER_DOCUMENT_ID = UUID("00000000-0000-4000-8000-0000000000d2")
UNKNOWN_DOCUMENT_ID = UUID("00000000-0000-4000-8000-0000000000df")

PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n%%EOF\n"
TEXT_BYTES = b"Acme raised a $12M Series A led by Initech Ventures.\n"
MARKDOWN_BYTES = b"# Acme\n\n- Series A: $12M\n"
# A PNG header: valid bytes, but not a type we accept. Used to prove that a
# lying filename does not get a file past the content check.
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


class FakeDocumentStorage:
    """In-memory :class:`DocumentStorage` that records what it was asked to do."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []
        #: Set to raise from the next upload, to exercise the failure paths.
        self.fail_upload = False
        self.fail_delete = False
        self.fail_download = False

    async def upload(self, path: str, data: bytes, content_type: str) -> None:
        if self.fail_upload:
            raise DocumentStorageError("upload")
        self.objects[path] = data

    async def download(self, path: str) -> bytes:
        if self.fail_download:
            raise DocumentStorageError("download")
        try:
            return self.objects[path]
        except KeyError as exc:
            raise DocumentStorageError("download") from exc

    async def delete(self, path: str) -> None:
        if self.fail_delete:
            raise DocumentStorageError("delete")
        self.deleted.append(path)
        self.objects.pop(path, None)


class FakeDocumentRepository:
    """In-memory :class:`DocumentRepository`, scoped exactly like the real one."""

    def __init__(self, documents: list[Document]) -> None:
        self.documents = list(documents)
        #: Set to raise from the next create, to exercise the compensation path.
        self.fail_create = False

    async def create(self, document: NewDocument) -> Document:
        if self.fail_create:
            raise RuntimeError("insert failed")
        now = dt.datetime.now(tz=dt.UTC)
        created = Document(
            id=document.id,
            workspace_id=document.workspace_id,
            uploaded_by=document.uploaded_by,
            filename=document.filename,
            storage_path=document.storage_path,
            mime_type=document.mime_type,
            size_bytes=document.size_bytes,
            checksum_sha256=document.checksum_sha256,
            status=DocumentStatus.UPLOADED,
            error_message=None,
            created_at=now,
            updated_at=now,
        )
        self.documents.append(created)
        return created

    async def list_for_workspace(self, workspace_id: UUID) -> list[Document]:
        found = [d for d in self.documents if d.workspace_id == workspace_id]
        return sorted(found, key=lambda d: d.created_at, reverse=True)

    async def get(self, workspace_id: UUID, document_id: UUID) -> Document | None:
        for d in self.documents:
            if d.id == document_id and d.workspace_id == workspace_id:
                return d
        return None

    async def find_by_checksum(self, workspace_id: UUID, checksum_sha256: str) -> Document | None:
        for d in self.documents:
            if d.workspace_id == workspace_id and d.checksum_sha256 == checksum_sha256:
                return d
        return None

    async def update_status(
        self,
        workspace_id: UUID,
        document_id: UUID,
        status: DocumentStatus,
        *,
        error_message: str | None = None,
    ) -> Document | None:
        for index, d in enumerate(self.documents):
            if d.id == document_id and d.workspace_id == workspace_id:
                updated = replace(
                    d,
                    status=status,
                    # Mirrors the database, which clears the explanation unless
                    # the new status is failed.
                    error_message=(error_message if status is DocumentStatus.FAILED else None),
                    updated_at=dt.datetime.now(tz=dt.UTC),
                )
                self.documents[index] = updated
                return updated
        return None

    async def delete(self, workspace_id: UUID, document_id: UUID) -> bool:
        for index, d in enumerate(self.documents):
            if d.id == document_id and d.workspace_id == workspace_id:
                del self.documents[index]
                return True
        return False


@pytest.fixture
def documents() -> list[Document]:
    """One document in each workspace, so isolation is testable by id."""
    now = dt.datetime(2026, 2, 1, tzinfo=dt.UTC)
    return [
        Document(
            id=DOCUMENT_ID,
            workspace_id=WORKSPACE_ID,
            uploaded_by=OWNER_ID,
            filename="acme-memo.pdf",
            storage_path=f"{WORKSPACE_ID}/{DOCUMENT_ID}.pdf",
            mime_type="application/pdf",
            size_bytes=len(PDF_BYTES),
            checksum_sha256="a" * 64,
            status=DocumentStatus.UPLOADED,
            error_message=None,
            created_at=now,
            updated_at=now,
        ),
        Document(
            id=OTHER_DOCUMENT_ID,
            workspace_id=OTHER_WORKSPACE_ID,
            uploaded_by=OUTSIDER_ID,
            filename="private-notes.md",
            storage_path=f"{OTHER_WORKSPACE_ID}/{OTHER_DOCUMENT_ID}.md",
            mime_type="text/markdown",
            size_bytes=len(MARKDOWN_BYTES),
            checksum_sha256="b" * 64,
            status=DocumentStatus.READY,
            error_message=None,
            created_at=now,
            updated_at=now,
        ),
    ]


@pytest.fixture
def document_repository(documents: list[Document]) -> FakeDocumentRepository:
    return FakeDocumentRepository(documents)


@pytest.fixture
def document_storage() -> FakeDocumentStorage:
    return FakeDocumentStorage()


# Structural conformance: if a fake stops satisfying the protocol the real code
# depends on, this fails at type-check time rather than at runtime in a test.
_: WorkspaceRepository = FakeWorkspaceRepository([])
__: ProfileRepository = FakeProfileRepository({})
___: DocumentRepository = FakeDocumentRepository([])
____: DocumentStorage = FakeDocumentStorage()


# --- Ingestion ---------------------------------------------------------------


class FakeIngestionJobRepository:
    """In-memory :class:`IngestionJobRepository`.

    Reproduces the behaviour the real one gets from the database, because the
    service's correctness depends on it: ``claim`` refuses while a job is
    active (the one-active-job-per-document index) and ``start`` only moves a
    job that is still queued (the ``status = 'queued'`` predicate). A fake that
    said yes to everything would let the duplicate-work tests pass while the
    real thing was broken.
    """

    def __init__(self) -> None:
        self.jobs: list[IngestionJob] = []
        #: Forces claim to fail, standing in for losing the insert race.
        self.fail_claim = False

    def _store(self, job: IngestionJob) -> IngestionJob:
        for index, existing in enumerate(self.jobs):
            if existing.id == job.id:
                self.jobs[index] = job
                return job
        raise AssertionError("job not found")

    async def claim(
        self, workspace_id: UUID, document_id: UUID, pipeline_version: str
    ) -> IngestionJob | None:
        if self.fail_claim:
            return None
        if any(j.document_id == document_id and j.status.is_active for j in self.jobs):
            return None
        now = dt.datetime.now(tz=dt.UTC)
        job = IngestionJob(
            id=uuid4(),
            workspace_id=workspace_id,
            document_id=document_id,
            status=IngestionJobStatus.QUEUED,
            current_stage=IngestionStage.UPLOAD,
            attempt=sum(1 for j in self.jobs if j.document_id == document_id) + 1,
            error_message=None,
            chunk_count=None,
            pipeline_version=pipeline_version,
            started_at=None,
            completed_at=None,
            created_at=now,
            updated_at=now,
        )
        self.jobs.append(job)
        return job

    async def get(self, workspace_id: UUID, job_id: UUID) -> IngestionJob | None:
        for job in self.jobs:
            if job.id == job_id and job.workspace_id == workspace_id:
                return job
        return None

    async def latest_for_document(
        self, workspace_id: UUID, document_id: UUID
    ) -> IngestionJob | None:
        found = [
            j for j in self.jobs if j.document_id == document_id and j.workspace_id == workspace_id
        ]
        return found[-1] if found else None

    async def active_for_document(
        self, workspace_id: UUID, document_id: UUID
    ) -> IngestionJob | None:
        for job in self.jobs:
            if (
                job.document_id == document_id
                and job.workspace_id == workspace_id
                and job.status.is_active
            ):
                return job
        return None

    async def start(self, job_id: UUID, stage: IngestionStage) -> IngestionJob | None:
        for job in self.jobs:
            if job.id == job_id and job.status is IngestionJobStatus.QUEUED:
                return self._store(
                    replace(
                        job,
                        status=IngestionJobStatus.RUNNING,
                        current_stage=stage,
                        started_at=dt.datetime.now(tz=dt.UTC),
                    )
                )
        return None

    async def advance(self, job_id: UUID, stage: IngestionStage) -> IngestionJob | None:
        for job in self.jobs:
            if job.id == job_id and job.status is IngestionJobStatus.RUNNING:
                return self._store(replace(job, current_stage=stage))
        return None

    async def finish(
        self,
        job_id: UUID,
        status: IngestionJobStatus,
        *,
        error_message: str | None = None,
        chunk_count: int | None = None,
    ) -> IngestionJob | None:
        for job in self.jobs:
            if job.id == job_id and job.status.is_active:
                return self._store(
                    replace(
                        job,
                        status=status,
                        error_message=error_message,
                        chunk_count=chunk_count,
                        completed_at=dt.datetime.now(tz=dt.UTC),
                    )
                )
        return None


class FakeChunkRepository:
    """In-memory :class:`ChunkRepository`, with the same replace semantics."""

    def __init__(self) -> None:
        self.chunks: dict[UUID, list[DocumentChunk]] = {}
        self.tokenizer: str | None = None
        #: How many times a replace happened, so a test can tell "wrote the
        #: same chunks again" from "did not write at all".
        self.replace_calls = 0

    async def replace_for_document(
        self,
        workspace_id: UUID,
        document_id: UUID,
        chunks: list[DocumentChunk],
        *,
        tokenizer: str,
        ingestion_job_id: UUID | None = None,
    ) -> int:
        self.replace_calls += 1
        self.tokenizer = tokenizer
        self.chunks[document_id] = list(chunks)
        return len(chunks)

    async def list_for_document(self, workspace_id: UUID, document_id: UUID) -> list[DocumentChunk]:
        return list(self.chunks.get(document_id, []))

    async def count_for_document(self, workspace_id: UUID, document_id: UUID) -> int:
        return len(self.chunks.get(document_id, []))


class DeferredTaskRunner:
    """Captures submitted work instead of running it.

    Lets a test observe the states a job passes through -- queued, then
    running, then terminal -- which an inline runner would hide by finishing
    everything before ``submit`` returned.
    """

    def __init__(self) -> None:
        self.submitted: list[tuple[str, TaskFactory]] = []

    async def submit(self, name: str, factory: TaskFactory) -> None:
        self.submitted.append((name, factory))

    async def run_all(self) -> None:
        """Run everything submitted so far, in order."""
        pending, self.submitted = self.submitted, []
        for _name, factory in pending:
            await factory()


@pytest.fixture
def ingestion_job_repository() -> FakeIngestionJobRepository:
    return FakeIngestionJobRepository()


@pytest.fixture
def chunk_repository() -> FakeChunkRepository:
    return FakeChunkRepository()


@pytest.fixture
def task_runner() -> DeferredTaskRunner:
    return DeferredTaskRunner()


_ingestion_jobs: IngestionJobRepository = FakeIngestionJobRepository()
_chunks: ChunkRepository = FakeChunkRepository()
_runner: TaskRunner = DeferredTaskRunner()
