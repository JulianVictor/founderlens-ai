"""PDF text extraction with PyMuPDF."""

from __future__ import annotations

import logging
from typing import Final
from uuid import UUID

import pymupdf

from app.ai.ingestion.models import PAGE_SEPARATOR, ExtractedDocument, ExtractedPage
from app.core.content_detection import DocumentType
from app.core.exceptions import DocumentParseError

logger: Final = logging.getLogger(__name__)

#: PDF metadata keys worth carrying forward. The dictionary PyMuPDF returns has
#: more, but the rest is producer trivia that would only pad the record.
_METADATA_KEYS: Final = ("title", "author", "subject", "creationDate")


class PDFParser:
    """Extracts page text from a PDF.

    Page structure is preserved rather than flattened, because a page number is
    the unit a person uses to check a citation -- "page 7 of the filing" is
    verifiable in a way that a character offset into a concatenated blob is not.

    Extraction is in reading order as PyMuPDF determines it. No layout analysis,
    column detection or table reconstruction: those are heuristics that can be
    wrong, and a wrong reconstruction is harder to detect downstream than
    slightly awkward reading order.
    """

    @property
    def supported_types(self) -> frozenset[DocumentType]:
        return frozenset({DocumentType.PDF})

    def parse(self, data: bytes, *, document_id: UUID, workspace_id: UUID) -> ExtractedDocument:
        if not data:
            raise DocumentParseError(document_id, "file is empty")

        try:
            document = pymupdf.open(stream=data, filetype="pdf")
        except Exception as exc:
            raise DocumentParseError(document_id, "not a readable PDF") from exc

        try:
            if document.needs_pass:
                # An encrypted PDF is a real document we cannot read, and the
                # user can fix it by uploading a decrypted copy -- so it gets
                # its own message rather than "not readable".
                raise DocumentParseError(document_id, "PDF is password protected")

            pages: list[ExtractedPage] = []
            parts: list[str] = []
            offset = 0

            # Iterated by index rather than by iterating the document, so the
            # page number is unambiguous and a failure names the page it came
            # from.
            for zero_based in range(document.page_count):
                index = zero_based + 1
                try:
                    text = str(document.load_page(zero_based).get_text("text"))
                except Exception as exc:
                    raise DocumentParseError(
                        document_id, f"page {index} could not be read"
                    ) from exc

                if index > 1:
                    # The separator belongs to the document text and therefore
                    # to the offset arithmetic, but to no page.
                    parts.append(PAGE_SEPARATOR)
                    offset += len(PAGE_SEPARATOR)

                parts.append(text)
                pages.append(
                    ExtractedPage(
                        page_number=index,
                        text=text,
                        start_offset=offset,
                        end_offset=offset + len(text),
                    )
                )
                offset += len(text)

            metadata = self._metadata(document)
        finally:
            document.close()

        full_text = "".join(parts)
        if not full_text.strip():
            # Almost always a scanned PDF: pages of images with no text layer.
            # Saying so points at the fix (OCR) instead of leaving someone to
            # wonder why a visibly full document produced nothing.
            raise DocumentParseError(
                document_id,
                "PDF contains no extractable text (it may be a scan needing OCR)",
            )

        return ExtractedDocument(
            document_id=document_id,
            workspace_id=workspace_id,
            mime_type=DocumentType.PDF.value,
            text=full_text,
            pages=tuple(pages),
            metadata=metadata,
        )

    @staticmethod
    def _metadata(document: pymupdf.Document) -> dict[str, str]:
        raw = document.metadata or {}
        found: dict[str, str] = {}
        for key in _METADATA_KEYS:
            value = raw.get(key)
            # Values come from the file and are not trusted to be strings, to be
            # non-empty, or to mean what they say.
            if isinstance(value, str) and value.strip():
                found[key] = value.strip()[:500]
        found["page_count"] = str(document.page_count)
        return found
