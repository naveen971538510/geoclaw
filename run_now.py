import importlib.util, sys

# Patch agent to run immediately instead of waiting
spec = importlib.util.spec_from_file_location("agent", "agent.py")
agent = importlib.util.load_from_spec(spec)
spec.loader.exec_module(agent)

agent.run_morning_briefing_job()
