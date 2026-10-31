# quotecheck

**Does the quoted passage actually appear in the source?**

AI agents (and people) often cite real sources but misstate what those sources
say: real references, wrong claims. `quotecheck` fetches the cited source and
checks that the quoted text is really there, word for word, and tells you where.

```console
$ quotecheck "Mulberry trees" "https://en.wikipedia.org/wiki/Morus_(plant)"
{
  "status": "EXACT",
  "char_start": 9271,
  "char_end": 9285,
  "similarity": 1.0,
  "snippet": "Mulberry trees",
  "source_final_url": "https://en.wikipedia.org/wiki/Morus_(plant)",
  "reason": null
}
```

Built by Feenmile.

## Verdicts

| status | meaning |
| --- | --- |
| `EXACT` | The quote appears in the source text after normalizing whitespace, curly quotes and dash styles. Matching is case-sensitive. |
| `NEAR` | No exact match, but a passage with similarity >= 0.90 exists (difflib `SequenceMatcher` ratio). `snippet` holds the source's actual wording. |
| `NOT_FOUND` | Nothing in the retrievable text is close enough. `similarity` shows the best score found. |
| `SOURCE_UNAVAILABLE` | The source could not be fetched or read; `reason` says why. |

`char_start` / `char_end` are Python string indices (end exclusive) into the
plain text quotecheck extracted from the source, which you can get with
`quotecheck.fetch_source(source).text`.

**Read NEAR carefully.** A NEAR result means the wording differs. A changed
number ("43,548" vs "43,549") or a dropped "not" can still score above 0.90.
Use the returned `snippet` as the quotation, or rephrase as a paraphrase.

## Sources

- `https://...` or `http://...` URLs
- `doi:10.xxxx/...` (resolved through https://doi.org, following the publisher redirect)
- `pmid:12345` (the PubMed abstract, via NCBI E-utilities `efetch`, plain text)

## Install

Not yet published on PyPI. From a clone of this repository:

```console
pip install .            # library + CLI, no dependencies
pip install ".[mcp]"     # also the MCP server (needs the official mcp SDK)
```

Python 3.10 or newer.

## Quick start: CLI

```console
quotecheck "<quote>" <source>
quotecheck "the exact words" doi:10.1371/journal.pone.0000308
quotecheck --strict "the exact words" pmid:33301246   # exit 1 unless EXACT
echo "a long passage" | quotecheck - https://example.org/article
python -m quotecheck --help
```

Output is always JSON on stdout. Exit status is 0 when a verdict was printed
(1 with `--strict` unless EXACT) and 2 for usage errors such as an empty quote.

## Quick start: library

```python
import quotecheck

result = quotecheck.check("pmid:33301246", "A total of 43,548 participants underwent randomization")
print(result.status, result.char_start, result.char_end, result.similarity)
print(result.to_dict())

# Already have the text? Skip the fetch.
quotecheck.check_text(text, quote, near_threshold=0.90)
```

The core library uses only the Python standard library.

## Quick start: MCP server

The server speaks MCP over stdio and exposes one tool, `check_quote(quote, source)`.

```json
{
  "mcpServers": {
    "quotecheck": {
      "command": "quotecheck-mcp"
    }
  }
}
```

If the script is not on your `PATH`, use `"command": "python", "args": ["-m", "quotecheck.mcp_server"]`.

The tool description tells the agent to call it before presenting text as a
direct quotation, and how to act on each verdict.

## Safe fetching

quotecheck fetches URLs that came from model output, so it treats them as untrusted:

- only `http` and `https`; URLs with embedded credentials are refused;
- the host is resolved and **every** resolved address must be public: private,
  loopback, link-local, multicast, reserved and unspecified addresses
  (including IPv4-mapped IPv6 forms) are refused; the connection then goes to
  the checked address, so a second DNS answer cannot swap in an internal one;
- the same checks run again on every redirect, with at most 5 redirects;
- one 15 second deadline covers the whole fetch, and the body is capped at
  5 MB (declared, streamed and after decompression);
- requests identify as `quotecheck/0.1 (+https://github.com/feenmile/quotecheck)`;
- proxy environment variables are not used.

## Limits (v0.1)

- **EXACT means the text appears, not that the claim is true.** quotecheck does
  not judge context, meaning, or whether the source is reliable. A quote can
  be verbatim and still be used to support something the source does not say.
- **No PDFs.** PDF responses return `SOURCE_UNAVAILABLE` with the reason
  `pdf not supported in v0.1`. Many DOIs resolve to a landing page whose
  abstract is HTML; the full text may only be in a PDF.
- **No JavaScript rendering.** Pages that build their text in the browser come
  back empty (`SOURCE_UNAVAILABLE`) or incomplete (`NOT_FOUND`).
- **Paywalls, logins, cookie walls and bot blocks.** quotecheck sees what an
  anonymous client sees. Text behind them reads as `NOT_FOUND` or
  `SOURCE_UNAVAILABLE`, which does not mean the quote is wrong.
- **PubMed is abstract-only.** `pmid:` checks the abstract text, not the full paper.
- **Ellipses are literal.** A quote that skips words with "..." will not match
  exactly; check each fragment separately.
- **HTML extraction is simple.** Text is taken from the standard-library HTML
  parser with script, style and template removed. Navigation, footers and
  hidden elements are included; text split across unusual markup may not join
  the way it reads on screen.
- **Unicode.** Characters are NFKC-normalized one at a time; the same accented
  letter written in composed and decomposed forms will not match exactly.
- Fuzzy search is anchored on words shared with the quote. Quotes longer than
  20,000 characters are checked for exact matches only.
- Pages larger than 5 MB are refused rather than truncated.

## Development

```console
python -m unittest discover -s tests -t .
python scripts/mutation_check.py
```

The tests are offline. They run a local `http.server` fixture. Because the
SSRF guard blocks loopback, tests exempt specific fixture `(ip, port)` pairs
with `quotecheck._testing.allow_loopback_endpoints`, a test-only switch that is
off unless a test turns it on. A second fixture server is never exempted, so
the tests can prove that a redirect into loopback is refused.

`scripts/mutation_check.py` copies the project to a temporary directory,
disables the address check, and confirms the suite fails.

## License

MIT. See [LICENSE](LICENSE).
