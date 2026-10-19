"""The parser interface, and selection of a parser for a document.

A parser turns bytes into :class:`~app.ai.ingestion.models.ExtractedDocument`:
text, page boundaries where the format has them, and format metadata. Parsers
do no interpretation -- no summarising, no LLM call, no judgement about what the
content means. That separation is what makes extraction deterministic and
therefore reproducible.

Selection is by MIME type, and the MIME type used is the one recorded on the
document, which the upload path determined by inspecting the bytes. A parser is
never chosen from a filename.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.ai.ingestion.models import ExtractedDocument
from app.core.content_detection import DocumentType
from app.core.exceptions import DocumentParseError


class DocumentParser(Protocol):
    """Turns the bytes of one document into text with provenance."""

    @property
    def supported_types(self) -> frozenset[DocumentType]:
        """Content types this parser handles."""
        ...

    def parse(self, data: bytes, *, document_id: UUID, workspace_id: UUID) -> ExtractedDocument:
        """Extract text from ``data``.

        Raises:
            DocumentParseError: the bytes could not be read as this format.
        """
        ...


class ParserRegistry:
    """Resolves the parser for a document's content type.

    A registry rather than a chain of ``if`` statements because adding a format
    should be adding a parser, not editing a dispatch function -- and because an
    unregistered type must fail loudly rather than fall through to a default
    that would guess.
    """

    def __init__(self, parsers: list[DocumentParser]) -> None:
        self._by_type: dict[DocumentType, DocumentParser] = {}
        for parser in parsers:
            for document_type in parser.supported_types:
                if document_type in self._by_type:
                    raise ValueError(f"Two parsers registered for {document_type.value}")
                self._by_type[document_type] = parser

    @property
    def supported_types(self) -> frozenset[DocumentType]:
        return frozenset(self._by_type)

    def for_type(self, document_type: DocumentType) -> DocumentParser:
        """The parser for ``document_type``.

        Raises:
            KeyError: no parser is registered. A programming error rather than
                a bad document: the upload path only accepts types the pipeline
                claims to handle, so the two lists disagreeing is a bug.
        """
        try:
            return self._by_type[document_type]
        except KeyError as exc:
            raise KeyError(f"No parser registered for {document_type.value}") from exc

    def parse(
        self,
        data: bytes,
        *,
        mime_type: str,
        document_id: UUID,
        workspace_id: UUID,
    ) -> ExtractedDocument:
        """Parse ``data`` using the parser for ``mime_type``.

        Raises:
            DocumentParseError: the type is not one we parse, or the bytes
                could not be read.
        """
        try:
            document_type = DocumentType(mime_type)
        except ValueError as exc:
            raise DocumentParseError(
                document_id, f"no parser for content type {mime_type!r}"
            ) from exc
        return self.for_type(document_type).parse(
            data, document_id=document_id, workspace_id=workspace_id
        )


def build_default_registry() -> ParserRegistry:
    """Registry covering every type the upload path accepts."""
    from app.ai.ingestion.markdown_parser import MarkdownParser
    from app.ai.ingestion.pdf_parser import PDFParser
    from app.ai.ingestion.text_parser import TextParser

    return ParserRegistry([PDFParser(), TextParser(), MarkdownParser()])
