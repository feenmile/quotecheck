"""Safe fetching of a source document as plain text.

Safety rules applied to every request, including every redirect hop:

* only ``http`` and ``https`` URLs; no credentials embedded in the URL;
* the host name is resolved once, every resolved address must be public
  (private, loopback, link-local, multicast, reserved and unspecified
  addresses are refused), and the connection is made to that exact checked
  address, so a second DNS answer cannot swap in an internal one;
* at most 5 redirects;
* one overall 15 second deadline for the whole fetch;
* a 5 MB cap on the response body (declared or streamed, and after
  decompression).

Environment proxy settings are not used.
"""

from __future__ import annotations

import codecs
import http.client
import ipaddress
import re
import socket
import ssl
import time
import zlib
from dataclasses import dataclass
from email.message import Message
from urllib.parse import quote, urldefrag, urljoin, urlsplit

from .htmltext import html_to_text, tidy

USER_AGENT = "quotecheck/0.1 (+https://github.com/feenmile/quotecheck)"
MAX_BYTES = 5 * 1024 * 1024
DEFAULT_TIMEOUT = 15.0
MAX_REDIRECTS = 5
DOI_RESOLVER = "https://doi.org/"
PMID_EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
PDF_REASON = "pdf not supported in v0.1"

_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
_HTML_TYPES = frozenset({"text/html", "application/xhtml+xml"})
_XML_TYPES = frozenset({"application/xml", "text/xml"})
_TEXT_TYPES = frozenset({"application/json"})
_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")
_PMID_RE = re.compile(r"^[0-9]{1,10}$")

# TEST ONLY. Loopback (ip, port) endpoints exempted from the address guard.
# Always empty in normal use; only quotecheck._testing.allow_loopback_endpoints
# adds entries, and only loopback addresses are honoured.
_TEST_ALLOWED_LOOPBACK: set[tuple[str, int]] = set()

# Indirections so tests can observe (and block) network access.
_getaddrinfo = socket.getaddrinfo
_open_socket = socket.create_connection


