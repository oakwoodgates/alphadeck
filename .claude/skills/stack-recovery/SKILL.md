---
name: stack-recovery
description: >-
  Diagnose + recover a wedged local Alpha Deck stack — "prod is down", "the app
  won't load", a 502 or blank frontend, an /api that fails while the backend is
  fine. A diagnostic ladder (containers → the three endpoint probes → the fix),
  READ-ONLY until the fix; restarting a stateless frontend is safe, the prod DB
  is never touched.
---

# Stack recovery — the app won't load

Read-only diagnosis first; the fixes are container restarts (safe — stateless nginx)
or an operator action (Docker Desktop). NEVER `docker compose down -v`, never
`DROP DATABASE`, never rebuild postgres.

## The diagnostic ladder
1. **Containers:** `docker ps -a --format '{{.Names}}\t{{.Status}}' | grep alphadeck`.
   backend + postgres should read `(healthy)`; frontend + cron just `Up`.
2. **The THREE probes** (prod `:8080`/`:8000`, dev `:8081`/`:8001`):
   - SPA:            `curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/`
   - backend direct: `... http://localhost:8000/health`
   - **the /api PROXY: `... http://localhost:8080/api/health`**  ← the load-bearing one
3. **Read the pattern → the fix:**

| SPA | backend :8000 | /api proxy | meaning | fix |
|---|---|---|---|---|
| 200 | 200 | **502** | frontend nginx cached a STALE backend IP (backend was recreated → new Docker-net IP) | **restart the frontend** — `docker restart alphadeck-frontend-1` (nginx re-resolves) |
| **000** | 200 | — | Docker **port-proxy wedged** (host→container relay dead — common after a Docker Desktop restart); confirm nginx serves INSIDE (`docker exec <fe> sh -c 'wget -qO- localhost/ | head -c 80'` = 200) | **restart the frontend** (re-establishes port publishing) |
| 000/err | 000/err | — | daemon unreachable / "Docker Desktop is unable to start" | **Docker Desktop is down** — operator restarts it (tray quit+relaunch; `wsl --shutdown` then relaunch as fallback). Not a per-container fix. |
| — | — | **413** on a large PUT | nginx `client_max_body_size` (set to 25m; large Workbench triage autosaves) | raise the limit in `frontend/nginx.conf` + rebuild the frontend (a deploy) |

4. **Verify the FIX on the /api PROXY** (`:8080/api/health` → 200), not just the SPA or the backend directly — the proxy is what the app actually uses.

## The deploy gotcha this exists to catch
A backend rebuild (`docker compose up -d --build --no-deps backend`) **recreates the
backend with a new Docker-network IP**. The frontend nginx resolved `backend` at its
own startup and CACHED that IP → every `/api` call 502s until the frontend restarts.
So **after any backend rebuild, restart/recreate the frontend too**, and verify
`:8080/api/health`. (A deploy that rebuilds frontend AND backend is safe — the frontend
recreate re-resolves.) The durable fix is an nginx `resolver` + a variabled `proxy_pass`
so nginx re-resolves per request — a `frontend/nginx.conf` change (its own PR + deploy).

## Guardrails
- Restarting a frontend/backend container is safe (stateless nginx / the DB is separate).
  **Never** `docker compose down -v`, `DROP DATABASE`, or rebuild/​recreate postgres.
- Diagnose before acting; a 502 is a stale-upstream restart, a 000 is a port-proxy
  restart, a daemon error is the operator's Docker Desktop — don't conflate them.
