"""The post-nightly dashboard cache warm-up (``pipeline/warm_dashboards.py``, ``docs/FEED_LOOP.md`` §the dashboards'
cache warm-up, ``docs/DASHBOARDS.md`` rule 7).

What is pinned here, and where it is real:

- the PLAN: ``--dry-run`` lists every page with its exact query and makes no request, ``--pages`` scopes it;
- the AS-OF rule (``visit_asofs``): the night itself (F1: the app's dial opens on the server's current night);
- the LOOP: a page that fails, times out or blows up the transport does not stop the others and the exit code is 0;
  the budget stops a run cleanly and lists what it never asked; the kill switch skips the whole step;
- the ARTIFACT's shape, written atomically and fail-open;
- the TRANSPORT against a real local HTTP server (a reply, an error status, a timeout, a refused connection);
- the PROOF the warm-up exists for, DB-backed against the REAL FastAPI app and the REAL shared cache: the warm-up's
  request for a page, then the query the frontend's hook sends on that page's default view at the same as-of, is a
  cache ``hit`` (for the current night, today and a past day); and a warm-up of the day AFTER the night (the
  pre-F1 rule) misses the next morning's default read;
- the WIRING: ``warm_dashboards`` is the last thing in the nightly wake, fail-open and always 0 (so it can never
  change the pass's status or exit code), and the compose service carries the env it reads.

``FRONTEND_DEFAULT_QUERY`` below is a hand-copied mirror of what each page's hook sends on its default view (the
hook and the page's initial state under ``frontend/src/dashboards/<slug>/``): it is what the proof compares the
warm-up's requests with, so it is kept apart from ``PAGES`` on purpose. A page whose default query changes updates
BOTH.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

import pytest
from fastapi.testclient import TestClient

from app.deps import get_conn
from app.main import app
from dashboards.cache import CACHE, MAX_ENTRIES
from db.session import DEFAULT_TENANT_ID
from domain import market_time
from domain.market_time import current_night
from domain.settings import get_settings
from domain.thesis import BasketMember, Segment, Thesis
from pipeline import warm_dashboards as wd
from pipeline.warm_dashboards import (
    PAGES,
    FetchError,
    Response,
    build_request,
    http_fetch,
    main,
    plan,
    run,
    visit_asofs,
)
from repositories import thesis_repo

BACKEND = Path(__file__).resolve().parents[2]
SCRIPT = (BACKEND / "scripts" / "daily_cron.sh").read_text(encoding="utf-8")
COMPOSE = (BACKEND.parent / "docker-compose.yml").read_text(encoding="utf-8")
ENV_EXAMPLE = (BACKEND.parent / ".env.example").read_text(encoding="utf-8")

NIGHT = date(2026, 10, 28)  # a Wednesday
ASOF = date(2026, 10, 29)

# What each page's hook sends on its DEFAULT view, beyond `asof` (frontend/src/dashboards/<slug>/use*.ts and the
# page's initial state). The Hot Board's hook opens on `rows=armed` (the armed names with their tape).
FRONTEND_DEFAULT_QUERY: dict[str, dict] = {
    "theme-leaderboard": {},
    "peer-table/scopes": {},
    "insider-tracker": {"days": 30, "silent": False},
    "daily-digest": {},
    "trigger-log": {"days": 90, "role": "entry"},
    "hot-board": {"rows": "armed"},
    "track-record": {"edition": 0},
    "forming-quietly": {},
    "entry-windows": {},
    "leaders": {"group_by": "link", "include_silent": False},
    "rotation-map": {"group_by": "link", "include_silent": False},
    "cross-radar": {"group_by": "link", "include_silent": False, "price_200d": False},
    "landing": {"group_by": "link", "include_silent": False},
    "narrative-index": {"window": "1y", "include_silent": False},
    "read-through": {},
    "rotation-quadrant": {"group_by": "link", "include_silent": False},
    "filing-stream/cards": {},
    "chain-map/cards": {},
    "evidence-ledger/cards": {},
    "fundamentals-multiples/cards": {},
    "narrative-adoption/cards": {},
    "chain-wave/cards": {},
}

# A query parameter PAGES sends that its route does not declare yet (FastAPI ignores it). Each entry is a change
# in flight; the guard below fails once the route declares it, so the entry is removed and the mirror is exact.
PENDING_PARAMS: set[tuple[str, str]] = (
    set()
)  # the Hot Board's `rows` was the last; its route declares it now

SCOPES_REPLY = {
    "default_scope": "business_type",
    "default_key": "miner",
    "companies": 3,
    "cache": "computed",
    "compute_ms": 5,
}


def _ok(extra: dict | None = None) -> Response:
    return Response(
        200, json.dumps({"cache": "computed", "compute_ms": 12, **(extra or {})}).encode()
    )


def _never(url: str, timeout: float) -> Response:  # pragma: no cover — must not run
    raise AssertionError(f"no request was expected, got {url}")


class Recorder:
    """A fetch that answers every page 200/computed (the peer-table scopes with a default scope) and keeps the
    URLs it was asked for, in order; ``special`` overrides one path's answer (a Response or an Exception).
    """

    def __init__(self, special: dict | None = None):
        self.urls: list[str] = []
        self.special = special or {}

    def __call__(self, url: str, timeout: float) -> Response:
        self.urls.append(url)
        path = urlparse(url).path.removeprefix("/dashboards/")
        if path in self.special:
            got = self.special[path]
            if isinstance(got, Exception):
                raise got
            return got
        if path == "peer-table/scopes":
            return Response(200, json.dumps(SCOPES_REPLY).encode())
        return _ok()

    def paths(self) -> list[str]:
        return [urlparse(u).path.removeprefix("/dashboards/") for u in self.urls]


# ---------------------------------------------------------------------------------------------------------
# the as-of rule
# ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "night",
    [
        date(2026, 10, 26),  # Monday night -> Tuesday's visits read Monday
        date(2026, 10, 29),  # Thursday
        date(2026, 10, 30),  # Friday -> the weekend and Monday's daytime read Friday
    ],
)
def test_the_asof_is_the_night_itself(night):
    assert visit_asofs(night) == [night]


@pytest.mark.parametrize(
    "pass_done, visits",
    [
        # Wednesday's pass (22:45 ET), then Thursday's morning and afternoon visits
        (
            datetime(2026, 10, 28, 22, 45),
            [datetime(2026, 10, 29, 9, 0), datetime(2026, 10, 29, 17, 0)],
        ),
        # Friday's pass, then Saturday, Sunday and Monday morning
        (
            datetime(2026, 10, 30, 22, 45),
            [
                datetime(2026, 10, 31, 12, 0),
                datetime(2026, 11, 1, 20, 0),
                datetime(2026, 11, 2, 9, 0),
            ],
        ),
    ],
)
def test_the_warmed_asof_is_the_current_night_every_visit_until_the_next_pass_reads(
    monkeypatch, pass_done, visits
):
    """The cron passes ``--night`` = the last expected night, which is the current night once its pass has run
    (``domain.market_time.current_night``, what ``GET /clock`` serves the dial). Every visit until the next pass
    time asks for that same date, so one key serves them all. Naive instants are market wall times.
    """
    monkeypatch.delenv("ALPHADECK_CRON_AT", raising=False)
    get_settings.cache_clear()
    night = current_night(pass_done)
    assert night == pass_done.date()
    for visit in visits:
        assert visit_asofs(night) == [current_night(visit)], visit
    get_settings.cache_clear()


# ---------------------------------------------------------------------------------------------------------
# the plan
# ---------------------------------------------------------------------------------------------------------


def test_pages_cover_the_heavy_dashboards_and_the_pickers_warm_only_their_picker_read():
    slugs = {wd.slug_of(p) for p, _ in PAGES}
    assert slugs == {
        "theme-leaderboard",
        "peer-table",
        "insider-tracker",
        "daily-digest",
        "trigger-log",
        "hot-board",
        "track-record",
        "forming-quietly",
        "entry-windows",
        "leaders",
        "rotation-map",
        "cross-radar",
        "landing",
        "narrative-index",
        "read-through",
        "rotation-quadrant",
        "filing-stream",
        "chain-map",
        "evidence-ledger",
        "fundamentals-multiples",
        "narrative-adoption",
        "chain-wave",
    }
    pickers = {"filing-stream", "chain-map", "evidence-ledger", "fundamentals-multiples"}
    pickers |= {"narrative-adoption", "chain-wave"}
    for path, params in PAGES:
        if wd.slug_of(path) in pickers:
            assert path == f"{wd.slug_of(path)}/cards" and not params  # never a card's own read
    assert len({p for p, _ in PAGES}) == len(PAGES)  # each request once


def test_the_plan_is_one_request_per_page_per_asof_with_the_pages_exact_default_query():
    reqs = plan([ASOF, date(2026, 11, 2)], PAGES)
    assert len(reqs) == 2 * len(PAGES)
    first = {r.path: dict(parse_qsl(r.query())) for r in reqs if r.asof == ASOF}
    assert [r.path for r in reqs[: len(PAGES)]] == [
        p for p, _ in PAGES
    ]  # the first as-of in full, then the next
    a = ASOF.isoformat()
    assert first["trigger-log"] == {"asof": a, "days": "90", "role": "entry"}
    assert first["insider-tracker"] == {"asof": a, "days": "30", "silent": "false"}
    assert first["track-record"] == {"asof": a, "edition": "0"}
    assert first["hot-board"] == {"asof": a, "rows": "armed"}
    assert first["cross-radar"] == {
        "asof": a,
        "group_by": "link",
        "include_silent": "false",
        "price_200d": "false",
    }
    assert first["theme-leaderboard"] == {"asof": a}
    assert first["narrative-index"] == {"asof": a, "window": "1y", "include_silent": "false"}
    assert build_request(ASOF, "x", {"on": True, "n": 3}).query() == "asof=2026-10-29&on=true&n=3"
    assert (
        build_request(ASOF, "trigger-log", {"days": 90}).url("http://backend:8000/")
        == "http://backend:8000/dashboards/trigger-log?asof=2026-10-29&days=90"
    )


def test_dry_run_lists_every_page_with_its_params_and_makes_no_request(tmp_path):
    lines: list[str] = []
    runs = tmp_path / "warm"
    rc = main(
        ["--dry-run", "--night", "2026-10-28", "--base", "http://api:9", "--runs-dir", str(runs)],
        fetch=_never,
        env={},
        emit=lines.append,
    )
    assert rc == 0
    text = "\n".join(lines)
    assert "http://api:9" in text and "as-of(s) 2026-10-28 " in text  # the night itself
    for path, params in PAGES:
        want = build_request(NIGHT, path, params)
        assert f"GET /dashboards/{want.path}?{want.query()}" in text, want.path
    assert (
        "scope=<default scope>" in text and "scope=all" in text
    )  # the peer table's follow-ups, named
    assert not runs.exists()  # a plan writes no artifact


def test_dry_run_on_a_friday_night_plans_friday_only():
    """F1: the weekend and Monday's daytime read Friday's night (the dial opens on the current night), so one key,
    not the pre-F1 Saturday + Monday pair."""
    lines: list[str] = []
    assert (
        main(["--dry-run", "--night", "2026-10-30"], fetch=_never, env={}, emit=lines.append) == 0
    )
    text = "\n".join(lines)
    assert (
        "asof=2026-10-30" in text
        and "asof=2026-10-31" not in text
        and "asof=2026-11-02" not in text
    )


def test_pages_scopes_the_run_and_an_unknown_page_is_a_usage_error(tmp_path, capsys):
    rec = Recorder()
    rc = main(
        ["--pages", "hot-board, trigger-log", "--asof", "2026-10-29", "--runs-dir", str(tmp_path)],
        fetch=rec,
        env={},
        emit=lambda line: None,
    )
    assert rc == 0 and rec.paths() == ["trigger-log", "hot-board"]  # PAGES order, nothing else
    with pytest.raises(SystemExit) as exc:
        main(["--pages", "no-such-page", "--dry-run"], fetch=_never, env={}, emit=lambda line: None)
    assert exc.value.code == 2
    assert "unknown page" in capsys.readouterr().err


def test_an_explicit_asof_replaces_the_derived_ones_and_the_base_comes_from_the_env(tmp_path):
    rec = Recorder()
    main(
        [
            "--asof",
            "2026-10-29",
            "--asof",
            "2026-10-29",
            "--pages",
            "read-through",
            "--runs-dir",
            str(tmp_path),
        ],
        fetch=rec,
        env={wd.BASE_URL_ENV: "http://elsewhere:1234"},
        emit=lambda line: None,
    )
    assert rec.urls == [
        "http://elsewhere:1234/dashboards/read-through?asof=2026-10-29"
    ]  # once, from the env
    rec2 = Recorder()
    main(
        ["--pages", "read-through", "--runs-dir", str(tmp_path)],
        fetch=rec2,
        env={},
        emit=lambda line: None,
    )
    assert rec2.urls[0].startswith(f"{wd.DEFAULT_BASE_URL}/dashboards/read-through?asof=")


# ---------------------------------------------------------------------------------------------------------
# the loop: fail-open, bounded, switched off
# ---------------------------------------------------------------------------------------------------------


def test_a_failing_page_does_not_stop_the_loop_and_the_exit_code_stays_zero(tmp_path):
    rec = Recorder(
        {
            "trigger-log": FetchError("timeout after 300s"),
            "hot-board": Response(500, b'{"detail": "boom"}'),
            "leaders": RuntimeError("a transport bug"),
            "read-through": Response(200, b"<html>not json</html>"),
            "landing": Response(404, b""),
        }
    )
    lines: list[str] = []
    rc = main(
        ["--asof", "2026-10-29", "--runs-dir", str(tmp_path)], fetch=rec, env={}, emit=lines.append
    )
    assert rc == 0
    # every page was still asked: the whole PAGES list plus the peer table's two follow-ups
    assert rec.paths().count("peer-table") == 2
    assert [p for p in rec.paths() if p != "peer-table"] == [p for p, _ in PAGES]
    text = "\n".join(lines)
    assert "trigger-log (days=90 role=entry) · FAILED (no reply: timeout after 300s)" in text
    assert "hot-board (rows=armed) · FAILED (HTTP 500: boom)" in text
    assert (
        "leaders (group_by=link include_silent=false) · FAILED (no reply: RuntimeError: a transport bug)"
        in text
    )
    assert "landing (group_by=link include_silent=false) · FAILED (HTTP 404" in text
    assert "read-through · 200 · no cache field" in text  # a 200 that is not JSON is not a failure
    (artifact,) = tmp_path.glob("*.json")
    doc = json.loads(artifact.read_text(encoding="utf-8"))
    by_path = {(p["path"], tuple(sorted(p["params"].items()))): p for p in doc["pages"]}
    tl = by_path[("trigger-log", (("days", "90"), ("role", "entry")))]
    assert (tl["status"], tl["cache"], tl["error"]) == (None, None, "timeout after 300s")
    hb = by_path[("hot-board", (("rows", "armed"),))]
    assert (hb["status"], hb["error"]) == (500, "boom")
    assert doc["counts"]["failed"] == 4  # trigger-log, hot-board, leaders, landing
    assert doc["counts"]["requests"] == len(PAGES) + 2 and doc["counts"]["skipped"] == 0


def test_a_failed_scopes_read_skips_the_peer_tables_default_scope_and_goes_on():
    rec = Recorder({"peer-table/scopes": Response(503, b"")})
    lines: list[str] = []
    outcome = run([ASOF], PAGES, fetch=rec, emit=lines.append)
    assert "peer-table" not in rec.paths()
    skipped = [r for r in outcome.results if r.skipped]
    assert [(r.path, r.error) for r in skipped] == [
        ("peer-table", "the scopes read failed, so the default scope is unknown")
    ]
    assert len(rec.urls) == len(PAGES)  # everything else was still asked
    assert any("SKIPPED (the scopes read failed)" in line for line in lines)


def test_the_peer_table_follow_ups_follow_the_scopes_reply():
    def follow(reply: dict) -> list[str]:
        return [r.query() for r in wd._peer_table_follow_ups(ASOF, reply)]

    both = follow({"default_scope": "business_type", "default_key": "miner", "companies": 4})
    assert both == [
        "asof=2026-10-29&scope=business_type&key=miner",
        "asof=2026-10-29&scope=all",
    ]
    # no default leaf, but companies: the page opens on `all`, so that is the one read
    assert follow({"default_scope": None, "default_key": None, "companies": 2}) == [
        "asof=2026-10-29&scope=all"
    ]
    assert (
        follow({"default_scope": None, "default_key": None, "companies": 0}) == []
    )  # nothing to scope to
    # in the run they come right after their scopes read, before the next page
    rec = Recorder()
    run(
        [ASOF],
        [p for p in PAGES if wd.slug_of(p[0]) in {"peer-table", "insider-tracker"}],
        fetch=rec,
    )
    assert rec.paths() == ["peer-table/scopes", "peer-table", "peer-table", "insider-tracker"]
    assert "scope=business_type&key=miner" in rec.urls[1] and rec.urls[2].endswith("scope=all")


def test_the_budget_stops_the_run_cleanly_and_lists_what_it_never_asked(tmp_path):
    ticks = iter(range(0, 10_000, 100))  # the fake clock: 100 s per read
    rec = Recorder()
    lines: list[str] = []
    outcome = run(
        [ASOF], PAGES, budget_s=250, fetch=rec, clock=lambda: next(ticks), emit=lines.append
    )
    assert len(rec.urls) == 2  # t=100 and t=200 are inside the 250 s budget; t=300 is not
    assert rec.paths() == [PAGES[0][0], PAGES[1][0]]
    counts = outcome.counts()
    # the scopes read was the second one asked, so its two follow-ups were queued and never asked either
    assert counts["requests"] == 2 and counts["skipped"] == (len(PAGES) - 2) + 2
    assert all(r.skipped and r.status is None for r in outcome.results[2:])
    assert [
        line for line in lines if "SKIPPED" in line
    ] and "skipped (budget 250s)" in outcome.summary_line()


def test_the_kill_switch_skips_the_whole_step(tmp_path):
    for off in ("0", "false", "OFF", " no "):
        lines: list[str] = []
        rc = main(
            ["--night", "2026-10-28", "--runs-dir", str(tmp_path)],
            fetch=_never,
            env={wd.KILL_SWITCH_ENV: off},
            emit=lines.append,
        )
        assert rc == 0 and len(lines) == 1, off  # one log line, no request
        assert "OFF" in lines[0] and wd.KILL_SWITCH_ENV in lines[0]
    assert not list(tmp_path.glob("*"))  # nothing written
    for on in (None, "", "1", "true"):
        env = {} if on is None else {wd.KILL_SWITCH_ENV: on}
        assert wd.switched_off(env) is False, on
    rec = Recorder()
    main(
        ["--pages", "read-through", "--asof", "2026-10-29", "--runs-dir", str(tmp_path)],
        fetch=rec,
        env={wd.KILL_SWITCH_ENV: "1"},
        emit=lambda line: None,
    )
    assert len(rec.urls) == 1


# ---------------------------------------------------------------------------------------------------------
# the artifact
# ---------------------------------------------------------------------------------------------------------

PAGE_KEYS = {
    "asof",
    "path",
    "params",
    "status",
    "cache",
    "compute_ms",
    "wall_ms",
    "error",
    "skipped",
}
ARTIFACT_KEYS = {
    "kind",
    "status",
    "started_at",
    "finished_at",
    "base_url",
    "night",
    "asofs",
    "timeout_s",
    "budget_s",
    "wall_s",
    "counts",
    "pages",
}


def test_the_artifact_shape(tmp_path):
    rec = Recorder({"hot-board": _ok({"cache": "hit", "compute_ms": 77})})
    rc = main(
        ["--night", "2026-10-30", "--base", "http://api:1", "--runs-dir", str(tmp_path / "w")],
        fetch=rec,
        env={},
        emit=lambda line: None,
    )
    assert rc == 0
    (path,) = (tmp_path / "w").iterdir()
    assert re.fullmatch(
        r"\d{8}T\d{6}Z\.json", path.name
    )  # the started-at instant, like the other run artifacts
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert set(doc) == ARTIFACT_KEYS
    assert doc["kind"] == "warm_dashboards" and doc["status"] == "done"
    assert doc["night"] == "2026-10-30" and doc["asofs"] == ["2026-10-30"]  # the night itself
    assert doc["base_url"] == "http://api:1"
    assert datetime.fromisoformat(doc["started_at"]) <= datetime.fromisoformat(doc["finished_at"])
    assert all(set(p) == PAGE_KEYS for p in doc["pages"])
    assert len(doc["pages"]) == len(PAGES) + 2  # one as-of, with the peer table's two follow-ups
    hot = next(p for p in doc["pages"] if p["path"] == "hot-board")
    assert hot == {
        "asof": "2026-10-30",
        "path": "hot-board",
        "params": {"rows": "armed"},
        "status": 200,
        "cache": "hit",
        "compute_ms": 77,
        "wall_ms": hot["wall_ms"],
        "error": None,
        "skipped": False,
    }
    c = doc["counts"]
    assert c["requests"] == c["ok"] == len(doc["pages"]) and c["failed"] == c["skipped"] == 0
    assert c["hit"] == 1 and c["computed"] == c["ok"] - 1  # the one as-of's hot-board read
    assert not [
        p for p in (tmp_path / "w").iterdir() if p.name.startswith(".")
    ]  # no temp file left behind


def test_a_run_artifact_that_cannot_be_written_never_fails_the_run(tmp_path):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x", encoding="utf-8")
    lines: list[str] = []
    rc = main(
        ["--asof", "2026-10-29", "--pages", "read-through", "--runs-dir", str(blocker / "w")],
        fetch=Recorder(),
        env={},
        emit=lines.append,
    )
    assert rc == 0 and any("done ·" in line for line in lines)
    assert not any("run artifact" in line for line in lines)


# ---------------------------------------------------------------------------------------------------------
# the transport, against a real local server
# ---------------------------------------------------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 — the http.server hook
        if self.path.startswith("/dashboards/slow"):
            time.sleep(1.5)
        status = 500 if self.path.startswith("/dashboards/boom") else 200
        body = json.dumps({"cache": "hit", "compute_ms": 5, "detail": "teapot"}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence
        pass


@pytest.fixture
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.daemon_threads = True
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


def test_the_http_transport_returns_a_reply_an_error_status_and_a_timeout(server):
    ok = http_fetch(f"{server}/dashboards/read-through?asof=2026-10-29", 5)
    assert ok.status == 200 and json.loads(ok.body)["cache"] == "hit"
    bad = http_fetch(f"{server}/dashboards/boom?asof=2026-10-29", 5)
    assert bad.status == 500 and json.loads(bad.body)["detail"] == "teapot"
    with pytest.raises(FetchError, match="timeout"):
        http_fetch(f"{server}/dashboards/slow?asof=2026-10-29", 0.2)


def test_the_http_transport_names_a_refused_connection():
    import socket

    with socket.socket() as s:  # a port nothing listens on
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with pytest.raises(FetchError):
        http_fetch(f"http://127.0.0.1:{port}/dashboards/x", 2)


def test_a_whole_run_over_the_real_transport_logs_the_status_cache_and_times(server, tmp_path):
    lines: list[str] = []
    rc = main(
        [
            "--asof",
            "2026-10-29",
            "--pages",
            "read-through,hot-board",
            "--base",
            server,
            "--runs-dir",
            str(tmp_path),
        ],
        fetch=http_fetch,
        env={},
        emit=lines.append,
    )
    assert rc == 0
    row = next(line for line in lines if "read-through" in line and "200" in line)
    assert re.fullmatch(
        r"warm-dashboards: 2026-10-29 read-through · 200 · hit · compute 5 ms · wall \d+ ms", row
    )


# ---------------------------------------------------------------------------------------------------------
# the plan against the routes (no DB)
# ---------------------------------------------------------------------------------------------------------


def _declared_query_params() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for route in app.routes:
        path = getattr(route, "path", "")
        if path.startswith("/dashboards/") and "GET" in getattr(route, "methods", ()):
            out[path.removeprefix("/dashboards/")] = {f.name for f in route.dependant.query_params}
    return out


def test_every_page_is_a_get_route_and_every_param_it_sends_is_one_the_route_declares():
    """FastAPI ignores a query parameter a route does not declare, so a renamed or dropped parameter would turn the
    warm-up into silent misses. (Only the pending ones in ``PENDING_PARAMS`` are allowed to be undeclared.)
    """
    declared = _declared_query_params()
    for path, params in PAGES:
        assert path in declared, f"{path} is not a GET route under /dashboards"
        assert "asof" in declared[path]
        for name in params:
            if (path, name) in PENDING_PARAMS:
                continue
            assert name in declared[path], f"{path} does not declare ?{name}="
    # the peer table's follow-up reads send these two
    assert {"scope", "key", "asof"} <= declared[wd.PEER_TABLE_PATH]


FRONTEND_DASHBOARDS = BACKEND.parent / "frontend" / "src" / "dashboards"


def _hook_query_keys() -> dict[str, set[str]]:
    """``{path under /dashboards: the query keys its hook sends}`` for every ``api.GET("/dashboards/<path>", ...)``
    whose ``query: { ... }`` is a plain object (a conditional one, the Peer Table's table read, is skipped).
    """
    out: dict[str, set[str]] = {}
    call = re.compile(r'api\.GET\("/dashboards/([^"]+)"')
    for hook in sorted(FRONTEND_DASHBOARDS.glob("*/use*.ts")):
        text = hook.read_text(encoding="utf-8")
        hits = list(call.finditer(text))
        for i, m in enumerate(hits):
            end = hits[i + 1].start() if i + 1 < len(hits) else len(text)
            q = re.search(r"query:\s*\{([^}]*)\}", text[m.end() : end])
            if q:
                out[m.group(1)] = {
                    t.split(":")[0].strip() for t in q.group(1).split(",") if t.strip()
                }
    return out


@pytest.mark.skipif(
    not FRONTEND_DASHBOARDS.is_dir(), reason="the frontend tree is not in this checkout"
)
def test_pages_send_the_query_parameters_the_frontend_hooks_send():
    """The names half of the hand-kept mirror: a hook that gains or drops a parameter changes the key the page
    reads, and ``PAGES`` must change with it (the values live in the pages' initial state, which the proof above
    compares through ``FRONTEND_DEFAULT_QUERY``)."""
    hooks = _hook_query_keys()
    for path, params in PAGES:
        assert path in hooks, f"no hook under frontend/src/dashboards reads /dashboards/{path}"
        sent = {"asof"} | {k for k in params if (path, k) not in PENDING_PARAMS}
        assert (
            hooks[path] == sent
        ), f"{path}: the hook sends {sorted(hooks[path])}, PAGES sends {sorted(sent)}"
        assert set(FRONTEND_DEFAULT_QUERY[path]) == sent - {"asof"}


def test_a_pending_param_is_removed_the_day_its_route_declares_it():
    declared = _declared_query_params()
    for path, name in PENDING_PARAMS:
        assert name not in declared[path], (
            f"{path} now declares ?{name}=: drop it from PENDING_PARAMS and update FRONTEND_DEFAULT_QUERY "
            "to what the hook sends"
        )


# ---------------------------------------------------------------------------------------------------------
# the proof: the warmed read IS the default read's cache hit (real app, real cache, real DB)
# ---------------------------------------------------------------------------------------------------------


def _seed_a_card(db) -> None:
    """One card with two companies of one business type: the Peer Table has a default leaf with two companies and an
    ``all`` scope, and every other page has a roster to read. Backdated so the card counts on a past as-of too.
    """
    sids = []
    with db.cursor() as cur:
        for ticker, cik in (("WARMA", "0000900001"), ("WARMB", "0000900002")):
            sid = uuid.uuid4()
            cur.execute(
                "INSERT INTO security_master (id, tenant_id, ticker, cik, instrument_kind, name, sector, "
                "valid_from) VALUES (%s, %s, %s, %s, 'equity', %s, %s, %s)",
                (
                    sid,
                    DEFAULT_TENANT_ID,
                    ticker,
                    cik,
                    f"{ticker} Inc",
                    "Pharmaceutical Preparations",
                    "2026-01-01",
                ),
            )
            sids.append((ticker, sid))
    thesis = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name="Warm-up fixture card",
        narrative="x",
        segments=[Segment(label="Core")],
        basket=[
            BasketMember(ticker=t, role="the name", security_id=sid, segment="Core")
            for t, sid in sids
        ],
    )
    thesis_repo.upsert(db, thesis)
    with db.cursor() as cur:  # a fixture backdate (the card existed long ago), not an app write
        cur.execute("UPDATE thesis SET created_at = %s WHERE id = %s", ("2026-01-02", thesis.id))
        cur.execute(
            "UPDATE basket_snapshot SET taken_at = %s WHERE thesis_id = %s",
            ("2026-06-02T12:00:00+00:00", thesis.id),
        )
    db.commit()


@pytest.fixture
def api(db, pinned_today):
    """The real app over the test DB with the real shared cache (emptied before and after)."""
    _seed_a_card(db)
    CACHE.clear()
    app.dependency_overrides[get_conn] = lambda: db
    client = TestClient(app)

    def fetch(url: str, timeout: float) -> Response:
        r = client.get(url)
        return Response(r.status_code, r.content)

    try:
        yield client, fetch
    finally:
        app.dependency_overrides.clear()
        CACHE.clear()


def _frontend_default_reads(client: TestClient, asof: date, warmed: list) -> list[tuple[str, dict]]:
    """The reads the frontend makes on opening each warmed page, in the warm-up's order. The Peer Table opens on the
    scope the scopes reply names (``resolveScope``) and its "All live cards" tab reads ``scope=all``.
    """
    reads: list[tuple[str, dict]] = []
    for result in warmed:
        if result.path == wd.PEER_TABLE_PATH:
            scopes = client.get(
                "/dashboards/peer-table/scopes", params={"asof": asof.isoformat()}
            ).json()
            if result.params.get("scope") == "all":
                reads.append((result.path, {"scope": "all"}))
            else:
                assert (scopes["default_scope"], scopes["default_key"]) == (
                    result.params["scope"],
                    result.params["key"],
                )
                reads.append(
                    (result.path, {"scope": scopes["default_scope"], "key": scopes["default_key"]})
                )
        else:
            reads.append((result.path, FRONTEND_DEFAULT_QUERY[result.path]))
    return reads


# The DB proofs pin the market clock to 09:00 ET on Thursday 2026-10-29, so "the current night" is an exact date
# (Wednesday 10-28: the 22:30 pass has not run yet) whatever hour the suite runs at. Pinned through ``market_now``
# (``pinned_today``'s seam, re-pinned after the ``api`` fixture's); transaction time stays real.
_THU_MORNING = datetime(2026, 10, 29, 13, 0, tzinfo=timezone.utc)  # 09:00 EDT
_THE_NIGHT, _TODAY, _A_PAST_DAY = date(2026, 10, 28), date(2026, 10, 29), date(2026, 10, 23)


def _pin_thursday_morning(monkeypatch) -> None:
    monkeypatch.delenv("ALPHADECK_CRON_AT", raising=False)
    get_settings.cache_clear()
    monkeypatch.setattr(
        market_time,
        "market_now",
        lambda tz=None: _THU_MORNING.astimezone(tz or market_time.market_tz()),
    )
    assert current_night() == _THE_NIGHT


@pytest.mark.parametrize(
    "asof", [_THE_NIGHT, _TODAY, _A_PAST_DAY], ids=["the-night", "today", "a-past-day"]
)
def test_the_warmed_read_is_the_cache_hit_of_the_pages_default_read(api, monkeypatch, asof):
    client, fetch = api
    _pin_thursday_morning(monkeypatch)

    outcome = run([asof], PAGES, base_url="http://testserver", fetch=fetch, emit=lambda line: None)

    warmed = [r for r in outcome.results if not r.skipped]
    assert not [r for r in outcome.results if not r.ok()], [
        r.as_dict() for r in outcome.results if not r.ok()
    ]
    # a fresh cache: the warm-up computed every page it asked (none was a hit already)
    assert {r.cache for r in warmed} == {"computed"}
    # the peer table's default scope and `all` were both warmed, from the real scopes reply
    peer = [r.params for r in warmed if r.path == wd.PEER_TABLE_PATH]
    assert peer == [{"scope": "business_type", "key": "biotech_pharma"}, {"scope": "all"}]
    # one as-of's warm set fits the cache with room for a second (a hand run's --asof can add one)
    assert len(CACHE) * 2 <= MAX_ENTRIES

    reads = _frontend_default_reads(client, asof, warmed)
    assert len(reads) == len(warmed) and len(warmed) == len(PAGES) + 2
    for path, query in reads:
        r = client.get(f"/dashboards/{path}", params={"asof": asof.isoformat(), **query})
        assert r.status_code == 200, (path, r.text[:200])
        assert (
            r.json()["cache"] == "hit"
        ), f"{path} {query}: the warm-up did not fill the key the page reads"


def test_the_clock_of_the_warmed_key_is_the_one_the_next_visit_reads(api, monkeypatch):
    """The current night reads on the record clock (F1: live from the current night on), exactly as the morning's
    default visit will, so the key warmed after the pass is the visit's; today and a later day read the same way; a
    day before the night reads on the public clock."""
    client, fetch = api
    _pin_thursday_morning(monkeypatch)
    for asof, clock in (
        (_THE_NIGHT, "record"),
        (_TODAY, "record"),
        (_TODAY + timedelta(days=1), "record"),
        (_A_PAST_DAY, "public"),
    ):
        got = run([asof], [("theme-leaderboard", {})], base_url="http://testserver", fetch=fetch)
        assert got.counts()["ok"] == 1
        body = client.get("/dashboards/theme-leaderboard", params={"asof": asof.isoformat()}).json()
        assert (body["clock"], body["cache"]) == (clock, "hit"), asof


def test_warming_the_day_after_the_night_would_not_have_helped_the_next_morning(api, monkeypatch):
    """The negative control for the as-of rule: the pre-F1 warm-up's key (the day after the night) is a different
    key from the night's, which is what the morning's default visit now reads — it would have paid the cold
    compute."""
    client, fetch = api
    _pin_thursday_morning(monkeypatch)
    old_rule = _THE_NIGHT + timedelta(days=1)
    run([old_rule], [("read-through", {})], base_url="http://testserver", fetch=fetch)
    warmed = client.get("/dashboards/read-through", params={"asof": old_rule.isoformat()}).json()
    visit = client.get(
        "/dashboards/read-through", params={"asof": current_night().isoformat()}
    ).json()
    assert (warmed["cache"], visit["cache"]) == ("hit", "computed")


# ---------------------------------------------------------------------------------------------------------
# the module's boundary and the cron wiring
# ---------------------------------------------------------------------------------------------------------


def test_the_job_is_get_only_it_opens_no_database_and_imports_no_dashboard_or_api_code():
    tree = ast.parse((BACKEND / "pipeline" / "warm_dashboards.py").read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module)
    banned = (
        "psycopg",
        "db",
        "dashboards",
        "app",
        "repositories",
        "calls",
        "signals",
        "llm",
        "notify",
    )
    assert not [m for m in imported if m.split(".")[0] in banned], imported
    assert "domain.market_time" in imported
    source = (BACKEND / "pipeline" / "warm_dashboards.py").read_text(encoding="utf-8")
    assert not re.search(
        r"method\s*=|\"POST\"|\"PUT\"|\"DELETE\"|\"PATCH\"", source
    )  # only the default GET


def _code_lines() -> list[str]:
    return [ln.strip() for ln in SCRIPT.splitlines() if not ln.strip().startswith("#")]


def test_the_warm_up_is_the_last_step_of_the_nightly_wake_after_the_editions_page():
    code = _code_lines()
    calls = [i for i, ln in enumerate(code) if ln == 'warm_dashboards "${_last}"']
    assert len(calls) == 1  # once, in the nightly loop
    (at,) = calls
    boot_end = code.index('if [ "${CRON_EXIT_AFTER_BOOT:-}" = 1 ]; then')
    assert at > code.index("while :; do", boot_end)  # never in the boot block
    assert code[at - 1] == 'editions_nightly "${target}"'  # after the backup and the editions page
    assert code[at + 1 : at + 3] == ["fi", "done"]  # and nothing follows it in the wake
    assert [ln for ln in code if "pipeline.warm_dashboards" in ln] == [
        'timeout -k 30 "${WARM_DASHBOARDS_TIMEOUT_S}" python -m pipeline.warm_dashboards --night "$1"'
    ]
    assert "warm" not in "\n".join(ln for ln in code if ln.startswith("run_pass"))


def _function(name: str) -> str:
    m = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", SCRIPT, flags=re.S | re.M)
    assert m, name
    return m.group(0)


needs_sh = pytest.mark.skipif(
    not (shutil.which("sh") and shutil.which("timeout")), reason="needs sh + timeout"
)


def _run_hook(tmp_path: Path, rc: int, env_extra: dict[str, str]) -> subprocess.CompletedProcess:
    stub = tmp_path / "bin"
    stub.mkdir()
    marker = tmp_path / "ran"
    (stub / "python").write_text(
        f'#!/bin/sh\necho "stub $*"\ntouch "{marker.as_posix()}"\nexit {rc}\n', encoding="utf-8"
    )
    (stub / "python").chmod(0o755)
    body = (
        "WARM_DASHBOARDS_TIMEOUT_S=30\n"
        + _function("warm_dashboards")
        + 'warm_dashboards 2026-07-10\necho "hook-rc=$?"\n'
    )
    script = tmp_path / "hook.sh"
    script.write_text(body, encoding="utf-8")
    env = {
        "PATH": f"{stub}{':' if sys.platform != 'win32' else ';'}" + os.environ["PATH"],
        **env_extra,
    }
    return subprocess.run(
        ["sh", str(script)], capture_output=True, text=True, timeout=60, env={**os.environ, **env}
    )


@needs_sh
@pytest.mark.parametrize(
    "rc, says",
    [
        (0, None),
        (1, "FAILED (exit 1) for night 2026-07-10 (continuing"),
        (124, "TIMED OUT after 30s"),
    ],
)
def test_the_hook_is_fail_open_and_always_returns_zero(tmp_path, rc, says):
    """Whatever the job exits with, the hook returns 0 — so the warm-up can never change the pass's status or exit
    code, and the loop goes on — and names the night it warmed for."""
    r = _run_hook(tmp_path, rc, {})
    assert "stub -m pipeline.warm_dashboards --night 2026-07-10" in r.stdout, r.stdout + r.stderr
    assert "hook-rc=0" in r.stdout
    if says:
        assert says in r.stdout


@needs_sh
def test_the_hook_honors_the_kill_switch_without_starting_python(tmp_path):
    r = _run_hook(tmp_path, 0, {"ALPHADECK_WARM_DASHBOARDS": "0"})
    assert "ALPHADECK_WARM_DASHBOARDS=0" in r.stdout and "OFF" in r.stdout
    assert "hook-rc=0" in r.stdout
    assert not (tmp_path / "ran").exists()  # the CLI was never started


def test_compose_carries_the_env_the_warm_up_reads_and_its_defaults_match():
    services = COMPOSE.split("\n  cron:\n", 1)
    cron_block = services[1].split("\n  frontend:\n", 1)[0]
    code = "\n".join(ln for ln in cron_block.splitlines() if not ln.strip().startswith("#"))
    assert re.search(r"ALPHADECK_WARM_DASHBOARDS: \$\{ALPHADECK_WARM_DASHBOARDS:-1\}", code)
    base = re.search(r"ALPHADECK_WARM_BASE_URL: \$\{ALPHADECK_WARM_BASE_URL:-([^}]+)\}", code)
    assert base and base.group(1) == wd.DEFAULT_BASE_URL == "http://backend:8000"
    t = re.search(
        r"WARM_DASHBOARDS_TIMEOUT_S: \$\{ALPHADECK_WARM_DASHBOARDS_TIMEOUT_S:-(\d+)\}", code
    )
    s = re.search(r'WARM_DASHBOARDS_TIMEOUT_S="\$\{WARM_DASHBOARDS_TIMEOUT_S:-(\d+)\}"', SCRIPT)
    assert t and s and t.group(1) == s.group(1) == "1800"
    # the hard timeout outlasts the CLI's own bound: the budget between requests plus the request in flight
    assert int(t.group(1)) >= wd.DEFAULT_BUDGET_S + wd.DEFAULT_TIMEOUT_S
    for name in (wd.KILL_SWITCH_ENV, wd.BASE_URL_ENV, "ALPHADECK_WARM_DASHBOARDS_TIMEOUT_S"):
        assert name in ENV_EXAMPLE
