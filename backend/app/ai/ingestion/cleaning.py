"""Whitespace and artefact normalisation that preserves provenance.

The constraint that shapes this module: **cleaning must not lose the ability to
point back at the original text.** A citation is ultimately a character span in
the document a person uploaded, so a cleaner that silently reindexes text
breaks citations in a way nobody notices until an answer quotes the wrong
sentence.

That is achieved by restricting cleaning to two kinds of edit:

* deleting a run of characters, and
* substituting a run with one of *exactly the same length*.

Neither can shift text by an amount the map cannot account for, so the
resulting :class:`~app.ai.ingestion.models.OffsetMap` is exact rather than
approximate. Any transformation that cannot be expressed this way -- Unicode
NFC normalisation, which changes length unpredictably, or paraphrasing of any
kind -- is out of scope by construction rather than by discipline.

What is *not* done here
-----------------------
Repeated headers and footers are left in place. Detecting them means deciding
that a line appearing on most pages is furniture rather than content, and that
judgement is wrong often enough -- a recurring section title, a company name in
a letterhead that is also the subject of the document -- that deleting the text
would lose evidence. It is a candidate for a later phase, with the frequency
analysis done across pages and the decision recorded rather than silent.
"""

from __future__ import annotations

import re
from typing import Final

from app.ai.ingestion.models import (
    CleanedDocument,
    ExtractedDocument,
    OffsetMap,
    OffsetRun,
)

#: One pass over the text. Alternation order is significant: the first
#: alternative that matches at a position wins, so document-level trimming is
#: listed before the rules that would otherwise claim the same whitespace.
_ARTEFACTS: Final = re.compile(
    r"""
      (?P<lead>\A\s+)                       # leading whitespace of the document
    | (?P<trail>\s+\Z)                      # trailing whitespace of the document
    | (?P<cr_lf>\r(?=\n))                   # CRLF: drop the CR, keep the LF
    | (?P<cr>\r)                            # lone CR, an old Mac line ending
    | (?P<invisible>[­​-‍⁠﻿]+)
    | (?P<hyphen>(?<=[A-Za-z])-\n(?=[a-z])) # word broken across a line
    | (?P<blanks>\n{3,})                    # more than one blank line
    # Before `runs`: whitespace at the end of a line is trailing, not
    # interior, and collapsing it to one space would leave the space behind.
    | (?P<eol_space>[ \t]+(?=\n))           # trailing spaces on a line
    | (?P<runs>(?<=\S)[ \t]{2,})            # repeated spacing inside a line
    """,
    re.VERBOSE,
)


class _Builder:
    """Accumulates cleaned text while recording where each piece came from."""

    def __init__(self) -> None:
        self._parts: list[str] = []
        self._runs: list[OffsetRun] = []
        self._length = 0

    def keep(self, source: str, start: int, end: int) -> None:
        """Copy ``source[start:end]`` through unchanged."""
        if end <= start:
            return
        self._append(source[start:end], start, end - start)

    def substitute(self, replacement: str, source_start: int) -> None:
        """Emit ``replacement`` in place of the same number of source characters.

        Equal length is the whole point; a shorter or longer replacement would
        make the offset map a lie, so it is rejected rather than tolerated.
        """
        self._append(replacement, source_start, len(replacement))

    def _append(self, text: str, source_start: int, length: int) -> None:
        self._parts.append(text)
        previous = self._runs[-1] if self._runs else None
        if (
            previous is not None
            and previous.source_start + previous.length == source_start
            and previous.clean_start + previous.length == self._length
        ):
            # Contiguous with the previous run in both coordinate systems, so
            # extend it. Without this the map would hold one run per match and
            # grow to the size of the document.
            self._runs[-1] = OffsetRun(
                clean_start=previous.clean_start,
                source_start=previous.source_start,
                length=previous.length + length,
            )
        else:
            self._runs.append(
                OffsetRun(
                    clean_start=self._length,
                    source_start=source_start,
                    length=length,
                )
            )
        self._length += length

    def build(self) -> tuple[str, OffsetMap]:
        return "".join(self._parts), OffsetMap(runs=tuple(self._runs))


class TextCleaner:
    """Normalises extracted text without breaking its link to the source.

    Deterministic and side-effect free: the same bytes always clean to the same
    text and the same map, which is what makes re-ingestion reproducible.
    """

    def clean_text(self, source: str) -> tuple[str, OffsetMap]:
        """Clean a string, returning the result and its offset map."""
        builder = _Builder()
        position = 0

        for match in _ARTEFACTS.finditer(source):
            builder.keep(source, position, match.start())
            self._emit(builder, source, match)
            position = match.end()

        builder.keep(source, position, len(source))
        return builder.build()

    @staticmethod
    def _emit(builder: _Builder, source: str, match: re.Match[str]) -> None:
        kind = match.lastgroup

        if kind == "cr":
            # Equal-length substitution: one CR becomes one LF.
            builder.substitute("\n", match.start())
        elif kind == "blanks":
            # Keep exactly one blank line. Paragraph boundaries are what the
            # chunker splits on, so they are preserved; the surplus is page-gap
            # noise from PDF extraction.
            builder.keep(source, match.start(), match.start() + 2)
        elif kind == "runs":
            # Collapse to a single space by keeping one character's worth of
            # position. Substituted rather than kept, so that a run beginning
            # with a tab still collapses to a space.
            builder.substitute(" ", match.start())
        # lead, trail, cr_lf, invisible, hyphen and eol_space are pure
        # deletions: nothing is emitted for them.

    def clean(self, document: ExtractedDocument) -> CleanedDocument:
        """Clean an extracted document, carrying its pages through."""
        text, source_map = self.clean_text(document.text)
        return CleanedDocument(
            document_id=document.document_id,
            workspace_id=document.workspace_id,
            text=text,
            source_map=source_map,
            pages=document.pages,
        )
