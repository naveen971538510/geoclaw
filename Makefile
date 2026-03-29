.PHONY: install migrate start test smoke prices ingest reason brief log status compile clean

root   = /Users/naveenkumar/GeoClaw
act    = source $(root)/venv/bin/activate &&

install:
	cd $(root) && $(act) pip3 install -r requirements.txt --break-system-packages

migrate:
	cd $(root) && $(act) python3 migration.py

start:
	cd $(root) && $(act) python3 startup.py && uvicorn main:app --port 8000 --reload

once:
	cd $(root) && $(act) python3 -c "from services.agent_loop_service import run_real_agent_loop; import json; r=run_real_agent_loop(max_records_per_source=8); print(json.dumps({k:v for k,v in r.items() if k!='steps'}, indent=2, default=str))"

test:
	cd $(root) && $(act) python3 -m unittest discover -s tests -v

smoke:
	cd $(root) && $(act) python3 tests/smoke_test.py

prices:
	cd $(root) && $(act) python3 -c "from services.price_feed import PriceFeed; pf=PriceFeed(); [print(f'{d[\"symbol\"]}: {d[\"price\"]} ({d[\"change_pct\"]:+.2f}%)') for d in pf.get_snapshot().values() if d.get('price')]"

ingest:
	cd $(root) && $(act) python3 -c "from services.feed_manager import FeedManager; fm=FeedManager(); arts=fm.fetch_all(); saved=fm.save_to_db(arts,'geoclaw.db'); print(f'{len(arts)} fetched, {saved} new')"

reason:
	cd $(root) && $(act) python3 -c "from services.reasoning_pipeline import process_unreasoned_articles; print(process_unreasoned_articles('geoclaw.db',200))"

brief:
	cd $(root) && $(act) python3 -c "from services.briefing_service import generate_briefing; print(generate_briefing('geoclaw.db')[:800])"

log:
	tail -f $(root)/geoclaw.log

status:
	sqlite3 $(root)/geoclaw.db "SELECT thesis_key, ROUND(confidence*100)||'%', status FROM agent_theses WHERE status!='superseded' ORDER BY confidence DESC LIMIT 10"

compile:
	find $(root) -name "*.py" ! -path "*/venv/*" ! -path "*/__pycache__/*" -exec python3 -m py_compile {} \; -print

clean:
	find $(root) -name "*.bak*" -delete && find $(root) -name "__pycache__" -exec rm -rf {} + 2>/dev/null; true

all: migrate compile test
