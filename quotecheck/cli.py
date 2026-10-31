"""Command line: ``quotecheck "<quote>" <source>`` prints a JSON verdict."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .core import NEAR_THRESHOLD, Status, check
from .fetch import DEFAULT_TIMEOUT


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="quotecheck",
        description="Verify that a quoted passage appears word for word in a source.",
        epilog="Built by Feenmile.",
    )
    parser.add_argument("quote", help='the quoted passage (use "-" to read it from stdin)')
    parser.add_argument("source", help="http(s) URL, doi:10.xxxx/..., or pmid:12345")
    parser.add_argument("--near-threshold", type=float, default=NEAR_THRESHOLD,
                        help="minimum similarity for NEAR (default %(default)s)")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                        help="overall fetch timeout in seconds (default %(default)s)")
    parser.add_argument("--strict", action="store_true",
                        help="exit with status 1 unless the verdict is EXACT")
    parser.add_argument("--version", action="version", version=f"quotecheck {__version__}")
    args = parser.parse_args(argv)

    quote = sys.stdin.read() if args.quote == "-" else args.quote
    try:
        result = check(args.source, quote, near_threshold=args.near_threshold,
                       timeout=args.timeout)
    except (TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=True))
    if args.strict and result.status is not Status.EXACT:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
