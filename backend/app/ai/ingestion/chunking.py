"""Splitting a cleaned document into retrievable, citable chunks.

The chunk is the unit of retrieval, of embedding and of citation at the same
time, so its boundaries matter more than any other ingestion parameter.

Why the text is split on structure rather than on tokens
--------------------------------------------------------
The obvious implementation is to encode the document, slice the token array
into windows, and decode each window. It is also wrong for this system:
decoding a token slice produces a string with no relationship to the original
character positions, and a chunk whose offsets are unknown cannot be cited.

So the text is divided at boundaries that exist in the text -- paragraphs, then
sentences, then words -- and the tokenizer is used only to decide how many of
those pieces fit. Character offsets survive exactly, and chunks additionally
end up respecting the document's own structure, which is better for retrieval
than a window that cuts mid-sentence.

Token counts are not additive under byte-pair encoding: encoding two strings
separately can yield a different total than encoding their concatenation,
because merges happen across the join. Packing therefore uses the sum of the
pieces as its budget, and the count recorded on the finished chunk is a fresh
count of the text actually emitted. The two agree closely but not always
exactly, and the recorded one is the true number.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass, replace
from typing import Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.ai.ingestion.models import CleanedDocument, DocumentChunk
from app.ai.ingestion.tokenization import Tokenizer

#: Namespace for deterministic chunk ids. A fixed, project-specific UUID, so
#: that ids are reproducible across processes and machines -- a random
#: namespace would make "the same document chunks to the same ids" false the
#: moment the service restarted.
CHUNK_NAMESPACE: Final = uuid.UUID("6f1c0d2e-3b4a-5c6d-8e9f-0a1b2c3d4e5f")

#: A blank line: the strongest structural boundary in plain text and Markdown.
_PARAGRAPH_BREAK: Final = re.compile(r"\n[ \t]*\n")

#: End of a sentence: terminal punctuation, optional closing quote or bracket,
#: then whitespace. Approximate -- it splits "Inc. v" and similar -- but a
#: mis-split sentence costs a slightly odd chunk boundary, never a wrong
#: citation, because offsets are preserved either way.
_SENTENCE_BREAK: Final = re.compile(r'(?<=[.!?])["\')\]]*\s+')

_WORD_BREAK: Final = re.compile(r"\s+")


class ChunkerConfig(BaseModel):
    """Chunk sizing, in tokens."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Target upper bound for a chunk. 800 tokens is comfortably inside every
    #: embedding model's window while still holding enough context that a
    #: retrieved chunk answers a question rather than hinting at one.
    chunk_size_tokens: int = Field(default=800, gt=0)

    #: Carried from the end of each chunk into the start of the next, so that a
    #: fact spanning a boundary is retrievable from both sides. 120 tokens is
    #: roughly a long paragraph: enough to keep a sentence with the context
    #: that makes it meaningful, without duplicating so much that retrieval
    #: returns the same passage repeatedly.
    #:
    #: A **budget, not a promise**. Overlap repeats whole paragraphs or
    #: sentences, because a piece is the smallest unit whose character offsets
    #: are known, and a chunk with unknown offsets cannot be cited. Where every
    #: trailing piece is larger than this budget, none fits and the chunks come
    #: out disjoint.
    chunk_overlap_tokens: int = Field(default=120, ge=0)

    @model_validator(mode="after")
    def _overlap_is_smaller(self) -> ChunkerConfig:
        if self.chunk_overlap_tokens >= self.chunk_size_tokens:
            # Overlap at or above the chunk size means each chunk re-emits
            # everything the last one did, and the chunker never advances.
            raise ValueError("chunk_overlap_tokens must be less than chunk_size_tokens")
        return self


@dataclass(frozen=True, slots=True)
class _Piece:
    """An indivisible span of the cleaned text, with its token cost.

    ``end`` excludes the whitespace that separated this piece from the next, so
    a chunk ending here does not trail blank space. ``next_start`` is where the
    following piece begins, which is what makes the pieces cover the document
    without gaps.
    """

    start: int
    end: int
    next_start: int
    tokens: int


