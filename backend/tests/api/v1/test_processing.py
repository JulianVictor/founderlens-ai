"""The process and processing-status endpoints."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from httpx import AsyncClient

from tests.conftest import (
    DOCUMENT_ID,
    MEMBER_ID,
    OTHER_DOCUMENT_ID,
    OTHER_WORKSPACE_ID,
    OUTSIDER_ID,
    OWNER_ID,
    UNKNOWN_DOCUMENT_ID,
    WORKSPACE_ID,
    DeferredTaskRunner,
    FakeChunkRepository,
    FakeDocumentRepository,
    FakeDocumentStorage,
)

Headers = Callable[..., dict[str, str]]

BASE = f"/api/v1/workspaces/{WORKSPACE_ID}/documents"
PROCESS = f"{BASE}/{DOCUMENT_ID}/process"
STATUS = f"{BASE}/{DOCUMENT_ID}/processing-status"

TEXT_DOCUMENT = (
    b"Acme Robotics builds warehouse automation hardware.\n\n"
    b"The Series A closed in March 2026, led by Initech Ventures.\n"
)


def seed_text_document(documents: FakeDocumentRepository, storage: FakeDocumentStorage) -> None:
    """Make the seeded document a plain-text file with bytes in storage."""
    document = documents.documents[0]
    documents.documents[0] = replace(document, mime_type="text/plain")
    storage.objects[document.storage_path] = TEXT_DOCUMENT


class TestProcessEndpoint:
    async def test_accepts_the_request_with_202(
        self,
        client: AsyncClient,
        auth_headers: Headers,
        document_repository: FakeDocumentRepository,
        document_storage: FakeDocumentStorage,
    ) -> None:
        seed_text_document(document_repository, document_storage)

        response = await client.post(PROCESS, headers=auth_headers(OWNER_ID))

        assert response.status_code == 202
        body = response.json()
        assert body["document_id"] == str(DOCUMENT_ID)
        assert body["status"] == "queued"
        assert body["current_stage"] == "upload"
        assert body["attempt"] == 1

    async def test_returns_before_the_work_is_done(
        self,
        client: AsyncClient,
        auth_headers: Headers,
        document_repository: FakeDocumentRepository,
        document_storage: FakeDocumentStorage,
        chunk_repository: FakeChunkRepository,
    ) -> None:
        """202 means accepted, not finished."""
        seed_text_document(document_repository, document_storage)

        await client.post(PROCESS, headers=auth_headers(OWNER_ID))

        assert chunk_repository.replace_calls == 0

    async def test_a_second_request_returns_the_same_job_with_200(
        self,
        client: AsyncClient,
        auth_headers: Headers,
        document_repository: FakeDocumentRepository,
        document_storage: FakeDocumentStorage,
    ) -> None:
        """Idempotent: a double-click gets the run already under way, and the
        status code says it was not a new one."""
        seed_text_document(document_repository, document_storage)

        first = await client.post(PROCESS, headers=auth_headers(OWNER_ID))
        second = await client.post(PROCESS, headers=auth_headers(OWNER_ID))

        assert first.status_code == 202
        assert second.status_code == 200
        assert second.json()["id"] == first.json()["id"]

    async def test_a_plain_member_may_process(
        self,
        client: AsyncClient,
        auth_headers: Headers,
        document_repository: FakeDocumentRepository,
        document_storage: FakeDocumentStorage,
    ) -> None:
        seed_text_document(document_repository, document_storage)

        response = await client.post(PROCESS, headers=auth_headers(MEMBER_ID))

        assert response.status_code == 202

    async def test_a_non_member_cannot_process(
        self,
        client: AsyncClient,
        auth_headers: Headers,
        task_runner: DeferredTaskRunner,
    ) -> None:
        response = await client.post(PROCESS, headers=auth_headers(OUTSIDER_ID))

        assert response.status_code == 404
        assert task_runner.submitted == []

    async def test_cannot_process_another_workspaces_document(
        self,
        client: AsyncClient,
        auth_headers: Headers,
        task_runner: DeferredTaskRunner,
    ) -> None:
        """A real document id, guessed, from a workspace the caller is not in."""
        response = await client.post(
            f"{BASE}/{OTHER_DOCUMENT_ID}/process", headers=auth_headers(OWNER_ID)
        )

        assert response.status_code == 404
        assert task_runner.submitted == []

    async def test_an_unknown_document_is_404(
        self, client: AsyncClient, auth_headers: Headers
    ) -> None:
        response = await client.post(
            f"{BASE}/{UNKNOWN_DOCUMENT_ID}/process", headers=auth_headers(OWNER_ID)
        )

        assert response.status_code == 404

    async def test_a_guessed_id_is_indistinguishable_from_a_fictional_one(
        self, client: AsyncClient, auth_headers: Headers
    ) -> None:
        real_elsewhere = await client.post(
            f"{BASE}/{OTHER_DOCUMENT_ID}/process", headers=auth_headers(OWNER_ID)
        )
        fictional = await client.post(
            f"{BASE}/{UNKNOWN_DOCUMENT_ID}/process", headers=auth_headers(OWNER_ID)
        )

        assert real_elsewhere.json() == fictional.json()

    async def test_malformed_document_id_is_422(
        self, client: AsyncClient, auth_headers: Headers
    ) -> None:
        response = await client.post(f"{BASE}/not-a-uuid/process", headers=auth_headers(OWNER_ID))

        assert response.status_code == 422


class TestProcessingStatusEndpoint:
    async def test_reports_an_unprocessed_document(
        self, client: AsyncClient, auth_headers: Headers
    ) -> None:
        response = await client.get(STATUS, headers=auth_headers(OWNER_ID))

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "uploaded"
        assert body["job"] is None
        assert body["chunk_count"] == 0

    async def test_reports_progress_while_queued(
        self,
        client: AsyncClient,
        auth_headers: Headers,
        document_repository: FakeDocumentRepository,
        document_storage: FakeDocumentStorage,
    ) -> None:
        seed_text_document(document_repository, document_storage)
        await client.post(PROCESS, headers=auth_headers(OWNER_ID))

        body = (await client.get(STATUS, headers=auth_headers(OWNER_ID))).json()

        assert body["job"]["status"] == "queued"
        assert body["status"] == "uploaded"

    async def test_reports_success_and_the_chunk_count(
        self,
        client: AsyncClient,
        auth_headers: Headers,
        document_repository: FakeDocumentRepository,
        document_storage: FakeDocumentStorage,
        task_runner: DeferredTaskRunner,
    ) -> None:
        seed_text_document(document_repository, document_storage)
        await client.post(PROCESS, headers=auth_headers(OWNER_ID))
        await task_runner.run_all()

        body = (await client.get(STATUS, headers=auth_headers(OWNER_ID))).json()

        assert body["status"] == "ready"
        assert body["job"]["status"] == "succeeded"
        assert body["job"]["current_stage"] == "persist"
        assert body["chunk_count"] > 0
        assert body["job"]["chunk_count"] == body["chunk_count"]

    async def test_reports_failure_with_a_reason(
        self,
        client: AsyncClient,
        auth_headers: Headers,
        document_repository: FakeDocumentRepository,
        document_storage: FakeDocumentStorage,
        task_runner: DeferredTaskRunner,
    ) -> None:
        document = document_repository.documents[0]
        document_storage.objects[document.storage_path] = b"not a pdf at all"

        await client.post(PROCESS, headers=auth_headers(OWNER_ID))
        await task_runner.run_all()

        body = (await client.get(STATUS, headers=auth_headers(OWNER_ID))).json()

        assert body["status"] == "failed"
        assert body["job"]["status"] == "failed"
        assert body["job"]["current_stage"] == "extract"
        assert body["error_message"]

    async def test_reports_the_attempt_number_after_a_retry(
        self,
        client: AsyncClient,
        auth_headers: Headers,
        document_repository: FakeDocumentRepository,
        document_storage: FakeDocumentStorage,
        task_runner: DeferredTaskRunner,
    ) -> None:
        document = document_repository.documents[0]
        document_storage.objects[document.storage_path] = b"not a pdf at all"

        await client.post(PROCESS, headers=auth_headers(OWNER_ID))
        await task_runner.run_all()
        await client.post(PROCESS, headers=auth_headers(OWNER_ID))

        body = (await client.get(STATUS, headers=auth_headers(OWNER_ID))).json()

        assert body["job"]["attempt"] == 2

    async def test_a_non_member_cannot_see_status(
        self, client: AsyncClient, auth_headers: Headers
    ) -> None:
        response = await client.get(STATUS, headers=auth_headers(OUTSIDER_ID))

        assert response.status_code == 404

    async def test_cannot_read_status_of_another_workspaces_document(
        self, client: AsyncClient, auth_headers: Headers
    ) -> None:
        response = await client.get(
            f"{BASE}/{OTHER_DOCUMENT_ID}/processing-status",
            headers=auth_headers(OWNER_ID),
        )

        assert response.status_code == 404

    async def test_status_is_scoped_to_the_workspace_in_the_path(
        self, client: AsyncClient, auth_headers: Headers
    ) -> None:
        """Naming the correct workspace does not help a caller outside it."""
        response = await client.get(
            f"/api/v1/workspaces/{OTHER_WORKSPACE_ID}/documents"
            f"/{OTHER_DOCUMENT_ID}/processing-status",
            headers=auth_headers(OWNER_ID),
        )

        assert response.status_code == 404


class TestDocumentListingReflectsProcessing:
    async def test_the_listing_shows_the_new_status(
        self,
        client: AsyncClient,
        auth_headers: Headers,
        document_repository: FakeDocumentRepository,
        document_storage: FakeDocumentStorage,
        task_runner: DeferredTaskRunner,
    ) -> None:
        """The UI polls the listing, so the status it shows has to move."""
        seed_text_document(document_repository, document_storage)

        before = (await client.get(BASE, headers=auth_headers(OWNER_ID))).json()
        assert before["documents"][0]["status"] == "uploaded"

        await client.post(PROCESS, headers=auth_headers(OWNER_ID))
        await task_runner.run_all()

        after = (await client.get(BASE, headers=auth_headers(OWNER_ID))).json()
        assert after["documents"][0]["status"] == "ready"
