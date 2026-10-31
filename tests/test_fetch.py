"""Fetching tests against a local fixture server (offline).

The SSRF guard blocks loopback, so the fixture server is reachable only
inside ``allow_loopback_endpoints`` (a test-only switch). A second fixture
server is never exempted, to prove redirects into loopback are refused.

Tests that must not touch the network patch ``quotecheck.fetch._open_socket``
so that, even if the guard regresses, no connection to a private address is
ever attempted.
"""

from __future__ import annotations

import time
import unittest
from contextlib import contextmanager
from unittest import mock

from quotecheck import Status, check, fetch_source
from quotecheck import fetch as fetch_mod
from quotecheck._testing import allow_loopback_endpoints
from quotecheck.fetch import PDF_REASON, USER_AGENT, resolve_source

from .fixture_server import FixtureServer

GUARD_TEXT = "not a public address"


@contextmanager
def record_connections():
    """Replace socket opening: record attempts and refuse them."""
    attempts: list = []

    def fake_open(address, timeout=None, *args, **kwargs):
        attempts.append(address)
        raise ConnectionRefusedError("blocked by test")

    with mock.patch.object(fetch_mod, "_open_socket", fake_open):
        yield attempts


class FetchTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a = FixtureServer().__enter__()  # exempted in tests that need it
        cls.b = FixtureServer().__enter__()  # never exempted

    @classmethod
    def tearDownClass(cls):
        cls.a.__exit__(None, None, None)
        cls.b.__exit__(None, None, None)

    def setUp(self):
        self._allow = allow_loopback_endpoints(("127.0.0.1", self.a.port))
        self._allow.__enter__()

    def tearDown(self):
        self._allow.__exit__(None, None, None)
        self.assertEqual(fetch_mod._TEST_ALLOWED_LOOPBACK, set())


class ContentTests(FetchTestBase):
    def test_constants(self):
        self.assertEqual(fetch_mod.MAX_BYTES, 5 * 1024 * 1024)
        self.assertEqual(fetch_mod.DEFAULT_TIMEOUT, 15.0)
        self.assertEqual(fetch_mod.MAX_REDIRECTS, 5)
        self.assertEqual(USER_AGENT, "quotecheck/0.1 (+https://github.com/feenmile/quotecheck)")

    def test_exact_match_with_offsets(self):
        url = self.a.base + "/article"
        text = fetch_source(url).text
        quote = "Soil temperature matters more than air temperature for early germination."
        r = check(url, quote)
        self.assertEqual(r.status, Status.EXACT)
        expected = text.index(quote)
        self.assertEqual((r.char_start, r.char_end), (expected, expected + len(quote)))
        self.assertEqual(text[r.char_start:r.char_end], quote)
        self.assertEqual(r.source_final_url, url)

    def test_exact_across_inline_tags_entities_and_curly_quotes(self):
        url = self.a.base + "/article"
        r = check(url, "emerged in half the time - a striking result.")
        self.assertEqual(r.status, Status.EXACT)
        r = check(url, '"No single factor explained the variation in yield across all twelve sites."')
        self.assertEqual(r.status, Status.EXACT)
        r = check(url, "Fish & chips are not part of this study.")
        self.assertEqual(r.status, Status.EXACT)

    def test_script_and_style_are_dropped(self):
        url = self.a.base + "/article"
        text = fetch_source(url).text
        self.assertNotIn("inside a script tag", text)
        self.assertNotIn("font-family", text)
        r = check(url, "this sentence lives only inside a script tag")
        self.assertEqual(r.status, Status.NOT_FOUND)

    def test_paraphrase_not_found(self):
        r = check(self.a.base + "/article", "Warm soil makes seeds sprout about twice as fast.")
        self.assertEqual(r.status, Status.NOT_FOUND)

    def test_one_changed_word_near(self):
        r = check(self.a.base + "/article",
                  "No single factor explained the variation in harvest across all twelve sites.")
        self.assertEqual(r.status, Status.NEAR)
        self.assertGreaterEqual(r.similarity, 0.90)
        self.assertIn("yield", r.snippet)

    def test_plain_text_and_gzip(self):
        r = check(self.a.base + "/plain", "The second line says something specific.")
        self.assertEqual(r.status, Status.EXACT)
        r = check(self.a.base + "/gzip", "Fish & chips are not part of this study.")
        self.assertEqual(r.status, Status.EXACT)

    def test_pdf_not_supported(self):
        r = check(self.a.base + "/pdf", "anything")
        self.assertEqual(r.status, Status.SOURCE_UNAVAILABLE)
        self.assertEqual(r.reason, PDF_REASON)
        self.assertEqual(PDF_REASON, "pdf not supported in v0.1")

    def test_js_only_page_and_http_error(self):
        r = check(self.a.base + "/js-only", "anything")
        self.assertEqual(r.status, Status.SOURCE_UNAVAILABLE)
        self.assertIn("JavaScript", r.reason)
        r = check(self.a.base + "/not-found", "anything")
        self.assertEqual(r.status, Status.SOURCE_UNAVAILABLE)
        self.assertIn("404", r.reason)

    def test_user_agent_sent(self):
        before = len(self.a.requests)
        fetch_source(self.a.base + "/plain")
        _, headers = self.a.requests[before]
        self.assertEqual(headers.get("User-Agent"), USER_AGENT)


