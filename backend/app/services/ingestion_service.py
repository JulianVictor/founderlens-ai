"""The ingestion pipeline: from a stored document to persisted chunks.

    UPLOAD -> EXTRACT -> CLEAN -> CHUNK -> PERSIST -> EMBED -> INDEX

``UPLOAD`` is where the pipeline *acquires* the stored bytes; the upload itself
happened earlier, when the document was accepted. ``PERSIST`` stages chunks in
Postgres, ``EMBED`` vectorises them and ``INDEX`` writes nodes and vectors into
Neo4j, so ``ready`` now means a document is retrievable. Entity extraction and
graph construction are still to come, and answering questions is not
implemented.

Idempotency
-----------
Two different things have to be idempotent, and they are handled in different
places.

* **Two requests to process the same document.** Settled by the database:
  ``ingestion_jobs_one_active_per_document`` lets only one job be queued or
  running at a time, so a second request finds the first and returns it rather
  than starting a rival run. Reading "is one active?" and then inserting would
  not be enough -- two callers can both read "no".
* **Processing the same document twice over time.** Settled by deterministic
  chunk ids and a replace, in both stores: re-running over unchanged bytes
  recomputes the same ids, so Postgres updates the same rows and Neo4j MERGEs
  the same nodes and the same ``HAS_CHUNK`` edges. Nothing is duplicated, and
  chunks the new run did not produce are removed rather than left behind as
  evidence the document no longer supports.

Failure
-------
Every stage records that it was reached before it runs, so a failure says where
it happened. A failed job is left in place and a retry is a *new* job, so the
history of what was tried survives rather than being overwritten.
"""

from __future__ import annotations

import logging
from typing import Final
from uuid import UUID

from app.ai.embeddings import EmbeddingProvider
from app.ai.graph import ChunkGraph
from app.ai.ingestion import (
    ChunkerConfig,
    ParserRegistry,
    TextChunker,
    TextCleaner,
    Tokenizer,
)
from app.core.background import TaskRunner
from app.core.exceptions import (
    DocumentNotFoundError,
    DocumentParseError,
    DocumentStorageError,
    EmbeddingDimensionError,
    EmbeddingError,
    GraphError,
)
from app.models.document import Document, DocumentStatus
from app.models.ingestion import IngestionJob, IngestionJobStatus, IngestionStage
from app.repositories.chunk_repository import ChunkRepository
from app.repositories.document_repository import DocumentRepository
from app.repositories.document_storage import DocumentStorage
from app.repositories.ingestion_job_repository import IngestionJobRepository

logger: Final = logging.getLogger(__name__)

#: Identifies the code that produced a result. Bump it when a change to
#: parsing, cleaning or chunking means existing chunks are no longer what this
#: pipeline would produce -- that is what makes "which documents need
#: re-running" answerable instead of a guess.
PIPELINE_VERSION: Final = "2026.10.2-embedded"

#: Reported to the caller when a stage fails for a reason that is not the
#: document's fault. The specific cause goes to the logs: an error message is
#: shown in a UI, and internal detail there is noise at best.
_INTERNAL_FAILURE: Final = "Processing failed unexpectedly. Please try again."


