"""The ingestion pipeline: state transitions, failure, retry and idempotency."""

from __future__ import annotations

from uuid import UUID

import pytest

from app.ai.ingestion import ChunkerConfig, HeuristicTokenizer, build_default_registry
from app.ai.ingestion.cleaning import TextCleaner
from app.core.exceptions import DocumentNotFoundError
from app.models.document import DocumentStatus
from app.models.ingestion import IngestionJob, IngestionJobStatus, IngestionStage
from app.services.ingestion_service import PIPELINE_VERSION, IngestionService
from tests.conftest import (
    DOCUMENT_ID,
    OTHER_DOCUMENT_ID,
    OTHER_WORKSPACE_ID,
    UNKNOWN_DOCUMENT_ID,
    WORKSPACE_ID,
    DeferredTaskRunner,
    FakeChunkRepository,
    FakeDocumentRepository,
    FakeDocumentStorage,
    FakeIngestionJobRepository,
)

PDF_PATH = f"{WORKSPACE_ID}/{DOCUMENT_ID}.pdf"

TEXT_DOCUMENT = (
    b"Acme Robotics builds warehouse automation hardware.\n\n"
    b"The Series A closed in March 2026 at twelve million dollars, led by "
    b"Initech Ventures.\n\n"
    b"Competition comes from incumbents selling fixed conveyor systems.\n"
)


@pytest.fixture
def service(
    document_repository: FakeDocumentRepository,
    ingestion_job_repository: FakeIngestionJobRepository,
    chunk_repository: FakeChunkRepository,
    document_storage: FakeDocumentStorage,
    task_runner: DeferredTaskRunner,
) -> IngestionService:
    return IngestionService(
        documents=document_repository,
        jobs=ingestion_job_repository,
        chunks=chunk_repository,
        storage=document_storage,
        parsers=build_default_registry(),
        cleaner=TextCleaner(),
        tokenizer=HeuristicTokenizer(),
        chunker_config=ChunkerConfig(chunk_size_tokens=40, chunk_overlap_tokens=10),
        runner=task_runner,
    )


@pytest.fixture
def stored_text_document(
    document_repository: FakeDocumentRepository, document_storage: FakeDocumentStorage
) -> None:
    """Point the fixture document at plain-text bytes in storage.

    The seeded document claims to be a PDF; the pipeline reads the recorded
    mime_type, so the stored bytes and the record have to agree.
    """
    from dataclasses import replace

    document = document_repository.documents[0]
    document_repository.documents[0] = replace(document, mime_type="text/plain")
    document_storage.objects[document.storage_path] = TEXT_DOCUMENT


async def run_to_completion(service: IngestionService, runner: DeferredTaskRunner) -> None:
    await runner.run_all()


