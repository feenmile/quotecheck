"""quotecheck: verify that a quoted passage actually appears in its source.

Built by Feenmile.
"""

__version__ = "0.1.0"

from .core import NEAR_THRESHOLD, CheckResult, Status, check, check_text  # noqa: E402
from .fetch import FetchedSource, SourceError, SourceRefused, fetch_source  # noqa: E402

__all__ = [
    "NEAR_THRESHOLD",
    "CheckResult",
    "FetchedSource",
    "SourceError",
    "SourceRefused",
    "Status",
    "__version__",
    "check",
    "check_text",
    "fetch_source",
]
