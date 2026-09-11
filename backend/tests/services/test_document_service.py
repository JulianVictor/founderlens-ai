"""Document upload and deletion orchestration."""

from __future__ import annotations

import pytest

from app.core.content_detection import sha256_hex
from app.core.exceptions import (
    DocumentNotFoundError,
    DocumentStorageError,
    DocumentTooLargeError,
    DuplicateDocumentError,
    EmptyDocumentError,
    UnsupportedDocumentTypeError,
)
from app.models.document import DocumentStatus
from app.services.document_service import DocumentService
from tests.conftest import (
    DOCUMENT_ID,
    MARKDOWN_BYTES,
    OTHER_DOCUMENT_ID,
    OTHER_WORKSPACE_ID,
    OWNER_ID,
    PDF_BYTES,
    PNG_BYTES,
    TEXT_BYTES,
    UNKNOWN_DOCUMENT_ID,
    WORKSPACE_ID,
    FakeDocumentRepository,
    FakeDocumentStorage,
)

MAX_BYTES = 1024 * 1024


@pytest.fixture
def service(
    document_repository: FakeDocumentRepository, document_storage: FakeDocumentStorage
) -> DocumentService:
    return DocumentService(document_repository, document_storage, max_document_bytes=MAX_BYTES)


class TestUploadMetadata:
    async def test_records_the_detected_type_not_the_claimed_one(
        self, service: DocumentService
    ) -> None:
        document = await service.upload(
            workspace_id=WORKSPACE_ID,
            uploaded_by=OWNER_ID,
            filename="looks-like-text.txt",
            data=PDF_BYTES,
        )

        assert document.mime_type == "application/pdf"

    async def test_records_size_and_checksum_from_the_content(
        self, service: DocumentService
    ) -> None:
        document = await service.upload(
            workspace_id=WORKSPACE_ID,
            uploaded_by=OWNER_ID,
            filename="notes.txt",
            data=TEXT_BYTES,
        )

        assert document.size_bytes == len(TEXT_BYTES)
        assert document.checksum_sha256 == sha256_hex(TEXT_BYTES)

    async def test_new_documents_start_as_uploaded(self, service: DocumentService) -> None:
        """Nothing is derived yet, and the status must not claim otherwise."""
        document = await service.upload(
            workspace_id=WORKSPACE_ID,
            uploaded_by=OWNER_ID,
            filename="notes.txt",
            data=TEXT_BYTES,
        )

        assert document.status is DocumentStatus.UPLOADED
        assert document.error_message is None

    async def test_records_the_uploader_and_the_workspace(self, service: DocumentService) -> None:
        document = await service.upload(
            workspace_id=WORKSPACE_ID,
            uploaded_by=OWNER_ID,
            filename="notes.md",
            data=MARKDOWN_BYTES,
        )

        assert document.workspace_id == WORKSPACE_ID
        assert document.uploaded_by == OWNER_ID

    async def test_storage_key_contains_no_user_supplied_text(
        self, service: DocumentService, document_storage: FakeDocumentStorage
    ) -> None:
        document = await service.upload(
            workspace_id=WORKSPACE_ID,
            uploaded_by=OWNER_ID,
            filename="../../etc/passwd.txt",
            data=TEXT_BYTES,
        )

        assert document.storage_path == f"{WORKSPACE_ID}/{document.id}.txt"
        assert ".." not in document.storage_path
        assert "passwd" not in document.storage_path
        assert document.storage_path in document_storage.objects

    async def test_filename_is_sanitised_but_preserved_for_display(
        self, service: DocumentService
    ) -> None:
        document = await service.upload(
            workspace_id=WORKSPACE_ID,
            uploaded_by=OWNER_ID,
            filename="../../etc/passwd.txt",
            data=TEXT_BYTES,
        )

        assert document.filename == "passwd.txt"

    async def test_bytes_reach_storage_unmodified(
        self, service: DocumentService, document_storage: FakeDocumentStorage
    ) -> None:
        document = await service.upload(
            workspace_id=WORKSPACE_ID,
            uploaded_by=OWNER_ID,
            filename="memo.pdf",
            data=PDF_BYTES,
        )

        assert document_storage.objects[document.storage_path] == PDF_BYTES


