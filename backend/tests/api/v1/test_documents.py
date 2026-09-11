"""Document endpoints over HTTP, including cross-workspace isolation."""

from __future__ import annotations

from collections.abc import Callable

from httpx import AsyncClient

from tests.conftest import (
    DOCUMENT_ID,
    MARKDOWN_BYTES,
    MAX_DOCUMENT_BYTES,
    MEMBER_ID,
    OTHER_DOCUMENT_ID,
    OTHER_WORKSPACE_ID,
    OUTSIDER_ID,
    OWNER_ID,
    PDF_BYTES,
    PNG_BYTES,
    TEXT_BYTES,
    UNKNOWN_DOCUMENT_ID,
    WORKSPACE_ID,
    FakeDocumentRepository,
    FakeDocumentStorage,
)

Headers = Callable[..., dict[str, str]]

DOCUMENTS_URL = f"/api/v1/workspaces/{WORKSPACE_ID}/documents"
OTHER_DOCUMENTS_URL = f"/api/v1/workspaces/{OTHER_WORKSPACE_ID}/documents"


def upload_file(
    content: bytes, filename: str = "notes.txt", content_type: str = "text/plain"
) -> dict[str, tuple[str, bytes, str]]:
    return {"file": (filename, content, content_type)}


class TestUpload:
    async def test_accepts_a_pdf(self, client: AsyncClient, auth_headers: Headers) -> None:
        response = await client.post(
            DOCUMENTS_URL,
            files=upload_file(PDF_BYTES, "memo.pdf", "application/pdf"),
            headers=auth_headers(OWNER_ID),
        )

        assert response.status_code == 201
        body = response.json()
        assert body["filename"] == "memo.pdf"
        assert body["mime_type"] == "application/pdf"
        assert body["size_bytes"] == len(PDF_BYTES)
        assert body["status"] == "uploaded"
        assert body["workspace_id"] == str(WORKSPACE_ID)
        assert body["uploaded_by"] == str(OWNER_ID)

    async def test_accepts_markdown(self, client: AsyncClient, auth_headers: Headers) -> None:
        response = await client.post(
            DOCUMENTS_URL,
            files=upload_file(MARKDOWN_BYTES, "notes.md", "text/markdown"),
            headers=auth_headers(OWNER_ID),
        )

        assert response.status_code == 201
        assert response.json()["mime_type"] == "text/markdown"

    async def test_a_plain_member_may_upload(
        self, client: AsyncClient, auth_headers: Headers
    ) -> None:
        """Uploading is not an owner-only action: the workspace owns its corpus."""
        response = await client.post(
            DOCUMENTS_URL,
            files=upload_file(TEXT_BYTES),
            headers=auth_headers(MEMBER_ID),
        )

        assert response.status_code == 201
        assert response.json()["uploaded_by"] == str(MEMBER_ID)

    async def test_response_hides_internal_fields(
        self, client: AsyncClient, auth_headers: Headers
    ) -> None:
        """The storage key must not leak: publishing it invites clients to
        address the bucket directly, which is where the membership check is not."""
        response = await client.post(
            DOCUMENTS_URL,
            files=upload_file(TEXT_BYTES),
            headers=auth_headers(OWNER_ID),
        )

        body = response.json()
        assert "storage_path" not in body
        assert "checksum_sha256" not in body


