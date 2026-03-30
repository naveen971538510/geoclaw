import unittest

from services.macro_calendar import MacroCalendar


class TestMacroCalendar(unittest.TestCase):
    def setUp(self):
        self.calendar = MacroCalendar()

    def test_get_upcoming_returns_list(self):
        events = self.calendar.get_upcoming(14)
        self.assertIsInstance(events, list)

    def test_high_impact_filter_works(self):
        events = self.calendar.get_high_impact_upcoming()
        self.assertTrue(all(event["impact"] in {"HIGH", "EXTREME"} for event in events))

    def test_events_have_required_fields(self):
        events = self.calendar.get_upcoming(14)
        if events:
            event = events[0]
            for key in ("name", "impact", "estimated_date", "days_away", "assets"):
                self.assertIn(key, event)

    def test_days_ahead_filter_respected(self):
        events = self.calendar.get_upcoming(3)
        self.assertTrue(all(int(event["days_away"]) <= 3 for event in events))

    def test_calendar_brief_is_string(self):
        brief = self.calendar.generate_calendar_brief()
        self.assertIsInstance(brief, str)
        self.assertIn("Macro Calendar", brief)

    def test_today_events_have_short_horizon(self):
        events = self.calendar.get_today_events()
        self.assertTrue(all(int(event["days_away"]) <= 1 for event in events))


if __name__ == "__main__":
    unittest.main()
