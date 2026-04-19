"""Regression tests for the ``LIKE`` wildcard DoS fix (L2 from the security
findings report).

Untrusted search fragments must be escaped so a caller can't pin a DB
worker with a pathological wildcard (e.g. ``%%%%``) or accidentally match
every row by injecting literal ``%``.

Covers:

  * ``services.db_helpers.escape_like`` — pure-Python escape helper
  * ``db.search_saved_articles`` — user-reachable via the /api/articles
    search routes
  * ``services.query_engine.QueryEngine._like_term`` — user-reachable via
    /api/ask
"""
from __future__ import annotations

import os
import sys
import sqlite3
import tempfile
import unittest

os.environ.setdefault("GEOCLAW_DB_BACKEND", "sqlite")

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))

from services.db_helpers import escape_like  # noqa: E402
from services.query_engine import QueryEngine  # noqa: E402


class EscapeLikeTests(unittest.TestCase):
    """Pure-function tests for the escape helper."""

    def test_passthrough_for_safe_input(self):
        self.assertEqual(escape_like("oil"), "oil")
        self.assertEqual(escape_like("brent crude"), "brent crude")

    def test_escapes_percent(self):
        self.assertEqual(escape_like("100% win"), r"100\% win")

    def test_escapes_underscore(self):
        self.assertEqual(escape_like("a_b"), r"a\_b")

    def test_escapes_backslash_first(self):
        # Backslash must be escaped BEFORE %/_ so the newly-inserted
        # escape character doesn't get double-escaped.
        self.assertEqual(escape_like(r"c:\path"), r"c:\\path")

    def test_combined_metacharacters(self):
        self.assertEqual(escape_like(r"\_50%"), r"\\\_50\%")

    def test_empty_and_none(self):
        self.assertEqual(escape_like(""), "")
        self.assertEqual(escape_like(None), "")  # type: ignore[arg-type]

    def test_idempotent_on_escaped_output_when_paired_with_escape_clause(self):
        """The helper is a one-shot escape. Double-applying it would
        double-escape (\\% -> \\\\\\%), so callers must only apply it once
        before composing the LIKE pattern. Documenting that contract."""
        once = escape_like("50%")
        twice = escape_like(once)
        self.assertNotEqual(once, twice, "caller must not re-escape")


class SearchSavedArticlesLikeTests(unittest.TestCase):
    """End-to-end: build an in-memory-equivalent sqlite, run the real
    ``search_saved_articles`` against it, verify wildcard input doesn't
    match rows that don't contain the literal character."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = self.tmp.name
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "CREATE TABLE articles ("
            "id INTEGER PRIMARY KEY, headline TEXT, source TEXT,"
            " url TEXT UNIQUE, published_at TEXT)"
        )
        conn.executemany(
            "INSERT INTO articles (headline, source, url, published_at) VALUES (?, ?, ?, ?)",
            [
                ("Oil jumps on OPEC cut",          "Reuters",   "http://x/1", "2026-01-01"),
                ("Gold edges up on safe haven",    "Bloomberg", "http://x/2", "2026-01-02"),
                ("100% rally in meme stocks",      "FT",        "http://x/3", "2026-01-03"),
                ("underscore_symbol rebounds",     "NHK",       "http://x/4", "2026-01-04"),
                (r"back\slash test headline",      "Wire",      "http://x/5", "2026-01-05"),
            ],
        )
        conn.commit()
        conn.close()

        # Point db.get_connection at our temp DB.
        import db  # noqa: E402
        self._original = db.get_connection
        db.get_connection = lambda: sqlite3.connect(self.db_path)
        self.db = db

    def tearDown(self):
        self.db.get_connection = self._original
        os.unlink(self.db_path)

    def test_literal_percent_only_matches_100_percent(self):
        rows = self.db.search_saved_articles("%")
        # Before the fix, ``%`` was a wildcard and matched every row.
        # After the fix, it matches only the one headline that literally
        # contains ``%``.
        self.assertEqual(len(rows), 1)
        self.assertIn("100%", rows[0]["headline"])

    def test_literal_underscore_only_matches_one_row(self):
        rows = self.db.search_saved_articles("_")
        # Before the fix, ``_`` was single-char wildcard and matched
        # effectively every row.
        self.assertEqual(len(rows), 1)
        self.assertIn("underscore_symbol", rows[0]["headline"])

    def test_pathological_wildcard_matches_only_literal(self):
        rows = self.db.search_saved_articles("%%%%%%%%")
        # Before the fix, this ran a catastrophic-backtracking pattern;
        # after, it's just literal ``%%%%%%%%`` — no row contains it.
        self.assertEqual(rows, [])

    def test_normal_word_still_works(self):
        rows = self.db.search_saved_articles("Gold")
        self.assertEqual(len(rows), 1)
        self.assertIn("Gold", rows[0]["headline"])

    def test_backslash_round_trips(self):
        rows = self.db.search_saved_articles(r"back\slash")
        self.assertEqual(len(rows), 1)


class QueryEngineLikeTermTests(unittest.TestCase):
    """Exercise ``QueryEngine._like_term`` directly against a temp DB."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = self.tmp.name
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "CREATE TABLE ingested_articles ("
            "id INTEGER PRIMARY KEY, headline TEXT, summary TEXT,"
            " source_name TEXT, published_at TEXT, fetched_at TEXT, url TEXT)"
        )
        conn.executemany(
            "INSERT INTO ingested_articles (headline, summary, source_name, url) VALUES (?, ?, ?, ?)",
            [
                ("Oil hits 90",           "Brent crude broke out", "Reuters",   "http://a/1"),
                ("Gold rally",            "safe-haven flows",      "Bloomberg", "http://a/2"),
                ("100% breakout",         "meme stocks",           "FT",        "http://a/3"),
            ],
        )
        conn.commit()
        conn.close()
        self.engine = QueryEngine(db_path=self.db_path)

    def tearDown(self):
        os.unlink(self.db_path)

    def test_safe_term_matches(self):
        rows = self.engine._like_term("ingested_articles", "headline", "Oil")
        self.assertEqual(len(rows), 1)
        self.assertIn("Oil", rows[0]["headline"])

    def test_percent_does_not_wildcard_match(self):
        rows = self.engine._like_term("ingested_articles", "headline", "%")
        # Only the row with a literal ``%`` in its headline.
        self.assertEqual(len(rows), 1)
        self.assertIn("100%", rows[0]["headline"])

    def test_unknown_table_returns_empty(self):
        # Not strictly LIKE-related but covers the short-circuit.
        self.assertEqual(
            self.engine._like_term("no_such_table", "headline", "oil"),
            [],
        )


if __name__ == "__main__":
    unittest.main()
