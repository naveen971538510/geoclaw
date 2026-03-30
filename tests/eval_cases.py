ASK_EVAL_CASES = [
    "What is driving oil right now?",
    "What is the current market regime?",
    "Show top confirmed theses",
    "Any active contradictions?",
    "What is happening in Iran?",
    "What actions are pending review?",
    "How accurate has the agent been?",
    "Why is confidence rising?",
    "What should I watch tomorrow?",
    "Show me the latest news summary",
]


THESIS_GENERATION_EVAL_CASES = [
    {"headline": "Iran warns of retaliation in the Strait of Hormuz", "source": "Reuters", "category": "geopolitics"},
    {"headline": "Gold jumps as investors hedge renewed missile risk", "source": "Bloomberg", "category": "markets"},
    {"headline": "China tariffs raise fresh semiconductor supply concerns", "source": "FT", "category": "tech"},
    {"headline": "Fed officials signal rates may stay higher for longer", "source": "WSJ", "category": "markets"},
    {"headline": "Ceasefire talks stall as border shelling resumes", "source": "BBC", "category": "geopolitics"},
    {"headline": "OPEC hints at tighter supply discipline into summer", "source": "Reuters", "category": "energy"},
    {"headline": "US CPI surprise pushes Treasury yields higher", "source": "Bloomberg", "category": "markets"},
    {"headline": "Taiwan election rhetoric revives Asia security concerns", "source": "FT", "category": "geopolitics"},
    {"headline": "European gas storage draw accelerates after cold snap", "source": "Reuters", "category": "energy"},
    {"headline": "Banking spreads widen as recession fears reprice credit", "source": "CNBC", "category": "markets"},
]


CONTRADICTION_EVAL_CASES = [
    {"prior_claim": "Iran tensions threaten oil shipping and lift crude risk premium.", "current_text": "Iran-backed groups deny any imminent move against shipping lanes."},
    {"prior_claim": "Gold safe-haven demand is accelerating on conflict risk.", "current_text": "Gold retreats as traders unwind defensive hedges after calm weekend headlines."},
    {"prior_claim": "China tariffs pressure semiconductor supply chains.", "current_text": "Chipmakers say inventory buffers can absorb a short tariff shock."},
    {"prior_claim": "Fed hawkishness is pushing yields higher.", "current_text": "A softer inflation print revives expectations for faster rate cuts."},
    {"prior_claim": "Ceasefire is stabilising regional risk appetite.", "current_text": "Border strikes resume, undermining the ceasefire narrative."},
    {"prior_claim": "Oil supply is tightening on OPEC discipline.", "current_text": "Several producers quietly increase exports despite quotas."},
    {"prior_claim": "Banking stress is spreading into credit markets.", "current_text": "Credit spreads narrow as bank funding conditions improve."},
    {"prior_claim": "The dollar is strengthening on sanction escalation.", "current_text": "FX markets fade sanction headlines as settlement workarounds emerge."},
    {"prior_claim": "Recession fears are boosting utilities and defensives.", "current_text": "Cyclical equities rally on stronger PMI data."},
    {"prior_claim": "Safe-haven demand is lifting volatility and gold together.", "current_text": "Volatility drops even as gold continues to grind higher on central-bank demand."},
]


