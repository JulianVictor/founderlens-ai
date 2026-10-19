"""Plain-text extraction."""

from __future__ import annotations

from uuid import UUID

from app.ai.ingestion.models import ExtractedDocument
from app.core.content_detection import DocumentType
from app.core.exceptions import DocumentParseError

#: Byte-order marks that some editors prepend. Stripped before decoding so the
#: mark does not become a character at offset 0 of every such document.
_BOM = "\ufeff"


def decode_text(data: bytes, document_id: UUID) -> str:
    """Decode ``data`` as UTF-8, or fail with an explanation.

    Decoding is strict. The upload check sampled only the first 8 KB, so a file
    can reach here and still turn out to be invalid further in -- and replacing
    bad bytes with U+FFFD would silently corrupt the text a citation will later
    quote. A document that cannot be decoded is a failed document, not a
    document full of question marks.
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DocumentParseError(
            document_id,
            f"file is not valid UTF-8 (byte {exc.start})",
        ) from exc
    return text.removeprefix(_BOM)


class TextParser:
    """Extracts text from a plain-text file.

    There is no structure to recover and nothing to interpret, so this is a
    decode and nothing more. Normalisation is deliberately left to the cleaner:
    doing it here would mean the extracted text -- the thing source offsets
    index -- was already a transformation of the file, and provenance would
    start one step away from the original.
    """

    @property
    def supported_types(self) -> frozenset[DocumentType]:
        return frozenset({DocumentType.PLAIN_TEXT})

    def parse(self, data: bytes, *, document_id: UUID, workspace_id: UUID) -> ExtractedDocument:
        if not data:
            raise DocumentParseError(document_id, "file is empty")

        text = decode_text(data, document_id)
        if not text.strip():
            raise DocumentParseError(document_id, "file contains no text")

        return ExtractedDocument(
            document_id=document_id,
            workspace_id=workspace_id,
            mime_type=DocumentType.PLAIN_TEXT.value,
            text=text,
            pages=(),
            metadata={"line_count": str(text.count("\n") + 1)},
        )