class TestSuccessfulProcessing:
    async def test_creates_a_queued_job(
        self, service: IngestionService, stored_text_document: None
    ) -> None:
        job, created = await service.request_processing(WORKSPACE_ID, DOCUMENT_ID)

        assert created
        assert job.status is IngestionJobStatus.QUEUED
        assert job.current_stage is IngestionStage.UPLOAD
        assert job.attempt == 1
        assert job.pipeline_version == PIPELINE_VERSION

    async def test_does_not_run_before_the_runner_does(
        self,
        service: IngestionService,
        stored_text_document: None,
        chunk_repository: FakeChunkRepository,
    ) -> None:
        """Requesting processing accepts work; it does not perform it."""
        await service.request_processing(WORKSPACE_ID, DOCUMENT_ID)

        assert chunk_repository.replace_calls == 0

    async def test_completes_and_persists_chunks(
        self,
        service: IngestionService,
        stored_text_document: None,
        task_runner: DeferredTaskRunner,
        chunk_repository: FakeChunkRepository,
    ) -> None:
        await service.request_processing(WORKSPACE_ID, DOCUMENT_ID)
        await task_runner.run_all()

        chunks = await chunk_repository.list_for_document(WORKSPACE_ID, DOCUMENT_ID)
        assert len(chunks) > 1
        assert all(chunk.document_id == DOCUMENT_ID for chunk in chunks)
        assert all(chunk.workspace_id == WORKSPACE_ID for chunk in chunks)

    async def test_job_succeeds_and_records_the_chunk_count(
        self,
        service: IngestionService,
        stored_text_document: None,
        task_runner: DeferredTaskRunner,
        ingestion_job_repository: FakeIngestionJobRepository,
        chunk_repository: FakeChunkRepository,
    ) -> None:
        await service.request_processing(WORKSPACE_ID, DOCUMENT_ID)
        await task_runner.run_all()

        job = ingestion_job_repository.jobs[-1]
        assert job.status is IngestionJobStatus.SUCCEEDED
        assert job.current_stage is IngestionStage.PERSIST
        assert job.error_message is None
        assert job.chunk_count == len(chunk_repository.chunks[DOCUMENT_ID])
        assert job.started_at is not None
        assert job.completed_at is not None

    async def test_document_becomes_ready(
        self,
        service: IngestionService,
        stored_text_document: None,
        task_runner: DeferredTaskRunner,
        document_repository: FakeDocumentRepository,
    ) -> None:
        await service.request_processing(WORKSPACE_ID, DOCUMENT_ID)
        await task_runner.run_all()

        document = await document_repository.get(WORKSPACE_ID, DOCUMENT_ID)
        assert document is not None
        assert document.status is DocumentStatus.READY
        assert document.error_message is None

    async def test_records_which_tokenizer_sized_the_chunks(
        self,
        service: IngestionService,
        stored_text_document: None,
        task_runner: DeferredTaskRunner,
        chunk_repository: FakeChunkRepository,
    ) -> None:
        """Token counts from different tokenizers are not comparable, so the
        number is meaningless without this."""
        await service.request_processing(WORKSPACE_ID, DOCUMENT_ID)
        await task_runner.run_all()

        assert chunk_repository.tokenizer == "heuristic"

    async def test_processes_a_real_pdf(
        self,
        service: IngestionService,
        document_repository: FakeDocumentRepository,
        document_storage: FakeDocumentStorage,
        task_runner: DeferredTaskRunner,
        chunk_repository: FakeChunkRepository,
    ) -> None:
        """End to end on the format that actually needs a parser."""
        from tests.ai.ingestion.conftest import fixture_bytes

        document = document_repository.documents[0]
        document_storage.objects[document.storage_path] = fixture_bytes("acme-brief.pdf")

        await service.request_processing(WORKSPACE_ID, DOCUMENT_ID)
        await task_runner.run_all()

        chunks = await chunk_repository.list_for_document(WORKSPACE_ID, DOCUMENT_ID)
        assert chunks
        assert all(chunk.page_number is not None for chunk in chunks)


