from datetime import datetime, timedelta, timezone
from typing import List


RECURRING_EVENTS = [
    {"name": "US Non-Farm Payrolls", "frequency": "monthly", "day": "Friday", "week": 1, "impact": "HIGH", "assets": ["SPY", "DX-Y.NYB", "^TNX"], "description": "Key jobs report"},
    {"name": "US CPI Inflation", "frequency": "monthly", "day": "Wednesday", "week": 2, "impact": "HIGH", "assets": ["^TNX", "DX-Y.NYB", "GC=F"], "description": "Consumer price index"},
    {"name": "Fed FOMC Decision", "frequency": "6-weekly", "day": "Wednesday", "week": 2, "impact": "EXTREME", "assets": ["^TNX", "SPY", "DX-Y.NYB", "GC=F"], "description": "Interest rate decision"},
    {"name": "US GDP (Advance)", "frequency": "quarterly", "day": "Thursday", "week": 4, "impact": "HIGH", "assets": ["SPY", "DX-Y.NYB"], "description": "GDP growth estimate"},
    {"name": "US Jobless Claims", "frequency": "weekly", "day": "Thursday", "week": "every", "impact": "MEDIUM", "assets": ["SPY"], "description": "Weekly unemployment claims"},
    {"name": "ECB Rate Decision", "frequency": "6-weekly", "day": "Thursday", "week": 2, "impact": "HIGH", "assets": ["EURUSD=X", "^TNX"], "description": "ECB interest rate"},
    {"name": "Eurozone CPI", "frequency": "monthly", "day": "Tuesday", "week": 1, "impact": "MEDIUM", "assets": ["EURUSD=X"], "description": "Eurozone inflation"},
    {"name": "Bank of England Decision", "frequency": "6-weekly", "day": "Thursday", "week": 2, "impact": "HIGH", "assets": ["GBPUSD=X"], "description": "BoE rate decision"},
    {"name": "China PMI", "frequency": "monthly", "day": "Monday", "week": 1, "impact": "HIGH", "assets": ["CL=F", "USDCNH=X"], "description": "Manufacturing activity"},
    {"name": "EIA Oil Inventory", "frequency": "weekly", "day": "Wednesday", "week": "every", "impact": "MEDIUM", "assets": ["CL=F", "XLE"], "description": "US crude oil stocks"},
    {"name": "OPEC Meeting", "frequency": "bi-monthly", "day": "Thursday", "week": 1, "impact": "HIGH", "assets": ["CL=F", "XLE"], "description": "OPEC production decisions"},
]

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


class MacroCalendar:
    def __init__(self, db_path=None):
        self.db_path = db_path

    def _candidate_dates(self, event: dict, start: datetime, end: datetime) -> List[datetime]:
        dates = []
        total_days = max(0, (end.date() - start.date()).days)
        for offset in range(total_days + 1):
            candidate = (start + timedelta(days=offset)).replace(hour=13, minute=0, second=0, microsecond=0)
            if DAY_NAMES[candidate.weekday()] != event["day"]:
                continue
            week_num = ((candidate.day - 1) // 7) + 1
            freq = event["frequency"]
            if freq == "weekly":
                dates.append(candidate)
            elif freq == "monthly" and int(event.get("week", 1) or 1) == week_num:
                dates.append(candidate)
            elif freq == "quarterly" and int(event.get("week", 1) or 1) == week_num and candidate.month in {1, 4, 7, 10}:
                dates.append(candidate)
            elif freq == "bi-monthly" and int(event.get("week", 1) or 1) == week_num and candidate.month % 2 == 0:
                dates.append(candidate)
            elif freq == "6-weekly" and int(event.get("week", 1) or 1) == week_num:
                if (candidate.toordinal() // 7) % 6 == 0:
                    dates.append(candidate)
        return dates

    def get_upcoming(self, days_ahead: int = 7) -> List[dict]:
        now = datetime.now(timezone.utc)
        end = now + timedelta(days=int(days_ahead or 7))
        events = []
        for event in RECURRING_EVENTS:
            for candidate in self._candidate_dates(event, now, end):
                if now <= candidate <= end:
                    events.append(
                        {
                            **event,
                            "estimated_date": candidate.strftime("%Y-%m-%d"),
                            "days_away": max(0, (candidate.date() - now.date()).days),
                        }
                    )
        deduped = {}
        for event in events:
            deduped[(event["name"], event["estimated_date"])] = event
        return sorted(deduped.values(), key=lambda item: (item.get("days_away", 999), item.get("name", "")))

    def get_today_events(self) -> List[dict]:
        return [item for item in self.get_upcoming(1) if int(item.get("days_away", 99) or 99) <= 1]

    def get_high_impact_upcoming(self) -> List[dict]:
        return [item for item in self.get_upcoming(14) if str(item.get("impact", "")).upper() in {"HIGH", "EXTREME"}]

    def generate_calendar_brief(self) -> str:
        events = self.get_upcoming(7)
        if not events:
            return "### Macro Calendar\nNo major macro events in the next 7 days."
        lines = ["### Macro Calendar"]
        for event in events[:8]:
            icon = "🔴" if str(event.get("impact", "")).upper() in {"HIGH", "EXTREME"} else "🟡"
            assets = ", ".join((event.get("assets") or [])[:3])
            lines.append(f"{icon} **{event['estimated_date']}** — {event['name']} [{event['impact']}] — Watch: {assets}")
        return "\n".join(lines)
