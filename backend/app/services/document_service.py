"""Document upload, listing and deletion.

Holds the two pieces of policy that route handlers must not own: what counts as
an acceptable upload, and the order in which a document's pieces are torn down.
"""

from __future__ import annotations

import logging
from typing import Final
from uuid import UUID, uuid4

from app.core.content_detection import (
    DocumentType,
    detect_document_type,
    sanitise_filename,
    sha256_hex,
)
from app.core.exceptions import (
    DocumentNotFoundError,
    DocumentTooLargeError,
    DuplicateDocumentError,
    EmptyDocumentError,
    UnsupportedDocumentTypeError,
)
from app.models.document import Document, NewDocument
from app.repositories.document_repository import DocumentRepository
from app.repositories.document_storage import DocumentStorage, build_storage_path

logger: Final = logging.getLogger(__name__)

#: Reported to the client when an upload is refused, so the message names what
#: is actually accepted rather than only what was rejected.
ACCEPTED_TYPES: Final[tuple[str, ...]] = tuple(
    document_type.value for document_type in DocumentType
)


class DocumentService:
    """Use cases over a workspace's documents.

    Every method takes ``workspace_id`` and passes it to the repository, which
    scopes its queries by it. The caller is expected to have authorised that
    workspace already -- the route dependency does -- so this is defence in
    depth rather than the primary check: even a route that forgot the
    dependency could not read another workspace's rows through this service.
    """

    def __init__(
        self,
        repository: DocumentRepository,
        storage: DocumentStorage,
        *,
        max_document_bytes: int,
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._max_document_bytes = max_document_bytes

    @property
    def max_document_bytes(self) -> int:
        """The upload limit this service enforces.

        Exposed so the HTTP layer can stop reading at the same number rather
        than keeping its own copy, which would eventually disagree.
        """
        return self._max_document_bytes

    async def upload(
        self,
        *,
        workspace_id: UUID,
        uploaded_by: UUID,
        filename: str,
        data: bytes,
    ) -> Document:
        """Validate, store and record an uploaded document.

        Raises:
            EmptyDocumentError: the upload had no bytes.
            DocumentTooLargeError: it exceeds the configured limit.
            UnsupportedDocumentTypeError: the *bytes* are not an accepted type.
            DuplicateDocumentError: identical content is already in the
                workspace.
            DocumentStorageError: Storage could not be written.
        """
        if not data:
            raise EmptyDocumentError("Document is empty")

        # Checked again here even though the route caps its read: the limit is a
        # policy of this service, and a second caller (a worker, a CLI) must not
        # be able to exceed it by not knowing about it.
        if len(data) > self._max_document_bytes:
            raise DocumentTooLargeError(self._max_document_bytes)

        safe_filename = sanitise_filename(filename)

        # The whole point of the phase: what this file *is*, decided by reading
        # it. The filename contributes only to the text/plain vs text/markdown
        # choice, which is cosmetic.
        document_type = detect_document_type(data, safe_filename)
        if document_type is None:
            raise UnsupportedDocumentTypeError(safe_filename, ACCEPTED_TYPES)

        checksum = sha256_hex(data)
        existing = await self._repository.find_by_checksum(workspace_id, checksum)
        if existing is not None:
            # Checked before writing so that a duplicate upload does not leave a
            # second copy of the bytes in the bucket. There is still a race
            # between two concurrent uploads of the same file; the unique
            # constraint on (workspace_id, checksum_sha256) is what actually
            # guarantees it, and this check is what makes the common case cheap
            # and the error message useful.
            raise DuplicateDocumentError(existing.id)

        document_id = uuid4()
        storage_path = build_storage_path(workspace_id, document_id, document_type.extension)

        await self._storage.upload(storage_path, data, document_type.value)

        try:
            return await self._repository.create(
                NewDocument(
                    id=document_id,
                    workspace_id=workspace_id,
                    uploaded_by=uploaded_by,
                    filename=safe_filename,
                    storage_path=storage_path,
                    mime_type=document_type.value,
                    size_bytes=len(data),
                    checksum_sha256=checksum,
                )
            )
        except Exception:
            # Bytes were written but the row was not. Without this the object
            # would be unreferenced and invisible -- nothing would ever know to
            # delete it. Compensating here keeps the failure clean; if the
            # compensation itself fails there is nothing better to do than say
            # so loudly.
            logger.exception(
                "Recording document %s failed; removing its storage object",
                document_id,
            )
            try:
                await self._storage.delete(storage_path)
            except Exception:
                logger.exception("Orphaned storage object %s could not be removed", storage_path)
            raise

    async def list_for_workspace(self, workspace_id: UUID) -> list[Document]:
        """Every document in the workspace, newest first."""
        return await self._repository.list_for_workspace(workspace_id)

    async def get(self, workspace_id: UUID, document_id: UUID) -> Document:
        """One document.

        Raises:
            DocumentNotFoundError: no such document *in this workspace*. A
                document that exists elsewhere is reported identically, because
                the query never leaves the workspace to find out.
        """
        document = await self._repository.get(workspace_id, document_id)
        if document is None:
            raise DocumentNotFoundError(document_id)
        return document

    async def delete(self, workspace_id: UUID, document_id: UUID) -> None:
        """Remove a document, its derived data and its bytes.

        Ordering is deliberate, working from the most-derived to the most
        authoritative:

        1. Derived data (chunks, embeddings, graph nodes) -- see
           :meth:`_purge_derived_data`.
        2. The storage object.
        3. The metadata row.

        The row goes last because it is the only record that the other two ever
        existed. Deleting it first and then failing would leave bytes in the
        bucket and nodes in the graph that nothing references and no retry can
        find. In the reverse order a failure leaves the document intact and the
        operation simply retryable, which is the failure mode to prefer.

        Raises:
            DocumentNotFoundError: not in this workspace.
            DocumentStorageError: the object could not be removed, in which case
                nothing is deleted and the call can be retried.
        """
        document = await self.get(workspace_id, document_id)

        await self._purge_derived_data(document)
        await self._storage.delete(document.storage_path)

        deleted = await self._repository.delete(workspace_id, document_id)
        if not deleted:  # pragma: no cover - only on a concurrent delete
            raise DocumentNotFoundError(document_id)

    async def _purge_derived_data(self, document: Document) -> None:
        """Remove everything derived from ``document``.

        Nothing is derived yet: ingestion lands in a later phase. The step
        exists now, in the right place in the ordering, so that adding graph and
        vector cleanup is a change to one method rather than a change to the
        shape of deletion.

        When ingestion exists this will delete the document's chunks and its
        graph contribution, scoped by ``workspace_id`` like every other graph
        access. Chunk rows in Postgres will cascade from the document row, so
        only Neo4j needs explicit work here.
        """
        logger.debug(
            "No derived data to purge for document %s (ingestion not implemented)",
            document.id,
        )
