FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev curl && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Only dashboard_api.py is exposed publicly. main.py is legacy and lacks
# tenant scoping across its endpoints — see AUDIT_FINDINGS.md — so it is
# not started here. If you need its scheduled jobs, run it as a sidecar
# bound to 127.0.0.1 only, never 0.0.0.0.
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8000/ || exit 1

CMD ["python", "dashboard_api.py"]
