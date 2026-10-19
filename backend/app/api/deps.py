"""Reusable FastAPI dependencies.

Two of these matter beyond this phase:

* :func:`get_current_user` -- put it on a route and the route is authenticated.
* :func:`WorkspaceMember` -- annotate a ``workspace_id`` path parameter with it
  and the route is authenticated *and* scoped to a workspace the caller belongs
  to, with their role already resolved.

The intent is that a future route cannot accidentally skip the workspace check,
because obtaining the workspace is the same act as being authorised for it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import lru_cache
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import Depends, Path, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.ai.embeddings import EmbeddingProvider, build_embedding_provider
from app.ai.graph import ChunkGraph, Neo4jChunkGraph
from app.ai.ingestion import (
    ChunkerConfig,
    ParserRegistry,
    TextCleaner,
    Tokenizer,
    build_default_registry,
    get_tokenizer,
)
from app.core.background import TaskRunner
from app.core.config import Settings, get_settings
from app.core.db import Database
from app.core.exceptions import AuthenticationError
from app.core.neo4j import Neo4jDatabase
from app.core.security import AuthenticatedUser, SupabaseJWTVerifier
from app.models.workspace import WorkspaceMembership, WorkspaceRole
from app.repositories.chunk_repository import ChunkRepository, PostgresChunkRepository
from app.repositories.document_repository import (
    DocumentRepository,
    PostgresDocumentRepository,
)
from app.repositories.document_storage import (
    DocumentStorage,
    SupabaseDocumentStorage,
)
from app.repositories.ingestion_job_repository import (
    IngestionJobRepository,
    PostgresIngestionJobRepository,
)
from app.repositories.profile_repository import (
    PostgresProfileRepository,
    ProfileRepository,
)
from app.repositories.workspace_repository import (
    PostgresWorkspaceRepository,
    WorkspaceRepository,
)
from app.services.document_service import DocumentService
from app.services.ingestion_service import IngestionService
from app.services.profile_service import ProfileService
from app.services.workspace_service import WorkspaceService

SettingsDep = Annotated[Settings, Depends(get_settings)]

# auto_error=False so that a missing Authorization header reaches our own code.
# With auto_error=True, FastAPI answers 403 for an absent header, which is the
# wrong status: the caller has not failed an authorization check, they have not
# authenticated at all.
_bearer_scheme = HTTPBearer(auto_error=False, description="Supabase access token")

CredentialsDep = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)]


@lru_cache(maxsize=1)
def _cached_verifier(secret: str, audience: str, leeway_seconds: int) -> SupabaseJWTVerifier:
    return SupabaseJWTVerifier(secret=secret, audience=audience, leeway_seconds=leeway_seconds)


def get_verifier(settings: SettingsDep) -> SupabaseJWTVerifier:
    """The process-wide token verifier.

    Cached on its inputs rather than rebuilt per request: constructing it
    validates configuration, and doing that on every request would be waste.
    """
    return _cached_verifier(
        settings.supabase_jwt_secret.get_secret_value(),
        settings.supabase_jwt_audience,
        settings.supabase_jwt_leeway_seconds,
    )


VerifierDep = Annotated[SupabaseJWTVerifier, Depends(get_verifier)]


def get_current_user(
    credentials: CredentialsDep,
    verifier: VerifierDep,
) -> AuthenticatedUser:
    """Identify the caller from their bearer token, or reject the request.

    Raises:
        AuthenticationError: no credentials, or credentials that do not verify.
            Mapped to 401 in :mod:`app.api.errors`.
    """
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("Not authenticated")
    return verifier.verify(credentials.credentials)


CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]


def get_database(request: Request) -> Database:
    """The pool owned by the running application."""
    database: Database = request.app.state.database
    return database


DatabaseDep = Annotated[Database, Depends(get_database)]


def get_workspace_repository(database: DatabaseDep) -> WorkspaceRepository:
    return PostgresWorkspaceRepository(database.pool)


def get_profile_repository(database: DatabaseDep) -> ProfileRepository:
    return PostgresProfileRepository(database.pool)


def get_workspace_service(
    repository: Annotated[WorkspaceRepository, Depends(get_workspace_repository)],
) -> WorkspaceService:
    return WorkspaceService(repository)


def get_profile_service(
    repository: Annotated[ProfileRepository, Depends(get_profile_repository)],
) -> ProfileService:
    return ProfileService(repository)


WorkspaceServiceDep = Annotated[WorkspaceService, Depends(get_workspace_service)]
ProfileServiceDep = Annotated[ProfileService, Depends(get_profile_service)]


def get_http_client(request: Request) -> httpx.AsyncClient:
    """The shared outbound HTTP client owned by the application.

    One pooled client for the process rather than one per request: building a
    client per call throws away connection reuse and TLS session resumption,
    which is most of the cost of talking to Storage.
    """
    client: httpx.AsyncClient = request.app.state.http_client
    return client


HttpClientDep = Annotated[httpx.AsyncClient, Depends(get_http_client)]


def get_document_repository(database: DatabaseDep) -> DocumentRepository:
    return PostgresDocumentRepository(database.pool)


def get_document_storage(settings: SettingsDep, client: HttpClientDep) -> DocumentStorage:
    return SupabaseDocumentStorage(settings, client)


def get_document_service(
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    storage: Annotated[DocumentStorage, Depends(get_document_storage)],
    settings: SettingsDep,
) -> DocumentService:
    return DocumentService(repository, storage, max_document_bytes=settings.max_document_bytes)


DocumentServiceDep = Annotated[DocumentService, Depends(get_document_service)]


async def get_workspace_membership(
    workspace_id: Annotated[UUID, Path(description="Workspace being accessed")],
    user: CurrentUser,
    service: WorkspaceServiceDep,
) -> WorkspaceMembership:
    """Resolve the caller's membership of the ``workspace_id`` in the path.

    Any route that declares this dependency is workspace-scoped: it cannot run
    unless the caller is a member, and it receives the workspace and the
    caller's role without a second query.

    Raises:
        WorkspaceNotFoundError: not a member, or no such workspace -- 404.
    """
    return await service.require_membership(workspace_id, user.id)


#: Annotate a route parameter with this to require workspace membership.
#: ``async def route(membership: WorkspaceMember) -> ...``
WorkspaceMember = Annotated[WorkspaceMembership, Depends(get_workspace_membership)]


def require_workspace_role(
    minimum_role: WorkspaceRole,
) -> Callable[..., Awaitable[WorkspaceMembership]]:
    """Build a dependency requiring at least ``minimum_role`` in the workspace.

    For routes where membership alone is not enough::

        OwnerOnly = Annotated[
            WorkspaceMembership, Depends(require_workspace_role(WorkspaceRole.OWNER))
        ]

    Raises (from the returned dependency):
        WorkspaceNotFoundError: not a member -- 404.
        InsufficientWorkspaceRoleError: a member, but too junior -- 403.
    """

    async def dependency(
        workspace_id: Annotated[UUID, Path(description="Workspace being accessed")],
        user: CurrentUser,
        service: WorkspaceServiceDep,
    ) -> WorkspaceMembership:
        return await service.require_membership(workspace_id, user.id, minimum_role=minimum_role)

    return dependency


# --- Ingestion ---------------------------------------------------------------
#
# The parser registry, cleaner and tokenizer are built once for the process
# rather than per request. Loading a tokenizer encoding costs real time, and
# none of the three holds request state.


@lru_cache(maxsize=1)
def get_parser_registry() -> ParserRegistry:
    return build_default_registry()


@lru_cache(maxsize=1)
def get_text_cleaner() -> TextCleaner:
    return TextCleaner()


@lru_cache(maxsize=1)
def _cached_tokenizer(encoding_name: str) -> Tokenizer:
    return get_tokenizer(encoding_name)


def get_ingestion_tokenizer(settings: SettingsDep) -> Tokenizer:
    return _cached_tokenizer(settings.tokenizer_encoding)


def get_chunker_config(settings: SettingsDep) -> ChunkerConfig:
    return ChunkerConfig(
        chunk_size_tokens=settings.chunk_size_tokens,
        chunk_overlap_tokens=settings.chunk_overlap_tokens,
    )


def get_ingestion_job_repository(database: DatabaseDep) -> IngestionJobRepository:
    return PostgresIngestionJobRepository(database.pool)


def get_chunk_repository(database: DatabaseDep) -> ChunkRepository:
    return PostgresChunkRepository(database.pool)


def get_task_runner(request: Request) -> TaskRunner:
    """The application's background runner.

    Held on the app rather than created per request: work submitted during a
    request outlives it, and a runner that went out of scope with the request
    could not be drained at shutdown.
    """
    runner: TaskRunner = request.app.state.task_runner
    return runner


def get_ingestion_service(
    documents: Annotated[DocumentRepository, Depends(get_document_repository)],
    jobs: Annotated[IngestionJobRepository, Depends(get_ingestion_job_repository)],
    chunks: Annotated[ChunkRepository, Depends(get_chunk_repository)],
    storage: Annotated[DocumentStorage, Depends(get_document_storage)],
    parsers: Annotated[ParserRegistry, Depends(get_parser_registry)],
    cleaner: Annotated[TextCleaner, Depends(get_text_cleaner)],
    tokenizer: Annotated[Tokenizer, Depends(get_ingestion_tokenizer)],
    chunker_config: Annotated[ChunkerConfig, Depends(get_chunker_config)],
    embeddings: Annotated[EmbeddingProvider, Depends(get_embedding_provider)],
    graph: Annotated[ChunkGraph, Depends(get_chunk_graph)],
    runner: Annotated[TaskRunner, Depends(get_task_runner)],
) -> IngestionService:
    return IngestionService(
        documents=documents,
        jobs=jobs,
        chunks=chunks,
        storage=storage,
        parsers=parsers,
        cleaner=cleaner,
        tokenizer=tokenizer,
        chunker_config=chunker_config,
        embeddings=embeddings,
        graph=graph,
        runner=runner,
    )


IngestionServiceDep = Annotated[IngestionService, Depends(get_ingestion_service)]


def get_graph_database(request: Request) -> Neo4jDatabase:
    """The Neo4j driver owned by the running application."""
    database: Neo4jDatabase = request.app.state.graph_database
    return database


GraphDatabaseDep = Annotated[Neo4jDatabase, Depends(get_graph_database)]


def get_chunk_graph(database: GraphDatabaseDep) -> ChunkGraph:
    return Neo4jChunkGraph(database)


def get_embedding_provider(settings: SettingsDep, client: HttpClientDep) -> EmbeddingProvider:
    """The provider named by configuration.

    Built per request rather than cached, because it closes over the shared
    HTTP client and holds no state of its own. Construction validates that a
    credential is configured, so a missing key surfaces as an error here
    rather than as a failed document later.
    """
    return build_embedding_provider(settings, client)
