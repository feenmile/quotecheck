"""HTML to plain text with the standard-library ``html.parser``."""

from __future__ import annotations

import re
from html.parser import HTMLParser

_SKIP = frozenset({"script", "style", "template"})
_BLOCK = frozenset(
    {
        "address", "article", "aside", "blockquote", "body", "br", "caption",
        "dd", "details", "div", "dl", "dt", "figcaption", "figure", "footer",
        "form", "h1", "h2", "h3", "h4", "h5", "h6", "head", "header", "hr",
        "html", "li", "main", "nav", "ol", "p", "pre", "section", "summary",
        "table", "tbody", "td", "tfoot", "th", "thead", "title", "tr", "ul",
    }
)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if tag in _SKIP:
            self._skip_depth += 1
        elif tag in _BLOCK and not self._skip_depth:
            self.parts.append("\n")

    def handle_startendtag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if tag in _BLOCK and not self._skip_depth:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            if self._skip_depth:
                self._skip_depth -= 1
        elif tag in _BLOCK and not self._skip_depth:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def tidy(text: str) -> str:
    """Collapse horizontal whitespace, strip lines, squeeze blank lines."""
    lines = [re.sub(r"[ \t\f\v\r]+", " ", line).strip() for line in text.split("\n")]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()


def html_to_text(html: str) -> str:
    """Visible text of an HTML document; script, style and template dropped."""
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return tidy("".join(parser.parts))
