"""Unit tests for per-symbol news filtering in dashboard_api.

Only tests the pure helpers (`_news_symbol_meta`, `_news_matches_keywords`) so
they can run without hitting RSS, DuckDuckGo, or the DB.
"""
import os
import sys
import unittest

# Silence optional startup deps when importing dashboard_api under pytest.
os.environ.setdefault("GEOCLAW_DB_BACKEND", "sqlite")

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))

from dashboard_api import (  # noqa: E402  (sys.path injection)
    NEWS_KEYWORDS,
    _news_matches_keywords,
    _news_symbol_meta,
)


class NewsSymbolMetaTests(unittest.TestCase):
    def test_every_dashboard_asset_has_keywords(self):
        expected = {"JP225", "USA500", "TSLA", "NVDA", "META", "AMZN", "INTC", "MU", "GOLD", "SILVER"}
        self.assertTrue(expected.issubset(set(NEWS_KEYWORDS)))
        for symbol in expected:
            entry = NEWS_KEYWORDS[symbol]
            self.assertIn("query", entry, symbol)
            self.assertIn("keywords", entry, symbol)
            self.assertTrue(entry["keywords"], symbol)

    def test_unknown_symbol_falls_back_to_jp225(self):
        meta = _news_symbol_meta("does-not-exist")
        self.assertEqual(meta["symbol"], "JP225")
        self.assertTrue(meta["keywords"])

    def test_none_symbol_defaults_to_jp225(self):
        meta = _news_symbol_meta(None)
        self.assertEqual(meta["symbol"], "JP225")

    def test_lowercase_symbol_normalised(self):
        meta = _news_symbol_meta("tsla")
        self.assertEqual(meta["symbol"], "TSLA")
        self.assertIn("tesla", meta["keywords"])

    def test_tsla_query_is_about_tesla(self):
        meta = _news_symbol_meta("TSLA")
        self.assertIn("Tesla", meta["query"])

    def test_gold_query_is_about_gold(self):
        meta = _news_symbol_meta("GOLD")
        self.assertIn("gold", meta["query"].lower())


class NewsMatchesKeywordsTests(unittest.TestCase):
    TSLA_KW = tuple(NEWS_KEYWORDS["TSLA"]["keywords"])
    GOLD_KW = tuple(NEWS_KEYWORDS["GOLD"]["keywords"])
    JP_KW = tuple(NEWS_KEYWORDS["JP225"]["keywords"])

    def test_headline_containing_symbol_matches(self):
        item = {"headline": "Tesla deliveries beat consensus", "source": "Reuters", "reason": ""}
        self.assertTrue(_news_matches_keywords(item, self.TSLA_KW))

    def test_headline_matching_alias_matches(self):
        item = {"headline": "Musk unveils new factory plan", "source": "CNBC", "reason": ""}
        self.assertTrue(_news_matches_keywords(item, self.TSLA_KW))

    def test_headline_not_matching_is_rejected(self):
        item = {"headline": "Nikkei 225 climbs on BOJ signal", "source": "NHK", "reason": "Tokyo open"}
        self.assertFalse(_news_matches_keywords(item, self.TSLA_KW))

    def test_reason_text_is_also_scanned(self):
        item = {"headline": "Mystery headline", "source": "Wire", "reason": "Analysts flag Tesla outlook shift"}
        self.assertTrue(_news_matches_keywords(item, self.TSLA_KW))

    def test_case_insensitive(self):
        item = {"headline": "GOLD rebounds on safe-haven flows", "source": "Bloomberg", "reason": ""}
        self.assertTrue(_news_matches_keywords(item, self.GOLD_KW))

    def test_jp225_aliases(self):
        for h in ("Yen weakens past 158", "Toyota production update", "BOJ keeps policy steady"):
            self.assertTrue(
                _news_matches_keywords({"headline": h, "source": "x", "reason": ""}, self.JP_KW),
                msg=h,
            )

    def test_empty_keywords_matches_everything(self):
        item = {"headline": "Anything goes", "source": "x", "reason": ""}
        self.assertTrue(_news_matches_keywords(item, tuple()))

    def test_missing_fields_do_not_raise(self):
        self.assertFalse(_news_matches_keywords({}, self.TSLA_KW))


if __name__ == "__main__":
    unittest.main()