def content_hash(text: str) -> str:
    """SHA-256 of a chunk's text.

    Identical hashes mean identical content. That is how boilerplate repeated
    across a corpus -- a disclaimer on every page, a footer in every filing --
    is detected without comparing text, and it is what lets a re-ingested
    document be recognised as unchanged.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_chunk_id(document_id: UUID, chunk_index: int, text_hash: str) -> UUID:
    """Deterministic id for a chunk.

    Derived from the document, the position and the content, so that:

    * re-ingesting an unchanged document with unchanged settings produces the
      same ids, and the graph's provenance edges do not churn;
    * a chunk whose text changed gets a new id, because it is a different
      chunk and should not inherit the claims that cited the old one;
    * two documents containing identical text still get distinct ids, since
      provenance is per document.
    """
    return uuid.uuid5(CHUNK_NAMESPACE, f"{document_id}:{chunk_index}:{text_hash}")


class TextChunker:
    """Splits cleaned text into overlapping, offset-preserving chunks."""

    def __init__(self, tokenizer: Tokenizer, config: ChunkerConfig | None = None) -> None:
        self._tokenizer = tokenizer
        self._config = config or ChunkerConfig()

    @property
    def config(self) -> ChunkerConfig:
        return self._config

    @property
    def tokenizer_name(self) -> str:
        return self._tokenizer.name

    def chunk(self, document: CleanedDocument) -> list[DocumentChunk]:
        """Split ``document`` into chunks, in reading order.

        Returns an empty list for a document with no text, rather than one
        empty chunk: an empty chunk would be embedded, retrieved and cited like
        any other, and it supports nothing.
        """
        text = document.text
        if not text.strip():
            return []

        pieces = self._split(text, 0, len(text))
        if not pieces:
            return []

        # Re-count each piece over its span *including* the separator that
        # follows it. The emitted chunk is a single slice spanning its pieces,
        # so those separators are inside it; counting pieces without them made
        # packing optimistic and let chunks run over the limit.
        pieces = [
            replace(piece, tokens=self._tokenizer.count(text[piece.start : piece.next_start]))
            for piece in pieces
        ]

        return self._pack(document, pieces)

    # --- splitting ------------------------------------------------------

    def _split(self, text: str, start: int, end: int) -> list[_Piece]:
        """Divide ``text[start:end]`` into pieces no larger than one chunk.

        Descends through progressively finer boundaries, stopping as soon as a
        span is small enough. A span that is still too large at the word level
        is cut by character count, which is a last resort rather than the
        strategy: it only happens for pathological input such as a
        base64 blob with no whitespace.
        """
        return self._split_by(text, start, end, level=0)

    def _split_by(self, text: str, start: int, end: int, *, level: int) -> list[_Piece]:
        span = text[start:end]
        if not span.strip():
            return []

        tokens = self._tokenizer.count(span)
        if tokens <= self._config.chunk_size_tokens or level > 2:
            if tokens <= self._config.chunk_size_tokens:
                return [_Piece(start=start, end=end, next_start=end, tokens=tokens)]
            return self._split_by_characters(text, start, end)

        pattern = (_PARAGRAPH_BREAK, _SENTENCE_BREAK, _WORD_BREAK)[level]
        segments = self._segments(text, start, end, pattern)

        if len(segments) <= 1:
            # This boundary did not divide the span, so trying to pack by it
            # would loop. Drop to the next one.
            return self._split_by(text, start, end, level=level + 1)

        pieces: list[_Piece] = []
        for segment_start, segment_end, segment_next in segments:
            inner = self._split_by(text, segment_start, segment_end, level=level + 1)
            if not inner:
                continue
            # The last inner piece inherits this segment's real successor, so
            # the pieces stay gap-free across the separator that was skipped.
            last = inner[-1]
            inner[-1] = _Piece(
                start=last.start,
                end=last.end,
                next_start=segment_next,
                tokens=last.tokens,
            )
            pieces.extend(inner)
        return pieces

    @staticmethod
    def _segments(
        text: str, start: int, end: int, pattern: re.Pattern[str]
    ) -> list[tuple[int, int, int]]:
        """Split on ``pattern``, as ``(start, content_end, next_start)``.

        ``content_end`` excludes the separator and ``next_start`` is past it,
        so chunks built from these never carry trailing whitespace and never
        skip a character either.
        """
        segments: list[tuple[int, int, int]] = []
        cursor = start
        for match in pattern.finditer(text, start, end):
            if match.start() > cursor:
                segments.append((cursor, match.start(), match.end()))
            cursor = match.end()
        if cursor < end:
            segments.append((cursor, end, end))
        return segments

    def _split_by_characters(self, text: str, start: int, end: int) -> list[_Piece]:
        """Last-resort split of a span with no usable boundary inside it."""
        span = text[start:end]
        tokens = max(self._tokenizer.count(span), 1)
        # Characters per token for *this* span specifically, rather than a
        # global guess: a run of CJK and a run of base64 differ by several
        # times, and a global constant would be wrong for both.
        chars_per_token = max(len(span) / tokens, 1.0)
        window = max(int(self._config.chunk_size_tokens * chars_per_token), 1)

        pieces: list[_Piece] = []
        cursor = start
        while cursor < end:
            stop = min(cursor + window, end)
            pieces.append(
                _Piece(
                    start=cursor,
                    end=stop,
                    next_start=stop,
                    tokens=self._tokenizer.count(text[cursor:stop]),
                )
            )
            cursor = stop
        return pieces

    # --- packing --------------------------------------------------------

    def _pack(self, document: CleanedDocument, pieces: list[_Piece]) -> list[DocumentChunk]:
        chunks: list[DocumentChunk] = []
        current: list[_Piece] = []
        current_tokens = 0

        limit = self._config.chunk_size_tokens

        for piece in pieces:
            if current and current_tokens + piece.tokens > limit:
                chunks.append(self._emit(document, current, len(chunks)))
                current = self._overlap_tail(current)
                current_tokens = sum(tail.tokens for tail in current)

                # Carrying overlap must not push the next chunk over the limit.
                # Where it would, the oldest overlap is dropped: staying inside
                # the size bound matters more than the full overlap, and a
                # single piece always fits on its own because _split
                # guarantees it.
                while current and current_tokens + piece.tokens > limit:
                    current_tokens -= current.pop(0).tokens

            current.append(piece)
            current_tokens += piece.tokens

        if current:
            chunks.append(self._emit(document, current, len(chunks)))

        return chunks

    def _overlap_tail(self, emitted: list[_Piece]) -> list[_Piece]:
        """Pieces from the end of ``emitted`` to repeat in the next chunk.

        Drawn from ``emitted[1:]`` only. Including the first piece would let
        the next chunk start exactly where this one did, so a document whose
        chunk is a single oversized piece would never advance -- the chunker
        would emit the same chunk forever.
        """
        if self._config.chunk_overlap_tokens == 0 or len(emitted) < 2:
            return []

        tail: list[_Piece] = []
        total = 0
        for piece in reversed(emitted[1:]):
            if total + piece.tokens > self._config.chunk_overlap_tokens:
                break
            tail.append(piece)
            total += piece.tokens
        tail.reverse()
        return tail

    def _emit(self, document: CleanedDocument, pieces: list[_Piece], index: int) -> DocumentChunk:
        start = pieces[0].start
        end = pieces[-1].end
        text = document.text[start:end]
        text_hash = content_hash(text)

        source_start = document.source_map.to_source(start)
        source_end = document.source_map.to_source(end)

        page_numbers = self._pages_covered(document, start, end)

        return DocumentChunk(
            chunk_id=build_chunk_id(document.document_id, index, text_hash),
            document_id=document.document_id,
            workspace_id=document.workspace_id,
            text=text,
            chunk_index=index,
            page_number=page_numbers[0] if page_numbers else None,
            page_numbers=page_numbers,
            # Counted on the emitted text rather than summed from the pieces,
            # because BPE merges across the joins between them.
            token_count=self._tokenizer.count(text),
            start_offset=start,
            end_offset=end,
            source_start_offset=source_start,
            source_end_offset=source_end,
            content_hash=text_hash,
        )

    @staticmethod
    def _pages_covered(document: CleanedDocument, start: int, end: int) -> tuple[int, ...]:
        """Every page the chunk's span touches, in order.

        Both ends are resolved rather than just the start: a chunk that crosses
        a page break belongs to both pages, and a citation naming only the
        first would be wrong about half its content.
        """
        if not document.pages:
            return ()

        first = document.page_for_clean_offset(start)
        last = document.page_for_clean_offset(max(end - 1, start))

        if first is None and last is None:
            return ()
        if first is None:
            return (last,)  # type: ignore[return-value]
        if last is None or last <= first:
            return (first,)
        return tuple(range(first, last + 1))
