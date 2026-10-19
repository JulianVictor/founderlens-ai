"""Markdown extraction."""

from __future__ import annotations

import re
from typing import Final
from uuid import UUID

from app.ai.ingestion.models import ExtractedDocument
from app.ai.ingestion.text_parser import decode_text
from app.core.content_detection import DocumentType
from app.core.exceptions import DocumentParseError

#: A YAML front-matter block: three dashes, content, three dashes, at the very
#: top of the file.
_FRONT_MATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)

#: ATX headings, used to record the document's outline.
_HEADING = re.compile(r"^(#{1,6})[ \t]+(\S.*?)[ \t]*#*[ \t]*$", re.MULTILINE)

_MAX_RECORDED_HEADINGS: Final = 50


class MarkdownParser:
    """Extracts text from a Markdown file, keeping its markup.

    The markup is deliberately *not* stripped. Headings, list markers and code
    fences are the document's structure, and the chunker splits on that
    structure; removing it would leave a wall of prose that chunks worse. It
    also reads correctly when a citation is displayed, which a de-marked-up
    approximation would not.

    Front matter is the exception: a YAML block is metadata about the document
    rather than part of it, and leaving it in would put ``title: ...`` into the
    first chunk as though the author had written it as prose.
    """

    @property
    def supported_types(self) -> frozenset[DocumentType]:
        return frozenset({DocumentType.MARKDOWN})

    def parse(self, data: bytes, *, document_id: UUID, workspace_id: UUID) -> ExtractedDocument:
        if not data:
            raise DocumentParseError(document_id, "file is empty")

        raw = decode_text(data, document_id)
        text, metadata = self._split_front_matter(raw)

        if not text.strip():
            raise DocumentParseError(document_id, "file contains no text")

        headings = _HEADING.findall(text)
        if headings:
            metadata["headings"] = " | ".join(
                title for _, title in headings[:_MAX_RECORDED_HEADINGS]
            )[:2000]
        metadata["heading_count"] = str(len(headings))

        return ExtractedDocument(
            document_id=document_id,
            workspace_id=workspace_id,
            mime_type=DocumentType.MARKDOWN.value,
            text=text,
            pages=(),
            metadata=metadata,
        )

    @staticmethod
    def _split_front_matter(raw: str) -> tuple[str, dict[str, str]]:
        """Remove a leading YAML block, recording its scalar keys.

        Parsed with a line split rather than a YAML library: this is untrusted
        input, the only thing wanted from it is a few top-level strings, and a
        full YAML parser is a much larger surface than that warrants.
        """
        match = _FRONT_MATTER.match(raw)
        if match is None:
            return raw, {}

        metadata: dict[str, str] = {}
        for line in match.group(1).splitlines():
            key, separator, value = line.partition(":")
            if not separator:
                continue
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and value and key.isidentifier():
                metadata[f"frontmatter_{key}"] = value[:500]

        return raw[match.end() :], metadata