class SourceError(Exception):
    """The source could not be fetched or read."""

    def __init__(self, reason: str, final_url: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.final_url = final_url


class SourceRefused(SourceError):
    """The request was refused by policy (scheme, address, size, redirects)."""


@dataclass(frozen=True)
class FetchedSource:
    text: str
    final_url: str
    content_type: str


# --------------------------------------------------------------------------
# Source identifiers


def resolve_source(source: str) -> str:
    """Turn ``doi:...`` / ``pmid:...`` / URL into the URL to fetch."""
    if not isinstance(source, str) or not source.strip():
        raise SourceRefused("source is empty")
    s = source.strip()
    low = s.lower()
    if low.startswith("doi:"):
        doi = s[4:].strip()
        if not _DOI_RE.match(doi):
            raise SourceRefused(f"invalid DOI {doi!r}: expected doi:10.<registrant>/<suffix>")
        return DOI_RESOLVER + quote(doi, safe="/:;()._-")
    if low.startswith("pmid:"):
        pmid = s[5:].strip()
        if not _PMID_RE.match(pmid):
            raise SourceRefused(f"invalid PMID {pmid!r}: expected digits")
        return (
            f"{PMID_EFETCH_URL}?db=pubmed&id={pmid}"
            "&rettype=abstract&retmode=text&tool=quotecheck"
        )
    return s


# --------------------------------------------------------------------------
# Address guard


def _is_public_address(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped is not None:
            addr = addr.ipv4_mapped
        elif addr in ipaddress.IPv6Network("64:ff9b::/96"):
            addr = ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)
    if (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    ):
        return False
    return addr.is_global


def _check_address(ip: str, port: int) -> None:
    """Raise SourceRefused unless ``ip`` is a public address."""
    addr = ipaddress.ip_address(ip.split("%", 1)[0])
    if addr.is_loopback and (str(addr), port) in _TEST_ALLOWED_LOOPBACK:
        return
    if not _is_public_address(addr):
        raise SourceRefused(
            f"refused: {ip} is not a public address "
            "(private, loopback, link-local and reserved addresses are blocked)"
        )


def _resolve(host: str, port: int, url: str) -> str:
    """Resolve ``host`` and check every answer; return the address to use."""
    try:
        infos = _getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as exc:
        raise SourceError(f"DNS lookup failed for {host}: {exc}", url) from exc
    addresses = [
        info[4][0] for info in infos if info[0] in (socket.AF_INET, socket.AF_INET6)
    ]
    if not addresses:
        raise SourceError(f"DNS lookup returned no usable address for {host}", url)
    for ip in addresses:
        try:
            _check_address(ip, port)
        except SourceRefused as exc:
            raise SourceRefused(exc.reason, url) from None
    return addresses[0]


# --------------------------------------------------------------------------
# URL validation


@dataclass(frozen=True)
class _Target:
    url: str
    scheme: str
    host: str
    port: int
    request_target: str


def _validate_url(url: str) -> _Target:
    url, _ = urldefrag(url.strip())
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise SourceRefused(f"malformed URL: {exc}", url) from exc
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise SourceRefused(
            f"unsupported scheme {scheme or '(none)'!r}: only http and https are allowed", url
        )
    if parts.username is not None or parts.password is not None:
        raise SourceRefused("credentials in the URL are not allowed", url)
    host = parts.hostname
    if not host:
        raise SourceRefused("URL has no host", url)
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise SourceRefused(f"invalid host name {host!r}", url) from exc
    safe = "/%:@!$&'()*+,;=-._~"
    target = quote(parts.path or "/", safe=safe)
    if parts.query:
        target += "?" + quote(parts.query, safe=safe + "?")
    return _Target(url, scheme, host, port or (443 if scheme == "https" else 80), target)


# --------------------------------------------------------------------------
# Connections pinned to the checked address


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, ip: str, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout)
        self._pinned_ip = ip

    def connect(self) -> None:
        self.sock = _open_socket((self._pinned_ip, self.port), self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, ip: str, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._pinned_ip = ip

    def connect(self) -> None:
        sock = _open_socket((self._pinned_ip, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


def _remaining(deadline: float, timeout: float, url: str) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise SourceError(f"timed out after {timeout:g} s", url)
    return left


def _read_capped(resp: http.client.HTTPResponse, conn: http.client.HTTPConnection,
                 deadline: float, timeout: float, max_bytes: int, url: str) -> bytes:
    declared = resp.getheader("Content-Length")
    if declared and declared.strip().isdigit() and int(declared) > max_bytes:
        raise SourceRefused(f"response too large: declared {int(declared)} bytes, limit {max_bytes}", url)
    chunks: list[bytes] = []
    total = 0
    while True:
        left = _remaining(deadline, timeout, url)
        if conn.sock is not None:
            conn.sock.settimeout(left)
        chunk = resp.read(65536)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise SourceRefused(f"response too large: more than {max_bytes} bytes", url)
        chunks.append(chunk)
    return b"".join(chunks)


def _fetch_bytes(url: str, timeout: float, max_bytes: int) -> tuple[bytes, str, str, str]:
    """GET ``url`` following redirects; return (body, final_url, type, encoding)."""
    deadline = time.monotonic() + timeout
    current = url
    for hop in range(MAX_REDIRECTS + 1):
        target = _validate_url(current)
        current = target.url
        ip = _resolve(target.host, target.port, current)
        left = _remaining(deadline, timeout, current)
        conn_cls = _PinnedHTTPSConnection if target.scheme == "https" else _PinnedHTTPConnection
        conn = conn_cls(target.host, target.port, ip, left)
        try:
            conn.request(
                "GET",
                target.request_target,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5",
                    "Accept-Encoding": "identity",
                },
            )
            resp = conn.getresponse()
            if resp.status in _REDIRECT_CODES:
                location = resp.getheader("Location")
                if not location:
                    raise SourceError(f"HTTP {resp.status} redirect without a Location header", current)
                if hop >= MAX_REDIRECTS:
                    raise SourceRefused(f"too many redirects (limit {MAX_REDIRECTS})", current)
                current = urljoin(current, location.strip())
                continue
            if not 200 <= resp.status < 300:
                raise SourceError(f"HTTP {resp.status} {resp.reason}".strip(), current)
            body = _read_capped(resp, conn, deadline, timeout, max_bytes, current)
            return (
                body,
                current,
                resp.getheader("Content-Type") or "",
                (resp.getheader("Content-Encoding") or "").strip().lower(),
            )
        except SourceError:
            raise
        except (TimeoutError, socket.timeout) as exc:
            raise SourceError(f"timed out after {timeout:g} s", current) from exc
        except (OSError, http.client.HTTPException, ValueError) as exc:
            raise SourceError(f"network error: {exc.__class__.__name__}: {exc}", current) from exc
        finally:
            conn.close()
    raise SourceRefused(f"too many redirects (limit {MAX_REDIRECTS})", current)  # pragma: no cover


# --------------------------------------------------------------------------
# Body to text


def _decompress(body: bytes, encoding: str, max_bytes: int, url: str) -> bytes:
    if encoding in ("", "identity"):
        return body
    if encoding in ("gzip", "x-gzip", "deflate"):
        for wbits in (47, -15):  # gzip/zlib auto-detect, then raw deflate
            try:
                out = zlib.decompressobj(wbits).decompress(body, max_bytes + 1)
            except zlib.error:
                continue
            if len(out) > max_bytes:
                raise SourceRefused(f"response too large after decompression (limit {max_bytes})", url)
            return out
        raise SourceError(f"could not decode {encoding} response", url)
    raise SourceError(f"unsupported content encoding {encoding!r}", url)


def _decode(body: bytes, charset: str | None, is_html: bool) -> str:
    for bom, enc in ((codecs.BOM_UTF8, "utf-8"), (codecs.BOM_UTF16_LE, "utf-16-le"),
                     (codecs.BOM_UTF16_BE, "utf-16-be")):
        if body.startswith(bom):
            return body[len(bom):].decode(enc, "replace")
    if not charset and is_html:
        m = re.search(rb"<meta[^>]+charset\s*=\s*[\"']?([A-Za-z0-9_\-:.]+)", body[:4096], re.I)
        if m:
            charset = m.group(1).decode("ascii", "replace")
    try:
        codecs.lookup(charset or "utf-8")
    except LookupError:
        charset = "utf-8"
    return body.decode(charset or "utf-8", "replace")


def _body_to_text(body: bytes, content_type: str, url: str) -> tuple[str, str]:
    if content_type.strip():
        msg = Message()
        msg["Content-Type"] = content_type
        ctype = msg.get_content_type()
        charset = msg.get_content_charset()
    else:
        ctype, charset = "", None

    if ctype == "application/pdf" or body.lstrip()[:5] == b"%PDF-":
        raise SourceError(PDF_REASON, url)

    if not ctype:
        head = body[:2048].lower()
        ctype = "text/html" if (b"<html" in head or b"<!doctype html" in head or b"<body" in head) else "text/plain"

    if ctype in _HTML_TYPES or ctype in _XML_TYPES or ctype.endswith("+xml"):
        text = html_to_text(_decode(body, charset, True))
    elif ctype.startswith("text/") or ctype in _TEXT_TYPES or ctype.endswith("+json"):
        text = tidy(_decode(body, charset, False))
    else:
        raise SourceError(f"unsupported content type {ctype!r}", url)

    if not text:
        raise SourceError(
            "no text could be extracted (the page may need JavaScript to render)", url
        )
    return text, ctype


def fetch_source(source: str, *, timeout: float = DEFAULT_TIMEOUT,
                 max_bytes: int = MAX_BYTES) -> FetchedSource:
    """Fetch ``source`` (URL, ``doi:...`` or ``pmid:...``) as plain text.

    Raises :class:`SourceError` (or its subclass :class:`SourceRefused`).
    Offsets reported by :func:`quotecheck.check` index into ``.text``.
    """
    url = resolve_source(source)
    body, final_url, content_type, encoding = _fetch_bytes(url, timeout, max_bytes)
    body = _decompress(body, encoding, max_bytes, final_url)
    text, ctype = _body_to_text(body, content_type, final_url)
    return FetchedSource(text=text, final_url=final_url, content_type=ctype)


__all__ = [
    "DEFAULT_TIMEOUT", "FetchedSource", "MAX_BYTES", "MAX_REDIRECTS", "PDF_REASON",
    "SourceError", "SourceRefused", "USER_AGENT", "fetch_source", "resolve_source",
]
