"""Task 3 core: incremental PII redaction over a stream of text chunks.

Never accumulates the full response. After each chunk we redact and emit
everything up to a safe cut point; only the tail that could still be the
prefix of a longer match is held back (bounded per pattern), so Time To
First Token stays low.
"""
from __future__ import annotations

import re

REDACTED = "[REDACTED]"

# Full-match patterns (bounded so a single match has a known max length).
PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),  # SSN (11 chars)
    # Linear-time card shape: 4 groups of 4 digits (optional single separator),
    # final group 1-4 digits. Matches 4111 1111 1111 1111 and 4111111111111111.
    re.compile(r"\b(?:\d{4}[ -]?){3}\d{1,4}\b"),  # credit card, 13-16 digits
    re.compile(r"\b[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,}\b"),  # email
]

# Suffix-anchored prefix patterns: the longest buffer suffix that could still
# grow into a full match. Bounded by RFC limits (local part <=64, domain <=255).
PREFIX_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\d{0,3}(?:-\d{0,2}(?:-\d{0,4})?)?$"),
    re.compile(r"(?:\d{4}[ -]?){0,3}\d{0,3}[ -]?$"),
    re.compile(r"[A-Za-z0-9._%+-]{1,64}(?:@[A-Za-z0-9.-]{0,253}(?:\.[A-Za-z]{0,2})?)?$"),
]


def redact_text(text: str) -> str:
    for pat in PATTERNS:
        text = pat.sub(REDACTED, text)
    return text


def _pending_prefix_len(buf: str) -> int:
    """Length of the longest suffix of buf that is a prefix of some match."""
    hold = 0
    for pat in PREFIX_PATTERNS:
        # No early break: the longest suffix corresponds to the rightmost
        # match start, which is not necessarily the first finditer hit.
        for m in pat.finditer(buf):
            hold = max(hold, len(buf) - m.start())
    return hold


class StreamRedactor:
    """Feed chunks with .feed(chunk) -> redacted output; call .flush() at EOS."""

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, chunk: str) -> str:
        self._buf += chunk
        cut = len(self._buf) - _pending_prefix_len(self._buf)
        if cut <= 0:
            return ""
        # A complete match must never be split across the cut.
        for pat in PATTERNS:
            for m in pat.finditer(self._buf):
                if m.start() < cut < m.end():
                    cut = m.start()
                    break
        if cut <= 0:
            return ""
        emit, self._buf = self._buf[:cut], self._buf[cut:]
        return redact_text(emit)

    def flush(self) -> str:
        out, self._buf = redact_text(self._buf), ""
        return out


def redact_stream(chunks: list[str]) -> list[str]:
    """Convenience: redact a whole chunk list, returning per-chunk outputs."""
    r = StreamRedactor()
    return [r.feed(c) for c in chunks] + [r.flush()]
