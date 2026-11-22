---
name: stack-recovery
description: >-
  Use when a local stack will not load: "prod is down", a 502, a blank app, an `/api` that fails while the
  backend answers, or a `deploy.sh` exit 4 or 5. Reads containers and three probes, then applies the fix.
---

# Stack recovery

Diagnose read-only first. The fixes are a frontend restart (nginx holds no state) or the operator's Docker
Desktop restart; the database is never part of the fix.

## Guardrails

- Never `docker compose down -v`, `DROP DATABASE`, or a rebuild or recreate of postgres.
- Restarting the frontend or the backend container is safe. Restarting the cron kills a pass in flight: check
  `/admin/status` first.
- One symptom, one fix: a 502 is a stale upstream, a 000 a wedged port relay, a daemon error is Docker Desktop.

## Steps

Prod is app 8080 and API 8000; dev is 8081 and 8001; a fork is 808N and 800N.

1. Containers: `docker ps -a --format '{{.Names}}\t{{.Status}}' | grep alphadeck`. Backend and postgres read
   `(healthy)`; frontend and cron read `Up`.
2. The three probes, each `curl -s -o /dev/null -w '%{http_code}' <url>`:
   - the app: `http://localhost:8080/`
   - the backend: `http://localhost:8000/health`
   - the proxy the app uses: `http://localhost:8080/api/health`
3. Match the pattern:

| App | Backend | Proxy | Cause | Fix |
|---|---|---|---|---|
| 200 | 200 | 502 | nginx kept the old backend address after a backend recreate | `docker restart alphadeck-frontend-1` |
| 000 | 200 | any | the host-to-container port relay is wedged, common after a Docker Desktop restart; confirm with `docker exec alphadeck-frontend-1 sh -c 'wget -qO- localhost/ \| head -c 80'` | `docker restart alphadeck-frontend-1` |
| error | error | error | the Docker daemon is down | the operator restarts Docker Desktop (quit from the tray and relaunch; `wsl --shutdown` first if that fails) |
| 200 | 200 | 413 on a large save | the body limit in `frontend/nginx.conf` | raise it there, then deploy `frontend` |
| any | unhealthy or restarting | any | the backend boot failed (database wait, a migration) | read `docker logs --tail 50 alphadeck-backend-1` and stop |

## Checks

- `http://localhost:8080/api/health` answers 200: the proxy, not only the app or `:8000`.
- `docker ps` shows the restarted container `Up`, and backend and postgres `(healthy)`.

## Stop and ask when

- The backend fails its boot: a migration error needs the operator, and possibly a restore (the `backup-restore`
  skill).
- Postgres is not healthy.
- A restart did not change the probes.

## Model

[OPERATIONS], "What a deploy changes" and "Restart and recovery".

[OPERATIONS]: ../../../docs/OPERATIONS.md
