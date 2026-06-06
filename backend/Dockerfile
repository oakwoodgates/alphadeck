# Alpha Deck backend — FastAPI on the pinned Python floor (matches CI + requires-python >=3.11).
FROM python:3.11-slim

WORKDIR /app

# Editable install so the source tree at /app is what runs: the migrations (db/migrations/*.sql) and
# the demo seed samples (seed_data/) are read from the filesystem relative to the package, not
# packaged into site-packages. psycopg[binary] bundles its own libpq, so no system deps are needed.
COPY . .
RUN pip install --no-cache-dir -e .

EXPOSE 8000

# Apply migrations (idempotent) -> seed the HIMS demo (idempotent) -> serve. `exec` hands signals
# (docker compose down / Ctrl-C) straight to uvicorn for a clean shutdown.
CMD ["sh", "-c", "python -m db.migrate && python -m pipeline.seed && exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000"]
