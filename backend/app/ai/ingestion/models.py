"""Domain models for the ingestion pipeline.

These are pipeline-internal values, not API contracts and not persistence rows,
so they live beside the code that produces them rather than in ``app/schemas``
or ``app/models``. A chunks table and a chunk response will exist in a later
phase; they will be projections of :class:`DocumentChunk`, not this class
renamed.

Offsets
-------
Two coordinate systems are in play, and every model says which one it uses.

* **Source offsets** index :attr:`ExtractedDocument.text` -- the text as the
  parser produced it. This is the canonical text a citation ultimately points
  into, and the only one that page boundaries are expressed in.
* **Cleaned offsets** index :attr:`CleanedDocument.text`. Cleaning deletes and
  substitutes characters, so the two systems differ; :class:`OffsetMap`
  converts exactly between them.

Keeping both means a chunk can be verified against the text it was cut from
(``cleaned.text[start:end] == chunk.text``) *and* traced back to a span of the
original document.
"""

from __future__ import annotations

from bisect import bisect_right
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Frozen(BaseModel):
    """Base for the immutable value objects below."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class ExtractedPage(_Frozen):
    """One page of a paginated document.

    Only formats that have pages produce these. Plain text and Markdown have
    none, and their documents carry an empty :attr:`ExtractedDocument.pages`.
    """

    #: 1-based, matching how a reader refers to a page.
    page_number: int = Field(ge=1)
    text: str
    #: Half-open span of this page within ``ExtractedDocument.text``.
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)

    @model_validator(mode="after")
    def _check_span(self) -> ExtractedPage:
        if self.end_offset < self.start_offset:
            raise ValueError("end_offset precedes start_offset")
        if self.end_offset - self.start_offset != len(self.text):
            raise ValueError("page span does not match its text length")
        return self


class ExtractedDocument(_Frozen):
    """The text of a document, as a parser produced it.

    :attr:`text` is the authority for source offsets. For a paginated format it
    is the pages joined by :data:`PAGE_SEPARATOR`; nothing else is inserted, so
    every page's recorded span indexes it exactly.
    """

    document_id: UUID
    workspace_id: UUID
    #: The detected MIME type the parser was selected for.
    mime_type: str
    text: str
    pages: tuple[ExtractedPage, ...] = ()
    #: Format-level metadata (PDF title, author, producer; Markdown front
    #: matter). Values are kept as strings because they come from the file and
    #: are not trusted to have any particular shape.
    metadata: dict[str, str] = Field(default_factory=dict)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def is_paginated(self) -> bool:
        return bool(self.pages)

    def page_for_offset(self, source_offset: int) -> int | None:
        """Page number containing ``source_offset``, or ``None``.

        ``None`` means either that the format has no pages, or that the offset
        falls in a separator between them -- both of which are "no page", not
        an error.
        """
        if not self.pages:
            return None
        starts = [page.start_offset for page in self.pages]
        index = bisect_right(starts, source_offset) - 1
        if index < 0:
            return None
        page = self.pages[index]
        return page.page_number if source_offset < page.end_offset else None


class OffsetRun(_Frozen):
    """A stretch of cleaned text that maps one-to-one onto source text.

    Within a run, ``cleaned[clean_start + k]`` corresponds to
    ``source[source_start + k]``. The two characters are usually identical; they
    differ only where cleaning performed an equal-length substitution, which is
    why this records a correspondence rather than claiming equality.
    """

    clean_start: int = Field(ge=0)
    source_start: int = Field(ge=0)
    length: int = Field(gt=0)


class OffsetMap(_Frozen):
    """Exact translation from cleaned offsets back to source offsets.

    Cleaning is expressed entirely as deletions and equal-length substitutions,
    so every cleaned character belongs to exactly one run and the translation is
    exact rather than approximate. An approximate map would be worse than none:
    a citation highlighting the wrong sentence is harder to notice than one that
    admits it does not know.
    """

    runs: tuple[OffsetRun, ...] = ()

    def to_source(self, clean_offset: int) -> int:
        """Source offset corresponding to ``clean_offset``.

        Accepts an offset one past the end, so that a half-open cleaned span
        converts to a half-open source span.
        """
        if clean_offset < 0:
            raise ValueError("clean_offset must not be negative")
        if not self.runs:
            return 0

        starts = [run.clean_start for run in self.runs]
        index = bisect_right(starts, clean_offset) - 1
        if index < 0:
            return self.runs[0].source_start

        run = self.runs[index]
        delta = clean_offset - run.clean_start
        if delta < run.length:
            return run.source_start + delta
        # Past the end of this run: the offset sits where the next run begins,
        # or at the end of the source text this map covers.
        return run.source_start + run.length


class CleanedDocument(_Frozen):
    """Normalised text, plus the means to get back to where it came from."""

    document_id: UUID
    workspace_id: UUID
    text: str
    source_map: OffsetMap
    #: Carried through from extraction so a chunk can be attributed to a page
    #: after its cleaned offsets are translated back to source offsets.
    pages: tuple[ExtractedPage, ...] = ()

    def page_for_clean_offset(self, clean_offset: int) -> int | None:
        """Page containing the cleaned offset, or ``None`` if unpaginated."""
        if not self.pages:
            return None
        source_offset = self.source_map.to_source(clean_offset)
        starts = [page.start_offset for page in self.pages]
        index = bisect_right(starts, source_offset) - 1
        if index < 0:
            return None
        page = self.pages[index]
        return page.page_number if source_offset < page.end_offset else None


class DocumentChunk(_Frozen):
    """One retrievable unit of text, with everything needed to cite it."""

    #: Deterministic: the same document chunked the same way yields the same id.
    #: See :func:`app.ai.ingestion.chunking.build_chunk_id`.
    chunk_id: UUID
    document_id: UUID
    workspace_id: UUID

    text: str
    #: Position within the document, 0-based and gap-free.
    chunk_index: int = Field(ge=0)

    #: Page the chunk starts on, or ``None`` for unpaginated formats.
    page_number: int | None = None
    #: Every page the chunk covers. A chunk may span a page break, and a
    #: citation that named only the first page would be wrong about the rest.
    page_numbers: tuple[int, ...] = ()

    token_count: int = Field(ge=0)

    #: Half-open span in ``CleanedDocument.text``. Exact:
    #: ``cleaned.text[start_offset:end_offset] == text``.
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)
    #: Half-open span in ``ExtractedDocument.text`` -- the provenance anchor.
    source_start_offset: int = Field(ge=0)
    source_end_offset: int = Field(ge=0)

    #: SHA-256 of :attr:`text`. Equal hashes mean identical chunk content, which
    #: is how repeated boilerplate is detected without comparing text.
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _check_spans(self) -> DocumentChunk:
        if self.end_offset < self.start_offset:
            raise ValueError("end_offset precedes start_offset")
        if self.source_end_offset < self.source_start_offset:
            raise ValueError("source_end_offset precedes source_start_offset")
        if self.page_number is not None and self.page_number not in self.page_numbers:
            raise ValueError("page_number must appear in page_numbers")
        return self


#: Inserted between pages when a paginated document's text is joined. Part of
#: the source text, and therefore part of the offset arithmetic, so it is a
#: constant rather than a parser-by-parser choice.
PAGE_SEPARATOR = "\n\n"
