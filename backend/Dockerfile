# Alpha Deck backend — FastAPI on the pinned Python floor (matches CI + requires-python >=3.11).
FROM python:3.11-slim

WORKDIR /app

# tzdata: the daily-cron sidecar (the docker-compose `cron` profile) schedules at an EXPLICIT timezone (US
# market close, America/New_York) — the slim base ships no zoneinfo, so without this an explicit TZ would
# silently fall back to UTC and the job would fire at the wrong hour. The API service ignores it.
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*

# pg_dump for the operator DB-snapshot button (Slice 4). Debian's default postgresql-client LAGS the
# server (bookworm=15) and pg_dump REFUSES a newer server (16), so pin the MATCHING major from PGDG.
# The repo codename is derived from the base image ($VERSION_CODENAME) so a base bump doesn't break it.
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
 && install -d /usr/share/postgresql-common/pgdg \
 && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
      -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
 && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] \
https://apt.postgresql.org/pub/repos/apt $(. /etc/os-release && echo $VERSION_CODENAME)-pgdg main" \
      > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update && apt-get install -y --no-install-recommends postgresql-client-16 \
 && rm -rf /var/lib/apt/lists/*

# Editable install so the source tree at /app is what runs: the migrations (db/migrations/*.sql) and
# the demo seed samples (seed_data/) are read from the filesystem relative to the package, not
# packaged into site-packages. psycopg[binary] bundles its own libpq, so no system deps are needed.
COPY . .
RUN pip install --no-cache-dir -e .

EXPOSE 8000

# Apply migrations (idempotent) -> seed the HIMS demo (idempotent) -> serve. `exec` hands signals
# (docker compose down / Ctrl-C) straight to uvicorn for a clean shutdown.
# `--workers 1` is DELIBERATE, not a default: the in-process draft-job + research registries
# (workbench/draft_jobs, workbench/research_runner) are per-process — >1 worker silently breaks the 409
# in-flight guard and job polls. The app lifespan also refuses env-driven scaling (assert_single_worker);
# do NOT raise this without building the DB-backed job store first.
CMD ["sh", "-c", "python -m db.migrate && python -m pipeline.seed && exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1"]