class TestUploadValidation:
    async def test_empty_upload_is_refused(self, service: DocumentService) -> None:
        with pytest.raises(EmptyDocumentError):
            await service.upload(
                workspace_id=WORKSPACE_ID,
                uploaded_by=OWNER_ID,
                filename="empty.txt",
                data=b"",
            )

    async def test_oversized_upload_is_refused(self, service: DocumentService) -> None:
        with pytest.raises(DocumentTooLargeError):
            await service.upload(
                workspace_id=WORKSPACE_ID,
                uploaded_by=OWNER_ID,
                filename="big.txt",
                data=b"a" * (MAX_BYTES + 1),
            )

    async def test_a_document_at_exactly_the_limit_is_accepted(
        self, service: DocumentService
    ) -> None:
        document = await service.upload(
            workspace_id=WORKSPACE_ID,
            uploaded_by=OWNER_ID,
            filename="exact.txt",
            data=b"a" * MAX_BYTES,
        )

        assert document.size_bytes == MAX_BYTES

    async def test_unsupported_type_is_refused(self, service: DocumentService) -> None:
        with pytest.raises(UnsupportedDocumentTypeError):
            await service.upload(
                workspace_id=WORKSPACE_ID,
                uploaded_by=OWNER_ID,
                filename="image.png",
                data=PNG_BYTES,
            )

    async def test_a_lying_extension_does_not_get_a_file_in(self, service: DocumentService) -> None:
        with pytest.raises(UnsupportedDocumentTypeError):
            await service.upload(
                workspace_id=WORKSPACE_ID,
                uploaded_by=OWNER_ID,
                filename="quarterly-report.pdf",
                data=PNG_BYTES,
            )

    async def test_a_refused_upload_writes_nothing_to_storage(
        self, service: DocumentService, document_storage: FakeDocumentStorage
    ) -> None:
        """Validation happens before the write, so a rejected file leaves no
        bytes behind to be cleaned up."""
        with pytest.raises(UnsupportedDocumentTypeError):
            await service.upload(
                workspace_id=WORKSPACE_ID,
                uploaded_by=OWNER_ID,
                filename="bad.pdf",
                data=PNG_BYTES,
            )

        assert document_storage.objects == {}

    async def test_identical_content_is_refused_as_a_duplicate(
        self, service: DocumentService
    ) -> None:
        first = await service.upload(
            workspace_id=WORKSPACE_ID,
            uploaded_by=OWNER_ID,
            filename="notes.txt",
            data=TEXT_BYTES,
        )

        with pytest.raises(DuplicateDocumentError) as caught:
            await service.upload(
                workspace_id=WORKSPACE_ID,
                uploaded_by=OWNER_ID,
                filename="a-different-name.txt",
                data=TEXT_BYTES,
            )

        # The caller is told which document it already is, so a client can point
        # at it rather than treating the upload as simply failed.
        assert caught.value.existing_document_id == first.id

    async def test_the_same_content_may_exist_in_two_workspaces(
        self, service: DocumentService
    ) -> None:
        """Dedup is per workspace. Two teams researching the same company must
        both be able to hold the same filing."""
        await service.upload(
            workspace_id=WORKSPACE_ID,
            uploaded_by=OWNER_ID,
            filename="notes.txt",
            data=TEXT_BYTES,
        )
        other = await service.upload(
            workspace_id=OTHER_WORKSPACE_ID,
            uploaded_by=OWNER_ID,
            filename="notes.txt",
            data=TEXT_BYTES,
        )

        assert other.workspace_id == OTHER_WORKSPACE_ID


