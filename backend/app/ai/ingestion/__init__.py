"""Document ingestion: loading, normalisation and chunking.

The deterministic half of the pipeline. Nothing here calls a model or reaches
the network: the same bytes always produce the same text, the same cleaned
text and the same chunks with the same ids. That is what makes re-ingestion
safe to run and what lets a retrieval regression be traced to a change in this
code rather than to a model's mood.

    data -> ParserRegistry.parse -> TextCleaner.clean -> TextChunker.chunk

Embeddings and entity extraction consume the chunks; neither is implemented
yet.
"""

from app.ai.ingestion.chunking import (
    CHUNK_NAMESPACE,
    ChunkerConfig,
    TextChunker,
    build_chunk_id,
    content_hash,
)
from app.ai.ingestion.cleaning import TextCleaner
from app.ai.ingestion.markdown_parser import MarkdownParser
from app.ai.ingestion.models import (
    PAGE_SEPARATOR,
    CleanedDocument,
    DocumentChunk,
    ExtractedDocument,
    ExtractedPage,
    OffsetMap,
    OffsetRun,
)
from app.ai.ingestion.parser import DocumentParser, ParserRegistry, build_default_registry
from app.ai.ingestion.pdf_parser import PDFParser
from app.ai.ingestion.text_parser import TextParser
from app.ai.ingestion.tokenization import (
    DEFAULT_ENCODING,
    HeuristicTokenizer,
    TiktokenTokenizer,
    Tokenizer,
    get_tokenizer,
)

__all__ = [
    "CHUNK_NAMESPACE",
    "DEFAULT_ENCODING",
    "PAGE_SEPARATOR",
    "ChunkerConfig",
    "CleanedDocument",
    "DocumentChunk",
    "DocumentParser",
    "ExtractedDocument",
    "ExtractedPage",
    "HeuristicTokenizer",
    "MarkdownParser",
    "OffsetMap",
    "OffsetRun",
    "PDFParser",
    "ParserRegistry",
    "TextChunker",
    "TextCleaner",
    "TextParser",
    "TiktokenTokenizer",
    "Tokenizer",
    "build_chunk_id",
    "build_default_registry",
    "content_hash",
    "get_tokenizer",
]
