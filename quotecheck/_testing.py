"""TEST ONLY helpers. Never use these in production code.

The SSRF guard refuses loopback addresses, which also blocks a local test
fixture server. ``allow_loopback_endpoints`` exempts specific loopback
``(ip, port)`` pairs for the duration of a ``with`` block. It must be enabled
explicitly; nothing in quotecheck turns it on.
"""

from __future__ import annotations

import ipaddress
from contextlib import contextmanager
from typing import Iterator

from . import fetch


@contextmanager
def allow_loopback_endpoints(*endpoints: tuple[str, int]) -> Iterator[None]:
    added: set[tuple[str, int]] = set()
    for host, port in endpoints:
        addr = ipaddress.ip_address(host)
        if not addr.is_loopback:
            raise ValueError(f"only loopback addresses can be exempted, got {host}")
        key = (str(addr), int(port))
        if key not in fetch._TEST_ALLOWED_LOOPBACK:
            added.add(key)
    fetch._TEST_ALLOWED_LOOPBACK.update(added)
    try:
        yield
    finally:
        fetch._TEST_ALLOWED_LOOPBACK.difference_update(added)
