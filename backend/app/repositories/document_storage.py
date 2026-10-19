"""Supabase Storage access for document bytes.

Uses the Storage REST API over httpx rather than the Supabase Python SDK: the
two calls needed here are a PUT and a DELETE, and going direct keeps the
credential handling and the failure modes visible instead of behind a client
that would have to be configured and mocked anyway.

**This client authenticates with the service-role key, which bypasses row-level
security entirely.** Nothing in this module decides who may touch an object; it
executes a decision already made. Every caller must have gone through
``WorkspaceService.require_membership`` first, and every key it is handed must
be one the service built from ids it verified.
"""

from __future__ import annotations

import logging
from typing import Final, Protocol
from uuid import UUID

import httpx

from app.core.config import Settings
from app.core.exceptions import DocumentStorageError

logger: Final = logging.getLogger(__name__)


def build_storage_path(workspace_id: UUID, document_id: UUID, extension: str) -> str:
    """Object key for a document.

    Every component is a UUID or a server-chosen extension derived from the
    *detected* content type. No part of the client's filename appears, so the
    key cannot traverse directories, collide across workspaces, or carry
    characters that mean something to a storage backend.

    The workspace prefix makes "everything belonging to this workspace" a single
    listable path, which is what a future workspace deletion will need.
    """
    return f"{workspace_id}/{document_id}.{extension}"


class DocumentStorage(Protocol):
    """Byte storage for documents."""

    async def upload(self, path: str, data: bytes, content_type: str) -> None:
        """Write ``data`` to ``path``, replacing anything already there."""
        ...

    async def download(self, path: str) -> bytes:
        """Read the object at ``path``.

        Raises:
            DocumentStorageError: it could not be read, including when it is
                not there. A missing object for a document that exists is a
                broken deployment, not an empty file.
        """
        ...

    async def delete(self, path: str) -> None:
        """Remove the object at ``path``.

        Idempotent: an object that is already gone is a success, because the
        caller's goal -- that nothing remains at this key -- is satisfied.
        """
        ...


class SupabaseDocumentStorage:
    """:class:`DocumentStorage` backed by the Supabase Storage REST API."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self._base_url = settings.supabase_url.rstrip("/")
        self._bucket = settings.documents_bucket
        self._service_key = settings.supabase_service_role_key.get_secret_value()
        self._client = client

    def _object_url(self, path: str) -> str:
        return f"{self._base_url}/storage/v1/object/{self._bucket}/{path}"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._service_key}",
            # Storage requires the apikey header in addition to the bearer token.
            "apikey": self._service_key,
        }

    async def upload(self, path: str, data: bytes, content_type: str) -> None:
        headers = self._headers()
        headers["Content-Type"] = content_type
        # Overwrite rather than fail on an existing key. The key is derived from
        # a freshly minted document id, so a collision means a retry of the same
        # upload, and the second attempt should converge rather than wedge.
        headers["x-upsert"] = "true"

        try:
            response = await self._client.put(self._object_url(path), content=data, headers=headers)
        except httpx.HTTPError as exc:
            logger.exception("Storage upload to %s failed", path)
            raise DocumentStorageError("upload") from exc

        if response.status_code >= 400:
            # The body can echo the service key back in an error envelope, so
            # only the status is logged.
            logger.error("Storage upload to %s rejected with %s", path, response.status_code)
            raise DocumentStorageError("upload")

    async def download(self, path: str) -> bytes:
        try:
            response = await self._client.get(self._object_url(path), headers=self._headers())
        except httpx.HTTPError as exc:
            logger.exception("Storage download of %s failed", path)
            raise DocumentStorageError("download") from exc

        if response.status_code >= 400:
            logger.error("Storage download of %s rejected with %s", path, response.status_code)
            raise DocumentStorageError("download")

        return response.content

    async def delete(self, path: str) -> None:
        try:
            response = await self._client.delete(self._object_url(path), headers=self._headers())
        except httpx.HTTPError as exc:
            logger.exception("Storage delete of %s failed", path)
            raise DocumentStorageError("delete") from exc

        if response.status_code == httpx.codes.NOT_FOUND:
            # Already absent. The desired end state holds, so this is not a
            # failure -- and treating it as one would make a retried delete
            # impossible to complete.
            logger.info("Storage object %s was already absent", path)
            return

        if response.status_code >= 400:
            logger.error("Storage delete of %s rejected with %s", path, response.status_code)
            raise DocumentStorageError("delete")
