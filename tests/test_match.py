"""Offline matching tests on in-memory text (no network)."""

from __future__ import annotations

import difflib
import random
import time
import unittest

from quotecheck import Status, check_text
from quotecheck.normalize import normalize

PASSAGE = (
    "In a three-year field trial, plots that received a thick layer of straw mulch "
    "lost noticeably less soil moisture during dry spells than bare plots, and the "
    "difference was largest in the hottest weeks of summer."
)
DOC = "Title line\n\nSome preface text that is unrelated.\n" + PASSAGE + "\nA closing remark."


def ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


class ExactTests(unittest.TestCase):
    def test_exact_match_offsets(self):
        quote = "plots that received a thick layer of straw mulch"
        r = check_text(DOC, quote)
        self.assertEqual(r.status, Status.EXACT)
        expected = DOC.index(quote)
        self.assertEqual((r.char_start, r.char_end), (expected, expected + len(quote)))
        self.assertEqual(DOC[r.char_start:r.char_end], quote)
        self.assertEqual(r.similarity, 1.0)

    def test_exact_after_whitespace_quote_and_dash_normalization(self):
        text = "Header\nShe said “it’s  done” — then\n  left the room."
        r = check_text(text, "She said \"it's done\" - then left the room.")
        self.assertEqual(r.status, Status.EXACT)
        self.assertEqual(r.char_start, text.index("She"))
        self.assertEqual(r.char_end, len(text))
        self.assertEqual(normalize(text[r.char_start:r.char_end]),
                         "She said \"it's done\" - then left the room.")

    def test_exact_is_case_sensitive(self):
        quote = "Plots That Received a thick layer of straw mulch"
        r = check_text(DOC, quote)
        self.assertNotEqual(r.status, Status.EXACT)

    def test_offsets_with_expanding_characters(self):
        text = "x ﬁne print … the end"  # "fi" ligature and ellipsis
        r = check_text(text, "fine print ... the")
        self.assertEqual(r.status, Status.EXACT)
        self.assertEqual(text[r.char_start:r.char_end], "ﬁne print … the")

    def test_empty_quote_rejected(self):
        with self.assertRaises(ValueError):
            check_text(DOC, "   \n ")


class FuzzyTests(unittest.TestCase):
    def test_paraphrase_is_not_found(self):
        paraphrase = "Mulching with straw kept the ground wetter during droughts, especially in July."
        r = check_text(DOC, paraphrase)
        self.assertEqual(r.status, Status.NOT_FOUND)
        self.assertLess(r.similarity, 0.90)
        self.assertIsNone(r.char_start)

    def test_one_changed_word_long_quote_is_near(self):
        original = ("plots that received a thick layer of straw mulch lost noticeably less "
                    "soil moisture during dry spells than bare plots")
        changed = original.replace("noticeably", "dramatically")
        r = check_text(DOC, changed)
        self.assertEqual(r.status, Status.NEAR)
        self.assertGreaterEqual(r.similarity, 0.90)
        self.assertEqual(DOC[r.char_start:r.char_end], r.snippet)
        self.assertIn("noticeably", r.snippet)
        # Independent recomputation of the reported similarity.
        self.assertAlmostEqual(r.similarity, ratio(normalize(r.snippet), normalize(changed)), places=3)

    def test_one_changed_word_short_quote_follows_threshold(self):
        text = "Notes.\nThe cat sat on the mat.\nMore notes."
        quote = "The dog sat on the mat."
        expected = ratio("The cat sat on the mat.", quote)
        self.assertLess(expected, 0.90)
        self.assertGreater(expected, 0.85)

        strict = check_text(text, quote)  # default threshold 0.90
        self.assertEqual(strict.status, Status.NOT_FOUND)
        self.assertLess(strict.similarity, 0.90)

        lenient = check_text(text, quote, near_threshold=0.85)
        self.assertEqual(lenient.status, Status.NEAR)
        self.assertEqual(lenient.snippet, "The cat sat on the mat.")
        self.assertAlmostEqual(lenient.similarity, expected, places=3)

    def test_dropped_negation_is_near_not_exact(self):
        # Documented limit: NEAR can hide a meaning-reversing edit.
        text = "The treatment did not reduce mortality in patients over sixty years of age."
        r = check_text(text, "The treatment did reduce mortality in patients over sixty years of age.")
        self.assertEqual(r.status, Status.NEAR)
        self.assertIn("did not reduce", r.snippet)

    def test_threshold_must_be_valid(self):
        with self.assertRaises(ValueError):
            check_text(DOC, "x", near_threshold=0)
        with self.assertRaises(ValueError):
            check_text(DOC, "x", near_threshold=1.5)

    def test_near_in_large_document_is_fast(self):
        rng = random.Random(7)
        vocab = ("alpha beta gamma delta epsilon zeta theta iota kappa lambda mu nu xi "
                 "omicron pi rho sigma tau upsilon phi chi psi omega the of and to in").split()
        filler = " ".join(rng.choice(vocab) for _ in range(150_000))
        text = filler[: len(filler) // 2] + " " + PASSAGE + " " + filler[len(filler) // 2:]
        quote = PASSAGE.replace("largest", "greatest")
        t0 = time.monotonic()
        r = check_text(text, quote)
        elapsed = time.monotonic() - t0
        self.assertEqual(r.status, Status.NEAR)
        self.assertIn("largest", r.snippet)
        self.assertLess(elapsed, 10.0)


if __name__ == "__main__":
    unittest.main()