class TestStateTransitions:
    async def test_passes_through_every_stage_in_order(
        self,
        service: IngestionService,
        stored_text_document: None,
        task_runner: DeferredTaskRunner,
        ingestion_job_repository: FakeIngestionJobRepository,
    ) -> None:
        """Stages are recorded as they are reached, so a job that dies
        mid-flight leaves behind where it was rather than where it last
        finished.
        """
        seen: list[IngestionStage] = []
        original = ingestion_job_repository.advance

        async def recording_advance(job_id: UUID, stage: IngestionStage) -> IngestionJob | None:
            seen.append(stage)
            return await original(job_id, stage)

        ingestion_job_repository.advance = recording_advance  # type: ignore[method-assign]

        await service.request_processing(WORKSPACE_ID, DOCUMENT_ID)
        await task_runner.run_all()

        assert seen == [
            IngestionStage.EXTRACT,
            IngestionStage.CLEAN,
            IngestionStage.CHUNK,
            IngestionStage.PERSIST,
        ]

    async def test_document_is_processing_while_the_job_runs(
        self,
        service: IngestionService,
        stored_text_document: None,
        task_runner: DeferredTaskRunner,
        document_repository: FakeDocumentRepository,
        document_storage: FakeDocumentStorage,
    ) -> None:
        """Observed from inside the run, which is the only moment it is true."""
        observed: list[DocumentStatus] = []
        original = document_storage.download

        async def observing_download(path: str) -> bytes:
            document = await document_repository.get(WORKSPACE_ID, DOCUMENT_ID)
            assert document is not None
            observed.append(document.status)
            return await original(path)

        document_storage.download = observing_download  # type: ignore[method-assign]

        await service.request_processing(WORKSPACE_ID, DOCUMENT_ID)
        await task_runner.run_all()

        assert observed == [DocumentStatus.PROCESSING]

    async def test_stage_order_is_the_declared_order(self) -> None:
        """`order` depends on declaration order, so a stage inserted in the
        wrong place would silently reorder the pipeline's own idea of
        progress."""
        stages = list(IngestionStage)

        assert [stage.order for stage in stages] == list(range(len(stages)))
        assert stages[0] is IngestionStage.UPLOAD
        assert stages[-1] is IngestionStage.PERSIST

    async def test_active_and_terminal_partition_the_statuses(self) -> None:
        for status in IngestionJobStatus:
            assert status.is_active != status.is_terminal