class LimitTests(FetchTestBase):
    def test_oversize_declared_refused(self):
        r = check(self.a.base + "/big-declared", "a")
        self.assertEqual(r.status, Status.SOURCE_UNAVAILABLE)
        self.assertIn("too large", r.reason)

    def test_oversize_streamed_refused(self):
        r = check(self.a.base + "/big-stream", "a")
        self.assertEqual(r.status, Status.SOURCE_UNAVAILABLE)
        self.assertIn("too large", r.reason)

    def test_timeout(self):
        t0 = time.monotonic()
        r = check(self.a.base + "/slow", "too late", timeout=1.0)
        self.assertEqual(r.status, Status.SOURCE_UNAVAILABLE)
        self.assertIn("timed out", r.reason)
        self.assertLess(time.monotonic() - t0, 2.9)

    def test_five_redirects_allowed_six_refused(self):
        r = check(self.a.base + "/hop/4", "Fish & chips are not part of this study.")  # 5 redirects
        self.assertEqual(r.status, Status.EXACT)
        self.assertEqual(r.source_final_url, self.a.base + "/article")
        r = check(self.a.base + "/hop/5", "Fish & chips are not part of this study.")  # 6 redirects
        self.assertEqual(r.status, Status.SOURCE_UNAVAILABLE)
        self.assertIn("too many redirects", r.reason)