class TestUploadValidation:
    async def test_rejects_an_unsupported_type_with_415(
        self, client: AsyncClient, auth_headers: Headers
    ) -> None:
        response = await client.post(
            DOCUMENTS_URL,
            files=upload_file(PNG_BYTES, "image.png", "image/png"),
            headers=auth_headers(OWNER_ID),
        )

        assert response.status_code == 415
        assert "application/pdf" in response.json()["detail"]

    async def test_a_declared_content_type_does_not_override_the_bytes(
        self, client: AsyncClient, auth_headers: Headers
    ) -> None:
        """The multipart Content-Type is a claim by the client.

        Here it says application/pdf and the filename agrees -- and the upload
        is still refused, because the bytes are a PNG.
        """
        response = await client.post(
            DOCUMENTS_URL,
            files=upload_file(PNG_BYTES, "report.pdf", "application/pdf"),
            headers=auth_headers(OWNER_ID),
        )

        assert response.status_code == 415

    async def test_rejects_an_empty_file_with_400(
        self, client: AsyncClient, auth_headers: Headers
    ) -> None:
        response = await client.post(
            DOCUMENTS_URL,
            files=upload_file(b"", "empty.txt"),
            headers=auth_headers(OWNER_ID),
        )

        assert response.status_code == 400

    async def test_rejects_an_oversized_file_with_413(
        self, client: AsyncClient, auth_headers: Headers
    ) -> None:
        response = await client.post(
            DOCUMENTS_URL,
            files=upload_file(b"a" * (MAX_DOCUMENT_BYTES + 1), "big.txt"),
            headers=auth_headers(OWNER_ID),
        )

        assert response.status_code == 413

    async def test_a_file_at_exactly_the_limit_is_accepted(
        self, client: AsyncClient, auth_headers: Headers
    ) -> None:
        """Off-by-one at the boundary would reject legitimate uploads."""
        response = await client.post(
            DOCUMENTS_URL,
            files=upload_file(b"a" * MAX_DOCUMENT_BYTES, "exact.txt"),
            headers=auth_headers(OWNER_ID),
        )

        assert response.status_code == 201

    async def test_an_oversized_upload_is_not_stored(
        self,
        client: AsyncClient,
        auth_headers: Headers,
        document_storage: FakeDocumentStorage,
    ) -> None:
        """The read is capped, so the body is refused partway through rather
        than after the whole file has been buffered."""
        await client.post(
            DOCUMENTS_URL,
            files=upload_file(b"a" * (MAX_DOCUMENT_BYTES * 4), "big.txt"),
            headers=auth_headers(OWNER_ID),
        )

        assert document_storage.objects == {}

    async def test_missing_file_part_is_422(
        self, client: AsyncClient, auth_headers: Headers
    ) -> None:
        response = await client.post(DOCUMENTS_URL, headers=auth_headers(OWNER_ID))

        assert response.status_code == 422

    async def test_duplicate_content_is_409_naming_the_existing_document(
        self, client: AsyncClient, auth_headers: Headers
    ) -> None:
        first = await client.post(
            DOCUMENTS_URL,
            files=upload_file(TEXT_BYTES),
            headers=auth_headers(OWNER_ID),
        )
        second = await client.post(
            DOCUMENTS_URL,
            files=upload_file(TEXT_BYTES, "renamed.txt"),
            headers=auth_headers(OWNER_ID),
        )

        assert second.status_code == 409
        assert second.json()["existing_document_id"] == first.json()["id"]


class TestList:
    async def test_lists_the_workspaces_documents(
        self, client: AsyncClient, auth_headers: Headers
    ) -> None:
        response = await client.get(DOCUMENTS_URL, headers=auth_headers(OWNER_ID))

        assert response.status_code == 200
        assert [d["id"] for d in response.json()["documents"]] == [str(DOCUMENT_ID)]

    async def test_does_not_leak_another_workspaces_documents(
        self, client: AsyncClient, auth_headers: Headers
    ) -> None:
        response = await client.get(DOCUMENTS_URL, headers=auth_headers(OWNER_ID))

        ids = [d["id"] for d in response.json()["documents"]]
        assert str(OTHER_DOCUMENT_ID) not in ids

    async def test_a_non_member_cannot_list(
        self, client: AsyncClient, auth_headers: Headers
    ) -> None:
        """404, not 403: a stranger must not learn the workspace exists."""
        response = await client.get(DOCUMENTS_URL, headers=auth_headers(OUTSIDER_ID))

        assert response.status_code == 404

    async def test_an_empty_workspace_lists_nothing_rather_than_erroring(
        self, client: AsyncClient, auth_headers: Headers
    ) -> None:
        response = await client.get(OTHER_DOCUMENTS_URL, headers=auth_headers(OUTSIDER_ID))

        assert response.status_code == 200
        assert [d["id"] for d in response.json()["documents"]] == [str(OTHER_DOCUMENT_ID)]


