"""A local HTTP fixture server for offline tests."""

from __future__ import annotations

import gzip
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

ARTICLE_HTML = """<!doctype html>
<html><head><title>Field Notes</title>
<style>.lede { color: red; } body { font-family: serif; }</style>
<script>var secret = "this sentence lives only inside a script tag";</script>
</head>
<body>
<h1>Field Notes</h1>
<p>Soil temperature  matters more than   air temperature for early germination.</p>
<p>Researchers observed that seedlings planted after the soil reached
<em>ten degrees</em> emerged in half the time &mdash; a striking result.</p>
<p>The authors wrote: &ldquo;No single factor explained the variation in yield
across all twelve sites.&rdquo;</p>
<p>Fish &amp; chips are not part of this study.</p>
</body></html>
"""

PLAIN_TEXT = "Line one of the plain document.\nThe second line says something specific.\n"

PMID_TEXT = (
    "1. Example Journal. 2020.\n\nA tiny abstract.\n\n"
    "Results suggest that mulch reduced evaporation by a measurable amount.\n"
)

BIG_SIZE = 5 * 1024 * 1024 + 1


class _Handler(BaseHTTPRequestHandler):
    server_version = "fixture/1.0"

    def log_message(self, *args) -> None:  # silence
        pass

    def _send(self, status: int, body: bytes, ctype: str = "text/html; charset=utf-8",
              extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str, status: int = 302) -> None:
        self.send_response(status)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _stream(self, total: int, declare: bool) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        if declare:
            self.send_header("Content-Length", str(total))
        self.end_headers()
        chunk = b"a" * 65536
        sent = 0
        try:
            while sent < total:
                piece = chunk[: min(len(chunk), total - sent)]
                self.wfile.write(piece)
                sent += len(piece)
        except OSError:
            pass  # client hung up, as expected

    def do_GET(self) -> None:  # noqa: C901
        self.server.requests.append((self.path, dict(self.headers)))
        parts = urlsplit(self.path)
        path, query = parts.path, parse_qs(parts.query)
        if path == "/article":
            self._send(200, ARTICLE_HTML.encode("utf-8"))
        elif path == "/plain":
            self._send(200, PLAIN_TEXT.encode("utf-8"), "text/plain; charset=utf-8")
        elif path == "/gzip":
            self._send(200, gzip.compress(ARTICLE_HTML.encode("utf-8")),
                       extra={"Content-Encoding": "gzip"})
        elif path == "/pdf":
            self._send(200, b"%PDF-1.4\n%fake\n", "application/pdf")
        elif path == "/js-only":
            self._send(200, b"<html><body><script>render()</script></body></html>")
        elif path == "/not-found":
            self._send(404, b"<p>missing</p>")
        elif path == "/big-declared":
            self._stream(BIG_SIZE, declare=True)
        elif path == "/big-stream":
            self._stream(BIG_SIZE, declare=False)
        elif path == "/slow":
            time.sleep(3)
            self._send(200, b"<p>too late</p>")
        elif path == "/redirect":
            self._redirect(query["to"][0])
        elif path.startswith("/hop/"):
            n = int(path.rsplit("/", 1)[1])
            if n == 0:
                self._redirect("/article")
            else:
                self._redirect(f"/hop/{n - 1}")
        elif path.startswith("/doi/"):
            self._redirect("/article", status=302)
        elif path == "/efetch.fcgi":
            self._send(200, PMID_TEXT.encode("utf-8"), "text/plain")
        else:
            self._send(404, b"no route")


class _QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address) -> None:
        pass  # clients hanging up early (size cap, timeout) is expected


class FixtureServer:
    """Threaded server on 127.0.0.1 with an OS-assigned port."""

    def __init__(self) -> None:
        self.httpd = _QuietServer(("127.0.0.1", 0), _Handler)
        self.httpd.requests = []
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def requests(self) -> list:
        return self.httpd.requests

    def __enter__(self) -> "FixtureServer":
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