class TestFailure:
    async def test_an_unparseable_document_fails_the_job(
        self,
        service: IngestionService,
        document_repository: FakeDocumentRepository,
        document_storage: FakeDocumentStorage,
        task_runner: DeferredTaskRunner,
        ingestion_job_repository: FakeIngestionJobRepository,
    ) -> None:
        document = document_repository.documents[0]
        document_storage.objects[document.storage_path] = b"not a pdf at all"

        await service.request_processing(WORKSPACE_ID, DOCUMENT_ID)
        await task_runner.run_all()

        job = ingestion_job_repository.jobs[-1]
        assert job.status is IngestionJobStatus.FAILED
        assert job.error_message is not None
        assert "PDF" in job.error_message
        assert job.completed_at is not None

    async def test_failure_records_the_stage_it_happened_at(
        self,
        service: IngestionService,
        document_repository: FakeDocumentRepository,
        document_storage: FakeDocumentStorage,
        task_runner: DeferredTaskRunner,
        ingestion_job_repository: FakeIngestionJobRepository,
    ) -> None:
        """The difference between 'ingestion failed' and 'extraction failed'."""
        document = document_repository.documents[0]
        document_storage.objects[document.storage_path] = b"not a pdf at all"

        await service.request_processing(WORKSPACE_ID, DOCUMENT_ID)
        await task_runner.run_all()

        assert ingestion_job_repository.jobs[-1].current_stage is IngestionStage.EXTRACT

    async def test_a_scanned_pdf_reports_that_it_needs_ocr(
        self,
        service: IngestionService,
        document_repository: FakeDocumentRepository,
        document_storage: FakeDocumentStorage,
        task_runner: DeferredTaskRunner,
        ingestion_job_repository: FakeIngestionJobRepository,
    ) -> None:
        """The user can act on this one, so the reason reaches them."""
        from tests.ai.ingestion.conftest import fixture_bytes

        document = document_repository.documents[0]
        document_storage.objects[document.storage_path] = fixture_bytes("no-text-layer.pdf")

        await service.request_processing(WORKSPACE_ID, DOCUMENT_ID)
        await task_runner.run_all()

        message = ingestion_job_repository.jobs[-1].error_message
        assert message is not None
        assert "OCR" in message

    async def test_document_becomes_failed_with_the_reason(
        self,
        service: IngestionService,
        document_repository: FakeDocumentRepository,
        document_storage: FakeDocumentStorage,
        task_runner: DeferredTaskRunner,
    ) -> None:
        document = document_repository.documents[0]
        document_storage.objects[document.storage_path] = b"not a pdf at all"

        await service.request_processing(WORKSPACE_ID, DOCUMENT_ID)
        await task_runner.run_all()

        updated = await document_repository.get(WORKSPACE_ID, DOCUMENT_ID)
        assert updated is not None
        assert updated.status is DocumentStatus.FAILED
        assert updated.error_message is not None

    async def test_unreachable_storage_fails_at_the_upload_stage(
        self,
        service: IngestionService,
        stored_text_document: None,
        document_storage: FakeDocumentStorage,
        task_runner: DeferredTaskRunner,
        ingestion_job_repository: FakeIngestionJobRepository,
    ) -> None:
        document_storage.fail_download = True

        await service.request_processing(WORKSPACE_ID, DOCUMENT_ID)
        await task_runner.run_all()

        job = ingestion_job_repository.jobs[-1]
        assert job.status is IngestionJobStatus.FAILED
        assert job.current_stage is IngestionStage.UPLOAD

    async def test_a_failed_run_persists_no_chunks(
        self,
        service: IngestionService,
        document_repository: FakeDocumentRepository,
        document_storage: FakeDocumentStorage,
        task_runner: DeferredTaskRunner,
        chunk_repository: FakeChunkRepository,
    ) -> None:
        """Persisting is the last stage, so a failure before it leaves the
        corpus untouched rather than half-written."""
        document = document_repository.documents[0]
        document_storage.objects[document.storage_path] = b"not a pdf at all"

        await service.request_processing(WORKSPACE_ID, DOCUMENT_ID)
        await task_runner.run_all()

        assert chunk_repository.replace_calls == 0

    async def test_an_unexpected_error_does_not_leak_internals(
        self,
        service: IngestionService,
        stored_text_document: None,
        chunk_repository: FakeChunkRepository,
        task_runner: DeferredTaskRunner,
        ingestion_job_repository: FakeIngestionJobRepository,
    ) -> None:
        """An error message is shown in a UI; a stack trace there is noise at
        best and disclosure at worst."""

        async def explode(*args: object, **kwargs: object) -> int:
            raise RuntimeError("connection pool exhausted at 10.0.0.4:5432")

        chunk_repository.replace_for_document = explode  # type: ignore[method-assign]

        await service.request_processing(WORKSPACE_ID, DOCUMENT_ID)
        await task_runner.run_all()

        job = ingestion_job_repository.jobs[-1]
        assert job.status is IngestionJobStatus.FAILED
        assert job.error_message is not None
        assert "10.0.0.4" not in job.error_message
        assert "pool" not in job.error_message

    async def test_a_deleted_document_cancels_rather_than_fails(
        self,
        service: IngestionService,
        stored_text_document: None,
        document_repository: FakeDocumentRepository,
        task_runner: DeferredTaskRunner,
        ingestion_job_repository: FakeIngestionJobRepository,
    ) -> None:
        """Nothing went wrong -- the work simply no longer has a subject."""
        await service.request_processing(WORKSPACE_ID, DOCUMENT_ID)
        await document_repository.delete(WORKSPACE_ID, DOCUMENT_ID)
        await task_runner.run_all()

        assert ingestion_job_repository.jobs[-1].status is IngestionJobStatus.CANCELLED