class SsrfTests(FetchTestBase):
    QUOTE = "Fish & chips are not part of this study."

    def test_loopback_refused_without_test_override(self):
        before = len(self.b.requests)
        r = check(self.b.base + "/article", self.QUOTE)
        self.assertEqual(r.status, Status.SOURCE_UNAVAILABLE)
        self.assertIn(GUARD_TEXT, r.reason)
        self.assertEqual(len(self.b.requests), before, "guard must refuse before connecting")

    def test_redirect_to_loopback_refused(self):
        before = len(self.b.requests)
        target = self.b.base + "/article"
        r = check(self.a.base + "/redirect?to=" + target, self.QUOTE)
        self.assertEqual(r.status, Status.SOURCE_UNAVAILABLE)
        self.assertIn(GUARD_TEXT, r.reason)
        self.assertEqual(r.source_final_url, target)
        self.assertEqual(len(self.b.requests), before, "redirect target must not be contacted")

    def test_redirect_to_localhost_name_refused(self):
        r = check(self.a.base + f"/redirect?to=http://localhost:{self.b.port}/article", self.QUOTE)
        self.assertEqual(r.status, Status.SOURCE_UNAVAILABLE)
        self.assertIn(GUARD_TEXT, r.reason)

    def test_redirect_to_private_ip_refused_without_connecting(self):
        real_open = fetch_mod._open_socket
        attempts = []

        def guarded_open(address, timeout=None, *args, **kwargs):
            attempts.append(address)
            if address == ("127.0.0.1", self.a.port):
                return real_open(address, timeout, *args, **kwargs)
            raise ConnectionRefusedError("blocked by test")

        with mock.patch.object(fetch_mod, "_open_socket", guarded_open):
            r = check(self.a.base + "/redirect?to=http://10.0.0.1/secret", self.QUOTE)
        self.assertEqual(r.status, Status.SOURCE_UNAVAILABLE)
        self.assertIn(GUARD_TEXT, r.reason)
        self.assertEqual(attempts, [("127.0.0.1", self.a.port)])

    def test_private_literal_addresses_refused(self):
        urls = [
            "http://10.0.0.1/", "http://172.16.5.4/", "http://192.168.1.1/",
            "http://127.0.0.1/", "http://127.1.2.3:8080/", "http://169.254.169.254/latest/meta-data",
            "http://0.0.0.0/", "http://100.64.0.1/", "http://[::1]/", "http://[fe80::1]/",
            "http://[fc00::1]/", "http://[::ffff:10.0.0.1]/", "http://[64:ff9b::7f00:1]/",
            "https://192.168.0.10/",
        ]
        with record_connections() as attempts:
            for url in urls:
                with self.subTest(url=url):
                    r = check(url, self.QUOTE)
                    self.assertEqual(r.status, Status.SOURCE_UNAVAILABLE)
                    self.assertIn(GUARD_TEXT, r.reason)
        self.assertEqual(attempts, [])

    def test_hostname_resolving_to_private_ip_refused(self):
        answers = {
            "intranet.example": [(2, 1, 6, "", ("10.1.2.3", 80))],
            "mixed.example": [(2, 1, 6, "", ("93.184.216.34", 80)), (2, 1, 6, "", ("192.168.0.7", 80))],
        }

        def fake_getaddrinfo(host, port, *args, **kwargs):
            return answers[host]

        with record_connections() as attempts, \
                mock.patch.object(fetch_mod, "_getaddrinfo", fake_getaddrinfo):
            for host in answers:
                with self.subTest(host=host):
                    r = check(f"http://{host}/page", self.QUOTE)
                    self.assertEqual(r.status, Status.SOURCE_UNAVAILABLE)
                    self.assertIn(GUARD_TEXT, r.reason)
        self.assertEqual(attempts, [])

    def test_public_address_passes_guard(self):
        # Control: the guard is not simply refusing everything. The (patched)
        # socket layer is reached for a public address.
        with record_connections() as attempts, mock.patch.object(
                fetch_mod, "_getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 80))]):
            r = check("http://public.example/page", self.QUOTE)
        self.assertEqual(r.status, Status.SOURCE_UNAVAILABLE)
        self.assertNotIn(GUARD_TEXT, r.reason)
        self.assertEqual(attempts, [("93.184.216.34", 80)])

    def test_override_only_accepts_loopback(self):
        with self.assertRaises(ValueError):
            with allow_loopback_endpoints(("10.0.0.1", 80)):
                pass


class SchemeTests(FetchTestBase):
    def test_non_http_schemes_refused(self):
        sources = ["ftp://example.com/file.txt", "file:///etc/hosts", "gopher://example.com/",
                   "javascript:alert(1)", "data:text/plain,hello", "example.com/no-scheme"]
        with record_connections() as attempts:
            for src in sources:
                with self.subTest(source=src):
                    r = check(src, "hello")
                    self.assertEqual(r.status, Status.SOURCE_UNAVAILABLE)
                    self.assertIn("scheme", r.reason)
        self.assertEqual(attempts, [])

    def test_redirect_to_non_http_scheme_refused(self):
        r = check(self.a.base + "/redirect?to=file:///etc/passwd", "root")
        self.assertEqual(r.status, Status.SOURCE_UNAVAILABLE)
        self.assertIn("scheme", r.reason)

    def test_credentials_in_url_refused(self):
        r = check(f"http://user:pw@127.0.0.1:{self.a.port}/article", "x")
        self.assertEqual(r.status, Status.SOURCE_UNAVAILABLE)
        self.assertIn("credentials", r.reason)


class IdentifierTests(FetchTestBase):
    def test_resolve_source_forms(self):
        self.assertEqual(resolve_source("doi:10.1000/xyz123"), "https://doi.org/10.1000/xyz123")
        self.assertEqual(resolve_source("DOI: 10.1000/a(b)c"), "https://doi.org/10.1000/a(b)c")
        self.assertEqual(
            resolve_source("pmid:12345"),
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
            "?db=pubmed&id=12345&rettype=abstract&retmode=text&tool=quotecheck",
        )
        for bad in ("doi:11.1/x", "doi:10.1000", "pmid:12a", "pmid:", ""):
            with self.subTest(bad=bad):
                r = check(bad, "x")
                self.assertEqual(r.status, Status.SOURCE_UNAVAILABLE)

    def test_pmid_fetches_abstract_text(self):
        with mock.patch.object(fetch_mod, "PMID_EFETCH_URL", self.a.base + "/efetch.fcgi"):
            before = len(self.a.requests)
            r = check("pmid:12345", "mulch reduced evaporation by a measurable amount")
        self.assertEqual(r.status, Status.EXACT)
        path, _ = self.a.requests[before]
        self.assertIn("db=pubmed", path)
        self.assertIn("id=12345", path)
        self.assertIn("retmode=text", path)

    def test_doi_follows_resolver_redirect(self):
        with mock.patch.object(fetch_mod, "DOI_RESOLVER", self.a.base + "/doi/"):
            r = check("doi:10.1234/abc.5", "Fish & chips are not part of this study.")
        self.assertEqual(r.status, Status.EXACT)
        self.assertEqual(r.source_final_url, self.a.base + "/article")


if __name__ == "__main__":
    unittest.main()
