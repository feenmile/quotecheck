"""MCP server (stdio) exposing one tool: ``check_quote(quote, source)``.

Requires the optional ``mcp`` extra: ``pip install "quotecheck[mcp]"``.
The core library does not import this module.
"""

from __future__ import annotations

import asyncio
import sys

from .core import check

TOOL_DESCRIPTION = """\
Verify that a quoted passage really appears, word for word, in a cited source.
Call this before you present text as a direct quotation from a URL, DOI or
PubMed record, and after you draft citations, to catch quotes that were
paraphrased, misremembered or invented.

Arguments:
- quote: the exact passage you are attributing to the source.
- source: an http(s) URL, "doi:10.xxxx/..." (resolved via doi.org) or
  "pmid:12345" (the PubMed abstract).

Returns JSON with:
- status: EXACT (appears verbatim, ignoring whitespace, curly quotes and dash
  style; case-sensitive), NEAR (a passage with similarity >= 0.90 exists:
  quote the returned snippet instead), NOT_FOUND (not in the retrievable
  text: do not present it as a quotation), or SOURCE_UNAVAILABLE (could not
  fetch or read the source; see reason; PDFs are not supported yet).
- char_start / char_end: position in the extracted plain text.
- similarity, snippet (the matching source text), source_final_url, reason.

EXACT only proves the words are in the source. It does not prove the claim
you build on them is true or that the quote is used in context. NOT_FOUND can
also mean the text sits behind a paywall, a login or JavaScript rendering."""


def build_server():
    """Create the FastMCP server. Raises ImportError if ``mcp`` is missing."""
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("quotecheck")

    @server.tool(name="check_quote", description=TOOL_DESCRIPTION)
    async def check_quote(quote: str, source: str) -> dict:
        result = await asyncio.to_thread(check, source, quote)
        return result.to_dict()

    return server


def main() -> int:
    try:
        server = build_server()
    except ImportError:
        print('quotecheck-mcp needs the MCP SDK: pip install "quotecheck[mcp]"', file=sys.stderr)
        return 1
    server.run()  # stdio transport
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
