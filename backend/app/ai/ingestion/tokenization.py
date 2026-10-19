"""Token counting for chunk sizing.

Chunks are sized in tokens rather than characters because the thing that
constrains them is a model's context and embedding window, which is measured in
tokens. Characters are a poor proxy: the ratio swings by more than 2x between
prose, code, tables of numbers, and non-Latin scripts, so a character budget
either wastes most of the window or silently overruns it.

Two implementations, because the right one is not always reachable:

* :class:`TiktokenTokenizer` -- the real BPE used by the configured embedding
  model. Correct, and what production should run.
* :class:`HeuristicTokenizer` -- an offline approximation. ``tiktoken``
  downloads its encoding table on first use, so a sandboxed CI box or an
  air-gapped deployment cannot load it. Refusing to chunk at all in that case
  would be worse than chunking slightly imprecisely: the consequence of a bad
  estimate is a chunk a little over or under its target, not a wrong answer.

The fallback is a deliberate, logged degradation, never a silent one.
"""

from __future__ import annotations

import logging
import re
from typing import Final, Protocol, runtime_checkable

logger: Final = logging.getLogger(__name__)

#: Encoding used by the OpenAI embedding models this project is configured for
#: (``text-embedding-3-*`` and the GPT-4 family).
DEFAULT_ENCODING: Final = "cl100k_base"


@runtime_checkable
class Tokenizer(Protocol):
    """Counts tokens in a string.

    Counting is all the chunker needs. It deliberately does not encode and slice
    token arrays, because decoding a token slice back to text loses the
    character offsets that every citation depends on -- so the chunker splits on
    text boundaries and uses this only to decide how much fits.
    """

    @property
    def name(self) -> str:
        """Identifier recorded alongside chunks, so a count can be explained."""
        ...

    def count(self, text: str) -> int:
        """Number of tokens ``text`` encodes to."""
        ...


class TiktokenTokenizer:
    """Exact token counts from ``tiktoken``."""

    def __init__(self, encoding_name: str = DEFAULT_ENCODING) -> None:
        import tiktoken

        self._encoding_name = encoding_name
        self._encoding = tiktoken.get_encoding(encoding_name)

    @property
    def name(self) -> str:
        return f"tiktoken:{self._encoding_name}"

    def count(self, text: str) -> int:
        if not text:
            return 0
        # disallowed_special=() so that text which happens to contain a special
        # token's literal spelling is counted as ordinary text instead of
        # raising. A document is data; it is not addressing the tokenizer.
        return len(self._encoding.encode(text, disallowed_special=()))


class HeuristicTokenizer:
    """Offline approximation of BPE token counts.

    Counts word-like runs, punctuation and whitespace-separated pieces the way a
    byte-pair encoder roughly does: common short words cost one token, long
    words split into several, and each punctuation mark is usually its own.

    It is an estimate and is documented as one. Measured against ``cl100k_base``
    on English prose it lands within roughly 10-15%, which is close enough to
    keep chunks near their target size.
    """

    #: Word characters, or any single non-space character (punctuation, CJK).
    _PIECE = re.compile(r"\w+|[^\w\s]", re.UNICODE)

    #: Characters per token inside a long word. BPE merges common sequences, so
    #: a long word is several tokens rather than one.
    _CHARS_PER_TOKEN: Final = 4

    @property
    def name(self) -> str:
        return "heuristic"

    def count(self, text: str) -> int:
        if not text:
            return 0

        total = 0
        for piece in self._PIECE.findall(text):
            if len(piece) <= self._CHARS_PER_TOKEN:
                total += 1
            else:
                total += -(-len(piece) // self._CHARS_PER_TOKEN)  # ceiling

        # Runs of whitespace are not free: a newline is its own token in most
        # BPE vocabularies, and blank lines between paragraphs add up.
        total += text.count("\n")
        return max(total, 1)


def get_tokenizer(encoding_name: str = DEFAULT_ENCODING) -> Tokenizer:
    """The best tokenizer available in this environment.

    Prefers the real encoding and falls back to the heuristic, logging a warning
    so that an environment silently running on estimates is visible in logs
    rather than discovered later from odd chunk sizes.
    """
    try:
        return TiktokenTokenizer(encoding_name)
    except Exception as exc:
        # ImportError when the package is absent; network and filesystem errors
        # when the encoding table cannot be fetched or cached.
        logger.warning(
            "tiktoken encoding %r unavailable (%s: %s); falling back to approximate token counts",
            encoding_name,
            type(exc).__name__,
            exc,
        )
        return HeuristicTokenizer()