BRIEFING_EVAL_CASES = [
    {"headline": "GeoClaw Intelligence Brief", "sections": [{"title": "Top developing stories", "points": ["Oil risk premium is rising on Strait of Hormuz headlines."]}, {"title": "Conflicting signals", "points": ["No large contradiction set has formed yet."]}, {"title": "Downstream risks", "points": ["Higher crude would tighten inflation expectations."]}], "watch_items": ["CL=F", "^VIX"], "closing": "Watch oil, volatility, and follow-through headlines."},
    {"headline": "GeoClaw Intelligence Brief", "sections": [{"title": "Top developing stories", "points": ["Gold is picking up safe-haven flows on renewed conflict risk."]}, {"title": "Conflicting signals", "points": ["Positioning looks crowded, so reversals can be sharp."]}, {"title": "Recommended actions", "points": ["Review defensive hedges before the next run."]}], "watch_items": ["GC=F", "GLD"], "closing": "The next confirmation should come from volatility and Treasury yields."},
    {"headline": "GeoClaw Intelligence Brief", "sections": [{"title": "Top developing stories", "points": ["China tariff pressure is resurfacing in semiconductors."]}, {"title": "Downstream risks", "points": ["Tech margin pressure can broaden into global growth fears."]}, {"title": "Macro calendar", "points": ["Next CPI release remains a key catalyst."]}], "watch_items": ["QQQ", "SOXX"], "closing": "Keep the tech supply chain and macro prints tied together."},
    {"headline": "GeoClaw Intelligence Brief", "sections": [{"title": "Top developing stories", "points": ["Fed messaging is holding front-end yields higher."]}, {"title": "Recommended actions", "points": ["Watch rate-sensitive equities into the next data release."]}, {"title": "What to watch", "points": ["Treasuries, DXY, and inflation expectations."]}], "watch_items": ["^TNX", "DX-Y.NYB"], "closing": "Rate path sensitivity remains the cleanest transmission mechanism."},
    {"headline": "GeoClaw Intelligence Brief", "sections": [{"title": "Top developing stories", "points": ["Ceasefire stability is fading after fresh clashes."]}, {"title": "Conflicting signals", "points": ["Markets are not fully repricing the renewed conflict risk."]}, {"title": "Downstream risks", "points": ["Energy and shipping remain the first-order channels."]}], "watch_items": ["USO", "^VIX"], "closing": "This story still hinges on whether escalation becomes sustained."},
    {"headline": "GeoClaw Intelligence Brief", "sections": [{"title": "Top developing stories", "points": ["OPEC discipline is supporting crude into the next inventory cycle."]}, {"title": "Macro calendar", "points": ["EIA inventories can confirm or fade this move."]}, {"title": "Recommended actions", "points": ["Keep an eye on XLE and front-month crude."]}], "watch_items": ["CL=F", "XLE"], "closing": "Inventory confirmation is the next truth test."},
    {"headline": "GeoClaw Intelligence Brief", "sections": [{"title": "Top developing stories", "points": ["A stronger CPI print is challenging the soft-landing view."]}, {"title": "Downstream risks", "points": ["Higher yields can pressure growth and duration."]}, {"title": "What to watch", "points": ["Front-end yields and rate-cut pricing."]}], "watch_items": ["^TNX", "SPY"], "closing": "The inflation path remains the central macro hinge."},
    {"headline": "GeoClaw Intelligence Brief", "sections": [{"title": "Top developing stories", "points": ["Asia security rhetoric is lifting geopolitical risk premia."]}, {"title": "Conflicting signals", "points": ["Markets still expect diplomatic stabilisers to hold."]}, {"title": "Recommended actions", "points": ["Watch semis, shipping, and FX spillovers."]}], "watch_items": ["QQQ", "USDCNH=X"], "closing": "The thesis strengthens only if rhetoric turns into policy or force posture."},
    {"headline": "GeoClaw Intelligence Brief", "sections": [{"title": "Top developing stories", "points": ["Gas storage concerns are re-entering the European energy story."]}, {"title": "Macro calendar", "points": ["Weather updates and storage data matter next."]}, {"title": "What to watch", "points": ["European gas proxies and utilities."]}], "watch_items": ["XLE", "EURUSD=X"], "closing": "This remains a weather-and-supply confirmation trade."},
    {"headline": "GeoClaw Intelligence Brief", "sections": [{"title": "Top developing stories", "points": ["Wider banking spreads are reviving recession hedges."]}, {"title": "Downstream risks", "points": ["Credit tightening can broaden into growth risk quickly."]}, {"title": "Recommended actions", "points": ["Review financial exposure against defensive positioning."]}], "watch_items": ["XLF", "XLU"], "closing": "Credit is the key cross-asset transmission channel here."},
]
