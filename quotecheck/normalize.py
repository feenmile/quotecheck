"""Text normalization with a map back to the original character positions.

Normalization is deliberately small and predictable:

* curly / typographic quotes become straight quotes (' and ");
* the various Unicode dashes and the minus sign become "-";
* every run of whitespace becomes a single space, and leading/trailing
  whitespace is dropped;
* soft hyphens and zero-width characters are removed;
* each remaining character is passed through NFKC (so ligatures such as
  "fi" and the ellipsis character expand to plain letters / "...").

Case is preserved: matching is case-sensitive.
"""

from __future__ import annotations

import unicodedata

_SINGLE_QUOTES = "‘’‚‛′‵ʼ＇"
_DOUBLE_QUOTES = "“”„‟″‶«»＂"
_DASHES = "‐‑‒–—―−﹘﹣－"

_CHAR_MAP: dict[str, str] = {}
_CHAR_MAP.update({c: "'" for c in _SINGLE_QUOTES})
_CHAR_MAP.update({c: '"' for c in _DOUBLE_QUOTES})
_CHAR_MAP.update({c: "-" for c in _DASHES})

# Characters that carry no visible text and are removed outright.
_DROP = frozenset("­​‌‍⁠﻿")


def normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Return ``(normalized, index_map)``.

    ``index_map[i]`` is the index in ``text`` of the character that produced
    ``normalized[i]``. A collapsed whitespace run maps to its first character.
    """
    out: list[str] = []
    index: list[int] = []
    pending_space: int | None = None

    for i, ch in enumerate(text):
        if ch in _DROP:
            continue
        if ch.isspace():
            if out and pending_space is None:
                pending_space = i
            continue
        mapped = _CHAR_MAP.get(ch)
        if mapped is None:
            mapped = unicodedata.normalize("NFKC", ch)
        for piece in mapped:
            if piece.isspace():
                if out and pending_space is None:
                    pending_space = i
                continue
            if pending_space is not None:
                out.append(" ")
                index.append(pending_space)
                pending_space = None
            out.append(_CHAR_MAP.get(piece, piece))
            index.append(i)

    return "".join(out), index


def normalize(text: str) -> str:
    """Normalize ``text`` (see module docstring) without the position map."""
    return normalize_with_map(text)[0]
