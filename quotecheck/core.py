"""The quotecheck verdict: does the quoted passage appear in the source?"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from enum import Enum

from .fetch import DEFAULT_TIMEOUT, SourceError, fetch_source
from .match import best_window
from .normalize import normalize_with_map

NEAR_THRESHOLD = 0.90


class Status(str, Enum):
    EXACT = "EXACT"
    NEAR = "NEAR"
    NOT_FOUND = "NOT_FOUND"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"

    def __str__(self) -> str:
        return self.value


@dataclass
class CheckResult:
    """Outcome of a check.

    ``char_start``/``char_end`` index (Python ``str`` indices, end exclusive)
    into the plain text extracted from the source, i.e.
    ``fetch_source(source).text`` (or the ``text`` passed to ``check_text``).
    They are ``None`` unless the status is EXACT or NEAR.
    """

    status: Status
    char_start: int | None = None
    char_end: int | None = None
    similarity: float = 0.0
    snippet: str | None = None
    source_final_url: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d


def _floor4(x: float) -> float:
    return math.floor(x * 10000) / 10000


def _validate(quote: str, near_threshold: float) -> None:
    if not isinstance(quote, str):
        raise TypeError("quote must be a string")
    if not 0.0 < near_threshold <= 1.0:
        raise ValueError("near_threshold must be in (0, 1]")


def check_text(text: str, quote: str, *, near_threshold: float = NEAR_THRESHOLD) -> CheckResult:
    """Check ``quote`` against already-loaded plain ``text``."""
    _validate(quote, near_threshold)
    qn, _ = normalize_with_map(quote)
    if not qn:
        raise ValueError("quote is empty")
    sn, smap = normalize_with_map(text)

    i = sn.find(qn)
    if i >= 0:
        start, end = smap[i], smap[i + len(qn) - 1] + 1
        return CheckResult(Status.EXACT, start, end, 1.0, text[start:end])

    window = best_window(sn, qn)
    if window is not None and window.ratio >= near_threshold:
        start, end = smap[window.start], smap[window.end - 1] + 1
        return CheckResult(Status.NEAR, start, end, _floor4(window.ratio), text[start:end])

    return CheckResult(
        Status.NOT_FOUND,
        similarity=_floor4(window.ratio) if window else 0.0,
        reason=f"best fuzzy match is below the {near_threshold:g} threshold",
    )


def check(source: str, quote: str, *, near_threshold: float = NEAR_THRESHOLD,
          timeout: float = DEFAULT_TIMEOUT) -> CheckResult:
    """Fetch ``source`` (http(s) URL, ``doi:10.x/...`` or ``pmid:123``) and check ``quote``.

    EXACT means the quote's words appear in the source text; it does not mean
    the claim built on the quote is true.
    """
    _validate(quote, near_threshold)
    if not normalize_with_map(quote)[0]:
        raise ValueError("quote is empty")
    try:
        fetched = fetch_source(source, timeout=timeout)
    except SourceError as exc:
        return CheckResult(Status.SOURCE_UNAVAILABLE, source_final_url=exc.final_url,
                           reason=exc.reason)
    result = check_text(fetched.text, quote, near_threshold=near_threshold)
    result.source_final_url = fetched.final_url
    return result