class TestGet:
    async def test_fetches_a_document_in_the_workspace(
        self, client: AsyncClient, auth_headers: Headers
    ) -> None:
        response = await client.get(
            f"{DOCUMENTS_URL}/{DOCUMENT_ID}", headers=auth_headers(OWNER_ID)
        )

        assert response.status_code == 200
        assert response.json()["filename"] == "acme-memo.pdf"

    async def test_a_guessed_id_from_another_workspace_is_404(
        self, client: AsyncClient, auth_headers: Headers
    ) -> None:
        """The central isolation guarantee.

        OTHER_DOCUMENT_ID is a real, existing document -- the caller simply has
        no right to it, and must not be able to tell it apart from a fiction.
        """
        real_elsewhere = await client.get(
            f"{DOCUMENTS_URL}/{OTHER_DOCUMENT_ID}", headers=auth_headers(OWNER_ID)
        )
        pure_fiction = await client.get(
            f"{DOCUMENTS_URL}/{UNKNOWN_DOCUMENT_ID}", headers=auth_headers(OWNER_ID)
        )

        assert real_elsewhere.status_code == 404
        assert pure_fiction.status_code == 404
        assert real_elsewhere.json() == pure_fiction.json()

    async def test_cannot_reach_a_document_by_pairing_it_with_its_own_workspace(
        self, client: AsyncClient, auth_headers: Headers
    ) -> None:
        """Naming the correct workspace does not help if you are not in it."""
        response = await client.get(
            f"{OTHER_DOCUMENTS_URL}/{OTHER_DOCUMENT_ID}",
            headers=auth_headers(OWNER_ID),
        )

        assert response.status_code == 404

    async def test_malformed_document_id_is_422(
        self, client: AsyncClient, auth_headers: Headers
    ) -> None:
        response = await client.get(f"{DOCUMENTS_URL}/not-a-uuid", headers=auth_headers(OWNER_ID))

        assert response.status_code == 422


class TestDelete:
    async def test_deletes_the_document_and_its_object(
        self,
        client: AsyncClient,
        auth_headers: Headers,
        document_storage: FakeDocumentStorage,
    ) -> None:
        response = await client.delete(
            f"{DOCUMENTS_URL}/{DOCUMENT_ID}", headers=auth_headers(OWNER_ID)
        )

        assert response.status_code == 204
        assert document_storage.deleted == [f"{WORKSPACE_ID}/{DOCUMENT_ID}.pdf"]

        after = await client.get(f"{DOCUMENTS_URL}/{DOCUMENT_ID}", headers=auth_headers(OWNER_ID))
        assert after.status_code == 404

    async def test_a_plain_member_may_delete(
        self, client: AsyncClient, auth_headers: Headers
    ) -> None:
        response = await client.delete(
            f"{DOCUMENTS_URL}/{DOCUMENT_ID}", headers=auth_headers(MEMBER_ID)
        )

        assert response.status_code == 204

    async def test_cannot_delete_another_workspaces_document(
        self,
        client: AsyncClient,
        auth_headers: Headers,
        document_repository: FakeDocumentRepository,
        document_storage: FakeDocumentStorage,
    ) -> None:
        """A guessed id must not let one workspace destroy another's data."""
        response = await client.delete(
            f"{DOCUMENTS_URL}/{OTHER_DOCUMENT_ID}", headers=auth_headers(OWNER_ID)
        )

        assert response.status_code == 404
        assert await document_repository.get(OTHER_WORKSPACE_ID, OTHER_DOCUMENT_ID) is not None
        assert document_storage.deleted == []

    async def test_a_non_member_cannot_delete(
        self,
        client: AsyncClient,
        auth_headers: Headers,
        document_repository: FakeDocumentRepository,
    ) -> None:
        response = await client.delete(
            f"{DOCUMENTS_URL}/{DOCUMENT_ID}", headers=auth_headers(OUTSIDER_ID)
        )

        assert response.status_code == 404
        assert await document_repository.get(WORKSPACE_ID, DOCUMENT_ID) is not None


class TestWorkspaceIsolation:
    async def test_every_document_route_refuses_a_non_member(
        self, client: AsyncClient, auth_headers: Headers
    ) -> None:
        """One assertion per verb, so a new route cannot be the one that leaks."""
        headers = auth_headers(OUTSIDER_ID)

        assert (await client.get(DOCUMENTS_URL, headers=headers)).status_code == 404
        assert (
            await client.get(f"{DOCUMENTS_URL}/{DOCUMENT_ID}", headers=headers)
        ).status_code == 404
        assert (
            await client.delete(f"{DOCUMENTS_URL}/{DOCUMENT_ID}", headers=headers)
        ).status_code == 404
        assert (
            await client.post(DOCUMENTS_URL, files=upload_file(TEXT_BYTES), headers=headers)
        ).status_code == 404

    async def test_a_non_member_upload_writes_nothing(
        self,
        client: AsyncClient,
        auth_headers: Headers,
        document_storage: FakeDocumentStorage,
        document_repository: FakeDocumentRepository,
    ) -> None:
        """Membership is checked by the route dependency, before the body is
        read, so a refused upload never reaches storage."""
        before = len(document_repository.documents)

        await client.post(
            DOCUMENTS_URL,
            files=upload_file(TEXT_BYTES),
            headers=auth_headers(OUTSIDER_ID),
        )

        assert document_storage.objects == {}
        assert len(document_repository.documents) == before