class TestUploadFailureHandling:
    async def test_storage_failure_records_nothing(
        self,
        service: DocumentService,
        document_storage: FakeDocumentStorage,
        document_repository: FakeDocumentRepository,
    ) -> None:
        document_storage.fail_upload = True
        before = len(document_repository.documents)

        with pytest.raises(DocumentStorageError):
            await service.upload(
                workspace_id=WORKSPACE_ID,
                uploaded_by=OWNER_ID,
                filename="notes.txt",
                data=TEXT_BYTES,
            )

        assert len(document_repository.documents) == before

    async def test_a_failed_insert_removes_the_orphaned_object(
        self,
        service: DocumentService,
        document_storage: FakeDocumentStorage,
        document_repository: FakeDocumentRepository,
    ) -> None:
        """Bytes written, row not. Without compensation the object would be
        unreferenced -- nothing would ever know it existed to delete it."""
        document_repository.fail_create = True

        with pytest.raises(RuntimeError):
            await service.upload(
                workspace_id=WORKSPACE_ID,
                uploaded_by=OWNER_ID,
                filename="notes.txt",
                data=TEXT_BYTES,
            )

        assert document_storage.objects == {}
        assert len(document_storage.deleted) == 1


class TestReads:
    async def test_lists_only_the_workspaces_documents(self, service: DocumentService) -> None:
        found = await service.list_for_workspace(WORKSPACE_ID)

        assert [d.id for d in found] == [DOCUMENT_ID]

    async def test_gets_a_document_in_the_workspace(self, service: DocumentService) -> None:
        document = await service.get(WORKSPACE_ID, DOCUMENT_ID)

        assert document.filename == "acme-memo.pdf"

    async def test_a_document_from_another_workspace_is_not_found(
        self, service: DocumentService
    ) -> None:
        """The id is real and the document exists -- but not here."""
        with pytest.raises(DocumentNotFoundError):
            await service.get(WORKSPACE_ID, OTHER_DOCUMENT_ID)

    async def test_an_unknown_id_raises_the_same_error(self, service: DocumentService) -> None:
        with pytest.raises(DocumentNotFoundError):
            await service.get(WORKSPACE_ID, UNKNOWN_DOCUMENT_ID)


class TestDelete:
    async def test_removes_the_row_and_the_object(
        self,
        service: DocumentService,
        document_repository: FakeDocumentRepository,
        document_storage: FakeDocumentStorage,
    ) -> None:
        await service.delete(WORKSPACE_ID, DOCUMENT_ID)

        assert await document_repository.get(WORKSPACE_ID, DOCUMENT_ID) is None
        assert document_storage.deleted == [f"{WORKSPACE_ID}/{DOCUMENT_ID}.pdf"]

    async def test_cannot_delete_another_workspaces_document(
        self,
        service: DocumentService,
        document_repository: FakeDocumentRepository,
        document_storage: FakeDocumentStorage,
    ) -> None:
        """Guessing a valid id from another workspace must not destroy it."""
        with pytest.raises(DocumentNotFoundError):
            await service.delete(WORKSPACE_ID, OTHER_DOCUMENT_ID)

        assert await document_repository.get(OTHER_WORKSPACE_ID, OTHER_DOCUMENT_ID) is not None
        assert document_storage.deleted == []

    async def test_a_storage_failure_leaves_the_document_intact(
        self,
        service: DocumentService,
        document_repository: FakeDocumentRepository,
        document_storage: FakeDocumentStorage,
    ) -> None:
        """The row is deleted last on purpose: it is the only record that the
        object ever existed, so losing it first would strand the bytes."""
        document_storage.fail_delete = True

        with pytest.raises(DocumentStorageError):
            await service.delete(WORKSPACE_ID, DOCUMENT_ID)

        assert await document_repository.get(WORKSPACE_ID, DOCUMENT_ID) is not None

    async def test_deleting_an_unknown_document_is_not_found(
        self, service: DocumentService
    ) -> None:
        with pytest.raises(DocumentNotFoundError):
            await service.delete(WORKSPACE_ID, UNKNOWN_DOCUMENT_ID)
