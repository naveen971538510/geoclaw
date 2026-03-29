import unittest

from services.rule_engine import RuleEngine


class TestRuleEngine(unittest.TestCase):
    def setUp(self):
        self.engine = RuleEngine()

    ARTICLES = [
        {
            "headline": "Iran fires missiles at oil tankers in Strait of Hormuz",
            "body": "Brent crude surges as conflict escalates",
        },
        {
            "headline": "Fed holds rates steady amid inflation concerns",
            "body": "Markets price in two cuts for next year",
        },
        {
            "headline": "China imposes new tariffs on US semiconductors",
            "body": "Trade war escalates as Beijing retaliates",
        },
        {
            "headline": "Ceasefire agreed between Israel and Hamas",
            "body": "US brokers deal, markets rally on news",
        },
        {
            "headline": "UK economy contracts for second consecutive quarter",
            "body": "Recession confirmed as GDP falls 0.3%",
        },
        {
            "headline": "Gold hits record high as dollar weakens",
            "body": "Safe-haven demand drives precious metals",
        },
        {
            "headline": "OPEC cuts production by 1 million barrels per day",
            "body": "Oil prices surge on supply restriction",
        },
    ]

    def test_all_articles_produce_thesis_key(self):
        for article in self.ARTICLES:
            key = self.engine.derive_thesis_key(article)
            self.assertIsInstance(key, str)
            self.assertGreater(len(key), 10)
            self.assertTrue(key.endswith("."))

    def test_iran_missile_high_delta(self):
        delta, chain = self.engine.reason(self.ARTICLES[0])
        self.assertGreater(delta, 0.10)
        self.assertGreaterEqual(len(chain), 2)

    def test_ceasefire_negative_delta(self):
        delta, _chain = self.engine.reason(self.ARTICLES[3])
        self.assertLess(delta, 0)

    def test_fed_produces_non_zero_delta(self):
        delta, _chain = self.engine.reason(self.ARTICLES[1])
        self.assertNotEqual(delta, 0.0)

    def test_recession_positive_risk_delta(self):
        delta, _chain = self.engine.reason(self.ARTICLES[4])
        self.assertGreater(delta, 0)

    def test_delta_always_clamped(self):
        article = {
            "headline": "war missile strike sanction default crisis ceasefire rally",
            "body": "oil gold fed china iran opec recession",
        }
        delta, _chain = self.engine.reason(article)
        self.assertLessEqual(abs(delta), 0.30)

    def test_chain_always_has_2_hops(self):
        for article in self.ARTICLES:
            _delta, chain = self.engine.reason(article)
            self.assertGreaterEqual(len(chain), 2, article["headline"])

    def test_chain_hops_have_required_fields(self):
        _delta, chain = self.engine.reason(self.ARTICLES[0])
        for hop in chain:
            self.assertIn("hop", hop)
            self.assertIn("from", hop)
            self.assertIn("to", hop)
            self.assertIn("mechanism", hop)
            self.assertIn("confidence", hop)
            self.assertIn("timeframe", hop)

    def test_terminal_risk_levels(self):
        risk_high = self.engine.compute_terminal_risk("war immediate", 0.85, "immediate")
        risk_low = self.engine.compute_terminal_risk("minor news", 0.30, "months")
        self.assertIn("HIGH", risk_high)
        self.assertIn("LOW", risk_low)

    def test_watchlist_suggestion_oil(self):
        suggestion = self.engine.compute_watchlist_suggestion(
            "Oil supply disruption threatens energy markets immediately."
        )
        self.assertIn("Brent", suggestion)

    def test_neutral_article_fallback(self):
        article = {"headline": "Company announces quarterly earnings", "body": ""}
        delta, chain = self.engine.reason(article)
        self.assertIsNotNone(delta)
        self.assertGreaterEqual(len(chain), 2)


if __name__ == "__main__":
    unittest.main()