class IngestionService:
    """Starts, runs and reports on ingestion."""

    def __init__(
        self,
        *,
        documents: DocumentRepository,
        jobs: IngestionJobRepository,
        chunks: ChunkRepository,
        storage: DocumentStorage,
        parsers: ParserRegistry,
        cleaner: TextCleaner,
        tokenizer: Tokenizer,
        chunker_config: ChunkerConfig,
        embeddings: EmbeddingProvider,
        graph: ChunkGraph,
        runner: TaskRunner,
    ) -> None:
        self._documents = documents
        self._jobs = jobs
        self._chunks = chunks
        self._storage = storage
        self._parsers = parsers
        self._cleaner = cleaner
        self._chunker = TextChunker(tokenizer, chunker_config)
        self._embeddings = embeddings
        self._graph = graph
        self._runner = runner

    # --- starting -------------------------------------------------------

    async def request_processing(
        self, workspace_id: UUID, document_id: UUID
    ) -> tuple[IngestionJob, bool]:
        """Ensure the document is being processed.

        Returns the job and whether this call created it. A caller that asks
        twice gets the same job back the second time rather than an error: a
        double-click is not a mistake worth refusing, and the guarantee that
        matters -- one run at a time -- still holds.

        Raises:
            DocumentNotFoundError: no such document in this workspace.
        """
        document = await self._documents.get(workspace_id, document_id)
        if document is None:
            raise DocumentNotFoundError(document_id)

        job = await self._jobs.claim(workspace_id, document_id, PIPELINE_VERSION)
        if job is None:
            # Lost the race, or one was already running. Either way the
            # document is being processed, which is what the caller wanted.
            existing = await self._jobs.active_for_document(workspace_id, document_id)
            if existing is not None:
                return existing, False
            # The active job finished between the insert and this read. Try
            # once more; a second failure is a real conflict worth surfacing.
            job = await self._jobs.claim(workspace_id, document_id, PIPELINE_VERSION)
            if job is None:  # pragma: no cover - needs a precise double race
                latest = await self._jobs.latest_for_document(workspace_id, document_id)
                if latest is None:
                    raise DocumentNotFoundError(document_id)
                return latest, False

        await self._runner.submit(f"ingest:{job.id}", lambda: self.run(workspace_id, job.id))
        return job, True

    # --- reporting ------------------------------------------------------

    async def status_for_document(
        self, workspace_id: UUID, document_id: UUID
    ) -> tuple[Document, IngestionJob | None, int]:
        """The document, its most recent job, and how many chunks it has.

        Raises:
            DocumentNotFoundError: no such document in this workspace.
        """
        document = await self._documents.get(workspace_id, document_id)
        if document is None:
            raise DocumentNotFoundError(document_id)

        job = await self._jobs.latest_for_document(workspace_id, document_id)
        chunk_count = await self._chunks.count_for_document(workspace_id, document_id)
        return document, job, chunk_count

    # --- running --------------------------------------------------------

    async def run(self, workspace_id: UUID, job_id: UUID) -> None:
        """Execute a queued job.

        Does nothing if the job is not queued. That is the second half of the
        no-duplicate-work guarantee: the claim stops two jobs existing, and
        this stops one job being run twice.
        """
        job = await self._jobs.start(job_id, IngestionStage.UPLOAD)
        if job is None:
            logger.info("Ingestion job %s was not queued; nothing to run", job_id)
            return

        document = await self._documents.get(workspace_id, job.document_id)
        if document is None:
            # Deleted between the request and the run.
            await self._jobs.finish(job_id, IngestionJobStatus.CANCELLED, error_message=None)
            return

        await self._documents.update_status(workspace_id, document.id, DocumentStatus.PROCESSING)

        try:
            chunk_count = await self._execute_stages(workspace_id, job_id, document)
        except (
            DocumentParseError,
            DocumentStorageError,
            EmbeddingError,
            EmbeddingDimensionError,
            GraphError,
        ) as exc:
            # Expected failures with a cause worth telling the user about: a
            # corrupt PDF, an unreachable bucket, a provider that is down.
            await self._fail(workspace_id, job_id, document, self._reason(exc))
            return
        except Exception:
            logger.exception("Ingestion job %s failed unexpectedly", job_id)
            await self._fail(workspace_id, job_id, document, _INTERNAL_FAILURE)
            return

        await self._jobs.finish(job_id, IngestionJobStatus.SUCCEEDED, chunk_count=chunk_count)
        await self._documents.update_status(workspace_id, document.id, DocumentStatus.READY)
        logger.info("Ingested document %s into %d chunk(s)", document.id, chunk_count)

    async def _execute_stages(self, workspace_id: UUID, job_id: UUID, document: Document) -> int:
        """Run the pipeline, recording each stage before attempting it.

        Recorded *before* rather than after, so a job that dies mid-stage --
        a restart, a killed process -- leaves behind the stage it was in
        rather than the last one it finished.
        """
        data = await self._storage.download(document.storage_path)

        await self._jobs.advance(job_id, IngestionStage.EXTRACT)
        extracted = self._parsers.parse(
            data,
            mime_type=document.mime_type,
            document_id=document.id,
            workspace_id=workspace_id,
        )

        await self._jobs.advance(job_id, IngestionStage.CLEAN)
        cleaned = self._cleaner.clean(extracted)

        await self._jobs.advance(job_id, IngestionStage.CHUNK)
        chunks = self._chunker.chunk(cleaned)

        await self._jobs.advance(job_id, IngestionStage.PERSIST)
        chunk_count = await self._chunks.replace_for_document(
            workspace_id,
            document.id,
            chunks,
            tokenizer=self._chunker.tokenizer_name,
            ingestion_job_id=job_id,
        )

        await self._jobs.advance(job_id, IngestionStage.EMBED)
        # One call for the whole document: the provider batches internally and
        # knows its own per-request limits, which this service does not.
        embeddings = await self._embeddings.embed_texts([chunk.text for chunk in chunks])

        await self._jobs.advance(job_id, IngestionStage.INDEX)
        await self._graph.index_document(
            workspace_id=workspace_id,
            document_id=document.id,
            filename=document.filename,
            mime_type=document.mime_type,
            chunks=chunks,
            embeddings=embeddings,
            embedding_model=self._embeddings.model,
        )
        return chunk_count

    async def _fail(
        self, workspace_id: UUID, job_id: UUID, document: Document, reason: str
    ) -> None:
        """Record a failure on both the job and the document.

        Both, because they answer different questions: the document says
        whether it is usable, and the job says what went wrong on which
        attempt. A retry adds a job; it does not erase this one.
        """
        await self._jobs.finish(job_id, IngestionJobStatus.FAILED, error_message=reason)
        await self._documents.update_status(
            workspace_id, document.id, DocumentStatus.FAILED, error_message=reason
        )

    @staticmethod
    def _reason(
        exc: DocumentParseError
        | DocumentStorageError
        | EmbeddingError
        | EmbeddingDimensionError
        | GraphError,
    ) -> str:
        """What to show the user for an expected failure.

        A parse error names something about their file, so it is passed
        through. The rest are infrastructure, and naming the provider or the
        database would tell them nothing they can act on.
        """
        if isinstance(exc, DocumentParseError):
            return exc.reason
        if isinstance(exc, DocumentStorageError):
            return "Document storage is unavailable. Please try again."
        if isinstance(exc, EmbeddingDimensionError):
            # A configuration fault, not a transient one: retrying changes
            # nothing until somebody fixes the model or the dimension.
            return "Embedding configuration is inconsistent. Contact support."
        if isinstance(exc, EmbeddingError):
            return "The embedding service is unavailable. Please try again."
        return "The knowledge graph is unavailable. Please try again."