class TestRetry:
    async def test_a_retry_after_failure_is_a_new_attempt(
        self,
        service: IngestionService,
        document_repository: FakeDocumentRepository,
        document_storage: FakeDocumentStorage,
        task_runner: DeferredTaskRunner,
        ingestion_job_repository: FakeIngestionJobRepository,
    ) -> None:
        document = document_repository.documents[0]
        document_storage.objects[document.storage_path] = b"not a pdf at all"

        await service.request_processing(WORKSPACE_ID, DOCUMENT_ID)
        await task_runner.run_all()
        second, created = await service.request_processing(WORKSPACE_ID, DOCUMENT_ID)

        assert created
        assert second.attempt == 2

    async def test_the_failed_attempt_is_kept(
        self,
        service: IngestionService,
        document_repository: FakeDocumentRepository,
        document_storage: FakeDocumentStorage,
        task_runner: DeferredTaskRunner,
        ingestion_job_repository: FakeIngestionJobRepository,
    ) -> None:
        """A retry adds a row; it does not overwrite the history of what was
        tried."""
        document = document_repository.documents[0]
        document_storage.objects[document.storage_path] = b"not a pdf at all"

        await service.request_processing(WORKSPACE_ID, DOCUMENT_ID)
        await task_runner.run_all()
        await service.request_processing(WORKSPACE_ID, DOCUMENT_ID)

        assert len(ingestion_job_repository.jobs) == 2
        assert ingestion_job_repository.jobs[0].status is IngestionJobStatus.FAILED

    async def test_a_retry_succeeds_once_the_cause_is_fixed(
        self,
        service: IngestionService,
        document_repository: FakeDocumentRepository,
        document_storage: FakeDocumentStorage,
        task_runner: DeferredTaskRunner,
        ingestion_job_repository: FakeIngestionJobRepository,
    ) -> None:
        """A transient failure must not leave a document permanently broken."""
        document = document_repository.documents[0]
        document_storage.objects[document.storage_path] = TEXT_DOCUMENT
        from dataclasses import replace

        document_repository.documents[0] = replace(document, mime_type="text/plain")
        document_storage.fail_download = True

        await service.request_processing(WORKSPACE_ID, DOCUMENT_ID)
        await task_runner.run_all()
        first_attempt = ingestion_job_repository.jobs[-1]
        assert first_attempt.status is IngestionJobStatus.FAILED

        document_storage.fail_download = False
        await service.request_processing(WORKSPACE_ID, DOCUMENT_ID)
        await task_runner.run_all()

        second_attempt = ingestion_job_repository.jobs[-1]
        assert second_attempt.status is IngestionJobStatus.SUCCEEDED
        updated = await document_repository.get(WORKSPACE_ID, DOCUMENT_ID)
        assert updated is not None
        assert updated.status is DocumentStatus.READY
        # The explanation is cleared, so a recovered document does not keep
        # explaining a failure that no longer applies.
        assert updated.error_message is None


