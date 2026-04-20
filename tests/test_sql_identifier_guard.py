"""Regression tests for ``services.db_helpers.safe_identifier`` and
its use in ``services.query_engine``.

The helpers ``QueryEngine._columns``, ``_like_term``, and
``_handle_show_recent_articles`` all inline a table or column name
into an f-string; those call sites must reject anything that isn't a
bare SQL identifier so a future caller who threads user / config
input through them can't escape into arbitrary SQL.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))

from services.db_helpers import safe_identifier  # noqa: E402
from services.query_engine import QueryEngine  # noqa: E402


class SafeIdentifierTests(unittest.TestCase):
    def test_accepts_valid_identifiers(self):
        for name in ("x", "X", "_x", "x1", "agent_theses", "Col_42"):
            self.assertEqual(safe_identifier(name), name)

    def test_rejects_injection_payloads(self):
        payloads = [
            "",
            " ",
            "1abc",
            "foo bar",
            "foo; DROP TABLE students; --",
            "foo) WHERE 1=1; DROP TABLE theses; --",
            "foo'",
            'foo"',
            "foo`",
            "foo.bar",
            "foo-bar",
            "foo\n",
            "foo\t",
            "foo/*comment*/",
            "你好",
        ]
        for payload in payloads:
            with self.assertRaises(ValueError, msg=payload):
                safe_identifier(payload, kind="test")

    def test_rejects_non_string(self):
        for payload in (None, 123, 1.5, b"x", ["x"]):
            with self.assertRaises(ValueError):
                safe_identifier(payload)  # type: ignore[arg-type]

    def test_error_includes_kind_and_value(self):
        try:
            safe_identifier("bad;", kind="table")
        except ValueError as exc:
            self.assertIn("table", str(exc))
            self.assertIn("bad;", str(exc))
        else:
            self.fail("expected ValueError")


class QueryEngineColumnsGuardTests(unittest.TestCase):
    """Exercise ``_columns`` / ``_like_term`` through the real
    sqlite driver on a temp database to prove the identifier guard
    actually short-circuits before SQL execution."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        cls.tmp.close()
        conn = sqlite3.connect(cls.tmp.name)
        conn.executescript(
            """
            CREATE TABLE legit_table (id INTEGER PRIMARY KEY, headline TEXT);
            INSERT INTO legit_table (headline) VALUES ('alpha'), ('beta');
            """
        )
        conn.commit()
        conn.close()
        cls.engine = QueryEngine(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        os.unlink(cls.tmp.name)

    def test_columns_rejects_bad_identifier(self):
        conn = self.engine._db()
        try:
            # Should return [] rather than attempting the injection.
            self.assertEqual(
                self.engine._columns(conn, "legit_table); DROP TABLE legit_table; --"),
                [],
            )
            # Verify the legit_table is still there — if the guard had
            # failed the injection would have dropped the table.
            rows = conn.execute("SELECT COUNT(*) FROM legit_table").fetchone()
            self.assertEqual(rows[0], 2)
        finally:
            conn.close()

    def test_columns_accepts_legit_identifier(self):
        conn = self.engine._db()
        try:
            self.assertEqual(
                set(self.engine._columns(conn, "legit_table")),
                {"id", "headline"},
            )
        finally:
            conn.close()

    def test_like_term_rejects_bad_table(self):
        self.assertEqual(
            self.engine._like_term("legit_table); DROP TABLE legit_table; --", "headline", "alpha"),
            [],
        )
        # Table still present + populated.
        conn = sqlite3.connect(self.tmp.name)
        try:
            rows = conn.execute("SELECT COUNT(*) FROM legit_table").fetchone()
            self.assertEqual(rows[0], 2)
        finally:
            conn.close()

    def test_like_term_rejects_bad_column(self):
        self.assertEqual(
            self.engine._like_term("legit_table", "headline; DROP TABLE", "alpha"),
            [],
        )

    def test_like_term_works_with_legit_args(self):
        rows = self.engine._like_term("legit_table", "headline", "alpha", limit=5)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["headline"], "alpha")


if __name__ == "__main__":
    unittest.main()
