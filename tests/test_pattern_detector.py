import unittest


class TestPatternDetector(unittest.TestCase):
    def setUp(self):
        from services.pattern_detector import PatternDetector

        self.detector = PatternDetector()
        self.theses = [
            {
                "thesis_key": "Iran missile strike threatens oil supply",
                "confidence": 0.82,
                "confidence_velocity": 0.05,
                "terminal_risk": "HIGH",
                "status": "confirmed",
            },
            {
                "thesis_key": "Fed rate decision moves treasury yields",
                "confidence": 0.65,
                "confidence_velocity": -0.01,
                "terminal_risk": "MEDIUM",
                "status": "active",
            },
            {
                "thesis_key": "China trade war escalates tariffs",
                "confidence": 0.71,
                "confidence_velocity": 0.02,
                "terminal_risk": "MEDIUM",
                "status": "active",
            },
            {
                "thesis_key": "Gold safe haven demand rises on war fears",
                "confidence": 0.58,
                "confidence_velocity": 0.01,
                "terminal_risk": "LOW",
                "status": "active",
            },
        ]

    def test_narrative_clustering_returns_list(self):
        result = self.detector.detect_narrative_cluster(self.theses)
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)

    def test_crisis_regime_high_risk_theses(self):
        theses = [{"confidence": 0.85, "terminal_risk": "HIGH", "thesis_key": "war", "confidence_velocity": 0.1}] * 4
        regime = self.detector.compute_market_regime(theses)
        self.assertIn(regime["regime"], ["CRISIS", "RISK-OFF"])

    def test_neutral_regime_low_conf(self):
        theses = [{"confidence": 0.35, "terminal_risk": "LOW", "thesis_key": "minor", "confidence_velocity": 0.0}] * 3
        regime = self.detector.compute_market_regime(theses)
        self.assertEqual(regime["regime"], "NEUTRAL")

    def test_narratives_sorted_by_confidence(self):
        result = self.detector.detect_narrative_cluster(self.theses)
        confidences = [item["avg_confidence"] for item in result]
        self.assertEqual(confidences, sorted(confidences, reverse=True))

    def test_empty_theses_returns_neutral(self):
        regime = self.detector.compute_market_regime([])
        self.assertEqual(regime["regime"], "NEUTRAL")


if __name__ == "__main__":
    unittest.main()
