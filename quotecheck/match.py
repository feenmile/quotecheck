"""Exact and fuzzy location of a (normalized) quote inside (normalized) text.

Fuzzy search avoids sliding a window over the whole document. Instead:

1. every word of the quote that also occurs in the text "votes" for the
   window start it implies (occurrence position minus the word's offset in
   the quote);
2. the most-voted start positions become candidates;
3. around each candidate, windows aligned to whitespace-delimited tokens are
   scored with ``difflib.SequenceMatcher(None, window, quote).ratio()`` and
   the best one wins.

The reported similarity is exactly that difflib ratio for the returned window.
"""

from __future__ import annotations

import bisect
import difflib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass

_WORD = re.compile(r"\w+")
_TOKEN = re.compile(r"\S+")

# Words occurring more often than this in the source are too common to vote.
_MAX_OCCURRENCES_PER_WORD = 5000
# Quotes longer than this are only checked for exact matches.
MAX_FUZZY_QUOTE_CHARS = 20000


@dataclass(frozen=True)
class Window:
    start: int  # inclusive, index into the normalized text
    end: int  # exclusive
    ratio: float


def similarity(window_text: str, quote: str) -> float:
    """The similarity measure quotecheck reports (difflib ratio)."""
    return difflib.SequenceMatcher(None, window_text, quote, autojunk=False).ratio()


def _nearest(values: list[int], lo: int, hi: int, target: int, limit: int) -> list[int]:
    """Up to ``limit`` items of sorted ``values`` within [lo, hi], closest to target."""
    a = bisect.bisect_left(values, lo)
    b = bisect.bisect_right(values, hi)
    chosen = values[a:b]
    if len(chosen) > limit:
        chosen = sorted(chosen, key=lambda v: abs(v - target))[:limit]
    return chosen


def best_window(text: str, quote: str, max_candidates: int = 24) -> Window | None:
    """Best fuzzy window of ``text`` for ``quote`` (both already normalized)."""
    qlen = len(quote)
    if not qlen or not text or qlen > MAX_FUZZY_QUOTE_CHARS:
        return None

    qwords = [(m.group().lower(), m.start()) for m in _WORD.finditer(quote)]
    if not qwords:
        return None
    wanted = {w for w, _ in qwords}

    occurrences: dict[str, list[int]] = defaultdict(list)
    for m in _WORD.finditer(text):
        w = m.group().lower()
        if w in wanted:
            occurrences[w].append(m.start())

    bucket = max(8, qlen // 8)
    votes: Counter[int] = Counter()
    starts_in_bucket: dict[int, list[int]] = defaultdict(list)
    for word, qoff in qwords:
        occ = occurrences.get(word, ())
        if len(occ) > _MAX_OCCURRENCES_PER_WORD:
            continue
        for pos in occ:
            s = pos - qoff
            votes[s // bucket] += 1
            starts_in_bucket[s // bucket].append(s)
    if not votes:
        return None

    centers = []
    for b, _ in votes.most_common(max_candidates):
        ss = sorted(starts_in_bucket[b])
        centers.append(ss[len(ss) // 2])

    tok_starts: list[int] = []
    tok_ends: list[int] = []
    for m in _TOKEN.finditer(text):
        tok_starts.append(m.start())
        tok_ends.append(m.end())

    matcher = difflib.SequenceMatcher(None, autojunk=False)
    matcher.set_seq2(quote)
    cache: dict[tuple[int, int], float] = {}

    def score(s: int, e: int) -> float:
        key = (s, e)
        if key not in cache:
            matcher.set_seq1(text[s:e])
            cache[key] = matcher.ratio()
        return cache[key]

    def ends_for(s: int, limit: int) -> list[int]:
        return _nearest(tok_ends, s + int(qlen * 0.75), s + int(qlen * 1.25) + 1, s + qlen, limit)

    slack = max(12, qlen // 5)

    # Phase 1: best start per candidate, end placed near start + len(quote).
    scored: list[tuple[float, int, int]] = []
    for c in centers:
        for s in _nearest(tok_starts, c - slack, c + slack, c, 9):
            for e in ends_for(s, 1):
                scored.append((score(s, e), s, e))
    if not scored:
        return None
    scored.sort(reverse=True)

    # Phase 2: for the strongest starts, try a range of ends.
    best = scored[0]
    for _, s, _ in scored[:5]:
        for e in ends_for(s, 15):
            r = score(s, e)
            if r > best[0]:
                best = (r, s, e)

    # Phase 3: with that end fixed, try nearby starts.
    _, bs, be = best
    for s in _nearest(tok_starts, bs - slack, bs + slack, bs, 15):
        if s < be:
            r = score(s, be)
            if r > best[0]:
                best = (r, s, be)

    r, s, e = best
    return Window(start=s, end=e, ratio=r)