class TestIdempotency:
    async def test_a_second_request_returns_the_running_job(
        self, service: IngestionService, stored_text_document: None
    ) -> None:
        """A double-click is not a mistake worth refusing."""
        first, first_created = await service.request_processing(WORKSPACE_ID, DOCUMENT_ID)
        second, second_created = await service.request_processing(WORKSPACE_ID, DOCUMENT_ID)

        assert first_created
        assert not second_created
        assert second.id == first.id

    async def test_a_second_request_does_not_queue_a_second_run(
        self,
        service: IngestionService,
        stored_text_document: None,
        task_runner: DeferredTaskRunner,
    ) -> None:
        await service.request_processing(WORKSPACE_ID, DOCUMENT_ID)
        await service.request_processing(WORKSPACE_ID, DOCUMENT_ID)

        assert len(task_runner.submitted) == 1

    async def test_running_the_same_job_twice_does_nothing_the_second_time(
        self,
        service: IngestionService,
        stored_text_document: None,
        task_runner: DeferredTaskRunner,
        chunk_repository: FakeChunkRepository,
    ) -> None:
        """The claim stops two jobs existing; this stops one job running
        twice, which is the half a queue with at-least-once delivery would
        otherwise expose."""
        job, _ = await service.request_processing(WORKSPACE_ID, DOCUMENT_ID)

        await service.run(WORKSPACE_ID, job.id)
        await service.run(WORKSPACE_ID, job.id)

        assert chunk_repository.replace_calls == 1

    async def test_reprocessing_does_not_duplicate_chunks(
        self,
        service: IngestionService,
        stored_text_document: None,
        task_runner: DeferredTaskRunner,
        chunk_repository: FakeChunkRepository,
    ) -> None:
        """The headline guarantee."""
        await service.request_processing(WORKSPACE_ID, DOCUMENT_ID)
        await task_runner.run_all()
        first = await chunk_repository.list_for_document(WORKSPACE_ID, DOCUMENT_ID)

        await service.request_processing(WORKSPACE_ID, DOCUMENT_ID)
        await task_runner.run_all()
        second = await chunk_repository.list_for_document(WORKSPACE_ID, DOCUMENT_ID)

        assert len(second) == len(first)

    async def test_reprocessing_produces_identical_chunk_ids(
        self,
        service: IngestionService,
        stored_text_document: None,
        task_runner: DeferredTaskRunner,
        chunk_repository: FakeChunkRepository,
    ) -> None:
        """Stable ids are what let the replace be an update rather than a
        delete-and-recreate, so graph provenance does not churn."""
        await service.request_processing(WORKSPACE_ID, DOCUMENT_ID)
        await task_runner.run_all()
        first = [c.chunk_id for c in chunk_repository.chunks[DOCUMENT_ID]]

        await service.request_processing(WORKSPACE_ID, DOCUMENT_ID)
        await task_runner.run_all()
        second = [c.chunk_id for c in chunk_repository.chunks[DOCUMENT_ID]]

        assert first == second

    async def test_reprocessing_changed_content_replaces_the_chunks(
        self,
        service: IngestionService,
        stored_text_document: None,
        document_repository: FakeDocumentRepository,
        document_storage: FakeDocumentStorage,
        task_runner: DeferredTaskRunner,
        chunk_repository: FakeChunkRepository,
    ) -> None:
        """Chunks from the old content must not survive as evidence the
        document no longer supports."""
        await service.request_processing(WORKSPACE_ID, DOCUMENT_ID)
        await task_runner.run_all()
        before = {c.chunk_id for c in chunk_repository.chunks[DOCUMENT_ID]}

        document = document_repository.documents[0]
        document_storage.objects[document.storage_path] = b"Entirely different text.\n"

        await service.request_processing(WORKSPACE_ID, DOCUMENT_ID)
        await task_runner.run_all()
        after = {c.chunk_id for c in chunk_repository.chunks[DOCUMENT_ID]}

        assert before.isdisjoint(after)
        assert len(after) == 1


class TestAuthorisationAndStatus:
    async def test_processing_a_document_from_another_workspace_is_not_found(
        self, service: IngestionService
    ) -> None:
        with pytest.raises(DocumentNotFoundError):
            await service.request_processing(WORKSPACE_ID, OTHER_DOCUMENT_ID)

    async def test_processing_an_unknown_document_is_not_found(
        self, service: IngestionService
    ) -> None:
        with pytest.raises(DocumentNotFoundError):
            await service.request_processing(WORKSPACE_ID, UNKNOWN_DOCUMENT_ID)

    async def test_a_rejected_request_queues_nothing(
        self, service: IngestionService, task_runner: DeferredTaskRunner
    ) -> None:
        with pytest.raises(DocumentNotFoundError):
            await service.request_processing(OTHER_WORKSPACE_ID, DOCUMENT_ID)

        assert task_runner.submitted == []

    async def test_status_before_any_processing(self, service: IngestionService) -> None:
        document, job, chunk_count = await service.status_for_document(WORKSPACE_ID, DOCUMENT_ID)

        assert document.status is DocumentStatus.UPLOADED
        assert job is None
        assert chunk_count == 0

    async def test_status_after_processing(
        self,
        service: IngestionService,
        stored_text_document: None,
        task_runner: DeferredTaskRunner,
    ) -> None:
        await service.request_processing(WORKSPACE_ID, DOCUMENT_ID)
        await task_runner.run_all()

        document, job, chunk_count = await service.status_for_document(WORKSPACE_ID, DOCUMENT_ID)

        assert document.status is DocumentStatus.READY
        assert job is not None
        assert job.status is IngestionJobStatus.SUCCEEDED
        assert chunk_count > 0

    async def test_status_for_a_document_in_another_workspace_is_not_found(
        self, service: IngestionService
    ) -> None:
        with pytest.raises(DocumentNotFoundError):
            await service.status_for_document(WORKSPACE_ID, OTHER_DOCUMENT_ID)
