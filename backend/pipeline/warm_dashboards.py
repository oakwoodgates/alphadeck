"""THE POST-NIGHTLY DASHBOARD CACHE WARM-UP — read each heavy dashboard's DEFAULT view once, so the first visit is a hit.

The dashboards' in-process cache (``dashboards/cache.py``, ``docs/DASHBOARDS.md`` rule 7) is cleared by a new
generation: every cron run moves its token, so the night's pass empties it, and the operator's first visit used to
pay each heavy page's cold compute (measured on a prod copy before this job, per the 2026-10-30 handoff: the Theme
Leaderboard 69 to 96 s, the Peer Table's all-cards scope 69 s, Insider Buying 37 s, the Trigger Log 33 s, the Hot
Board 20 s, the Track Record 17 s, Forming Quietly 12 s, Entry Windows 7 s; this job's own effect is unmeasured
until it runs on a prod copy). This job runs right after the nightly pass (``scripts/daily_cron.sh``,
``warm_dashboards``, after the backup and the editions page) and requests each page's default read once::

    python -m pipeline.warm_dashboards --dry-run                  # the plan: every request, no network
    python -m pipeline.warm_dashboards --night 2026-10-30         # as the cron fires it after that night's pass
    python -m pipeline.warm_dashboards --pages hot-board,trigger-log --asof 2026-10-30   # a hand-run subset

READ-ONLY, DISPLAY ONLY. It issues GETs to the running API (``<base>/dashboards/...``) and nothing else: no
database connection, no write, no fact, no call. The cache it fills lives in the API process, so it must go
through HTTP; it imports nothing from ``dashboards`` or the API (the dashboards leaf scan,
``tests/dashboards/test_isolation.py``).

THE AS-OF IS THE NIGHT ITSELF (F1, 2026-10-30). The frontend's dial opens on the server's CURRENT NIGHT
(``GET /clock`` -> ``domain.market_time.current_night``: the night the nightly pass last recorded), never the
browser's date; the cache keys a read on ``(slug, params, asof)`` (``dashboards/cache.py``). From the pass that
records night N until the next scheduled pass time, the current night is N — the next morning, the weekend after
a Friday, a holiday Monday's daytime — so the one key every visit in that stretch asks for is N, and ``visit_asofs``
returns exactly that: one as-of, the night. (Before F1 the dial read the browser's date and this job warmed
N + 1, plus the Monday after a weekend.) The night is a LIVE read on the record clock (``serve_view``: an as-of on
or after the current night is ``(None, "record")``), so the key warmed tonight is the key the morning's visit
reads. The cron passes ``--night`` = the last expected night, which is the current night once its pass has run;
without ``--night`` the job reads ``current_night()`` itself. Every dashboard computes from the as-of it is given,
never the wall clock (the two that read ``market_today()``, the Catalyst Calendar and the Idea Timeline, are not in
``PAGES``).

THE QUERIES. ``PAGES`` is a hand-maintained mirror of what each page's hook sends on its default view
(``frontend/src/dashboards/<slug>/use*.ts`` and the page's initial state). Only the defaults are warmed: a page's
other views (another window, the Context/Bench toggle) compute on first use as before, and a page with a card picker
(Filing Stream, Chain Map, Evidence Ledger, Fundamentals Multiples, Narrative Adoption, Chain Wave) warms its picker
read only, never every card. When a page's default query changes, change its entry here and the table in
``tests/pipeline/test_warm_dashboards.py`` (the cache-hit proof reads both).

THE PEER TABLE opens on a scope the SCOPES read names (``default_scope`` / ``default_key``; ``resolveScope`` in
``peer-table/tableView.ts``), so the job reads the scopes, then that scope, and the ``all`` scope too (the heavy one).

BOUNDED AND FAIL-OPEN. One request at a time (the API is one process), each under ``--timeout-s`` (300 s), the whole
run under ``--budget-s`` (20 min; a request already in flight finishes first, and a request the budget never
reached is listed ``skipped``). A page that fails is one logged line and the loop goes on; the exit code is 0
whatever a page did. Kill switch: ``ALPHADECK_WARM_DASHBOARDS=0`` skips the whole step. Each real run writes
``data/warm_dashboards/<started>.json`` (per request: status, cache, compute_ms, wall_ms, error).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from domain.market_time import current_night

_log = logging.getLogger("alphadeck.cron")

KIND = "warm_dashboards"
KILL_SWITCH_ENV = "ALPHADECK_WARM_DASHBOARDS"
BASE_URL_ENV = "ALPHADECK_WARM_BASE_URL"
# the compose service name of the API, as the cron sidecar reaches it
DEFAULT_BASE_URL = "http://backend:8000"
DEFAULT_TIMEOUT_S = 300.0
DEFAULT_BUDGET_S = 1200.0

# the repo's gitignored data/ == the container's /data (the compose `appdata` volume), like the other run artifacts
WARM_RUNS_DIR = Path(__file__).resolve().parents[2] / "data" / "warm_dashboards"

Param = str | int | bool
Params = Mapping[str, Param]

# --- the pages -------------------------------------------------------------------------------------------------

# (path under /dashboards, the query a page sends beyond ``asof`` on its default view), heaviest cold read first so
# a budget that runs out costs the lightest pages. The cache is keyed by the as-of, the params the router puts in its
# key and the clock; a param the router serves off ONE shared read (group_by, window, the vs-SPY toggle) is sent as the
# page sends it all the same, so a router that moves it into its key does not silently turn this into a miss.
PAGES: tuple[tuple[str, Params], ...] = (
    ("theme-leaderboard", {}),
    # the scopes read; the default scope and `all` follow it, named by its reply (_peer_table_follow_ups)
    ("peer-table/scopes", {}),
    ("insider-tracker", {"days": 30, "silent": False}),
    # the morning page: one read holds every section (its cold cost is mostly Insider Buying's Form 4 read), no param
    ("daily-digest", {}),
    ("trigger-log", {"days": 90, "role": "entry"}),
    (
        "hot-board",
        {"rows": "armed"},
    ),  # armed rows with their tape; `rows=all` is the toggles' second read
    ("track-record", {"edition": 0}),
    ("forming-quietly", {}),
    ("entry-windows", {}),
    ("leaders", {"group_by": "link", "include_silent": False}),
    ("rotation-map", {"group_by": "link", "include_silent": False}),
    ("cross-radar", {"group_by": "link", "include_silent": False, "price_200d": False}),
    ("landing", {"group_by": "link", "include_silent": False}),
    ("narrative-index", {"window": "1y", "include_silent": False}),
    ("read-through", {}),
    ("rotation-quadrant", {"group_by": "link", "include_silent": False}),
    # the card pickers (the picker read only, never a card)
    ("filing-stream/cards", {}),
    ("chain-map/cards", {}),
    ("evidence-ledger/cards", {}),
    ("fundamentals-multiples/cards", {}),
    ("narrative-adoption/cards", {}),
    ("chain-wave/cards", {}),
)

PEER_SCOPES_PATH = "peer-table/scopes"
PEER_TABLE_PATH = "peer-table"

BUDGET_SKIP = "the run's budget was spent before this request"


def slug_of(path: str) -> str:
    """The dashboard a path belongs to (``peer-table/scopes`` -> ``peer-table``): what ``--pages`` names."""
    return path.split("/", 1)[0]


def known_slugs() -> tuple[str, ...]:
    out: list[str] = []
    for path, _ in PAGES:
        if slug_of(path) not in out:
            out.append(slug_of(path))
    return tuple(out)


# --- the as-of -------------------------------------------------------------------------------------------------


def visit_asofs(night: date) -> list[date]:
    """The as-of(s) the visits after ``night``'s pass send: the night itself, one key. The app's default as-of is
    the current night (``GET /clock``), which stays ``night`` until the next scheduled pass time — the next
    morning, and the whole weekend after a Friday. Pure over its argument; see the module docstring.
    """
    return [night]


# --- the requests ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Request:
    """One GET: the path under ``/dashboards`` and its whole query (``asof`` first)."""

    asof: date
    path: str
    params: tuple[tuple[str, str], ...]

    def query(self) -> str:
        return urllib.parse.urlencode(self.params)

    def url(self, base_url: str) -> str:
        return f"{base_url.rstrip('/')}/dashboards/{self.path}?{self.query()}"

    def label(self) -> str:
        """``path (k=v k=v)`` for the log and the artifact (the as-of is its own column)."""
        extra = " ".join(f"{k}={v}" for k, v in self.params if k != "asof")
        return f"{self.path} ({extra})" if extra else self.path


def _wire(value: Param) -> str:
    # the booleans as the frontend's client sends them (and FastAPI parses them)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def build_request(asof: date, path: str, params: Params) -> Request:
    """The request for one page at one as-of: the ``asof`` first, then the page's default params in order."""
    return Request(
        asof=asof,
        path=path,
        params=(("asof", asof.isoformat()), *((k, _wire(v)) for k, v in params.items())),
    )


def select_pages(only: Sequence[str] | None) -> tuple[tuple[str, Params], ...]:
    """``PAGES`` narrowed to the dashboards named (by slug); ``None`` = all. An unknown slug is an error."""
    if not only:
        return PAGES
    unknown = sorted(set(only) - set(known_slugs()))
    if unknown:
        raise ValueError(f"unknown page(s) {', '.join(unknown)}; known: {', '.join(known_slugs())}")
    return tuple((p, q) for p, q in PAGES if slug_of(p) in only)


def plan(asofs: Sequence[date], pages: Sequence[tuple[str, Params]]) -> list[Request]:
    """Every request a run starts with, in order: each as-of in turn (the first visit's date first, so a budget
    that runs out costs the later date), each page in ``PAGES`` order. The Peer Table's default scope and its
    ``all`` scope are NOT here: they are read off the scopes reply at run time (``_peer_table_follow_ups``).
    """
    return [build_request(a, path, params) for a in asofs for path, params in pages]


def _peer_table_follow_ups(asof: date, scopes: Mapping[str, Any]) -> list[Request]:
    """The Peer Table reads that follow its scopes read: the scope the page opens on (``resolveScope``: the
    scopes' own default leaf, else ``all`` when any company is counted, else nothing to read) and the ``all``
    scope, the heaviest one (deduplicated when the default is ``all``)."""
    out: list[Request] = []
    scope, key = scopes.get("default_scope"), scopes.get("default_key")
    if scope and key:
        out.append(build_request(asof, PEER_TABLE_PATH, {"scope": str(scope), "key": str(key)}))
    if (scopes.get("companies") or 0) > 0:
        out.append(build_request(asof, PEER_TABLE_PATH, {"scope": "all"}))
    return out


# --- the transport ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes


Fetch = Callable[[str, float], Response]


class FetchError(Exception):
    """A request that got no HTTP reply (a timeout, a refused connection, a reset)."""


def http_fetch(url: str, timeout_s: float) -> Response:
    """One GET with the stdlib. An HTTP error status is a ``Response`` (the status and its body); a request that
    got no reply raises ``FetchError`` with a short reason."""
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(
            req, timeout=timeout_s
        ) as resp:  # noqa: S310 — http(s) base from config
            return Response(resp.status, resp.read())
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read()
        except Exception:  # noqa: BLE001 — the status is what matters
            body = b""
        return Response(exc.code, body)
    except TimeoutError as exc:
        raise FetchError(f"timeout after {timeout_s:g}s") from exc
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, TimeoutError):
            raise FetchError(f"timeout after {timeout_s:g}s") from exc
        raise FetchError(f"{type(reason).__name__}: {reason}") from exc
    except OSError as exc:  # a reset mid-read, a socket timeout raised as OSError
        raise FetchError(f"{type(exc).__name__}: {exc}") from exc


# --- one run ---------------------------------------------------------------------------------------------------


@dataclass
class PageResult:
    asof: str
    path: str
    params: dict[str, str]
    status: int | None  # the HTTP status; None = no reply (or never asked)
    cache: str | None  # the reply's "hit" / "computed"
    compute_ms: int | None  # the reply's own compute time (a hit carries the stored figure)
    wall_ms: int
    error: str | None
    skipped: bool = False  # never asked: the budget ran out, or the read it depends on failed

    def ok(self) -> bool:
        return self.status is not None and 200 <= self.status < 300

    def as_dict(self) -> dict[str, Any]:
        return {
            "asof": self.asof,
            "path": self.path,
            "params": self.params,
            "status": self.status,
            "cache": self.cache,
            "compute_ms": self.compute_ms,
            "wall_ms": self.wall_ms,
            "error": self.error,
            "skipped": self.skipped,
        }


def _decode(body: bytes) -> dict[str, Any] | None:
    try:
        doc = json.loads(body)
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


def _result_line(r: PageResult) -> str:
    extra = " ".join(f"{k}={v}" for k, v in r.params.items())
    name = f"{r.path} ({extra})" if extra else r.path
    if r.ok():
        return (
            f"warm-dashboards: {r.asof} {name} · {r.status} · {r.cache or 'no cache field'} · "
            f"compute {r.compute_ms if r.compute_ms is not None else '-'} ms · wall {r.wall_ms} ms"
        )
    why = r.error or "failed"
    shown = f"HTTP {r.status}" if r.status is not None else "no reply"
    return f"warm-dashboards: {r.asof} {name} · FAILED ({shown}: {why}) · wall {r.wall_ms} ms"


def _params_of(req: Request) -> dict[str, str]:
    """The request's query without ``asof`` (which is its own column in the log and the artifact)."""
    return {k: v for k, v in req.params if k != "asof"}


def _skipped(req: Request, why: str) -> PageResult:
    return PageResult(
        asof=req.asof.isoformat(),
        path=req.path,
        params=_params_of(req),
        status=None,
        cache=None,
        compute_ms=None,
        wall_ms=0,
        error=why,
        skipped=True,
    )


def _request_one(
    req: Request, *, base_url: str, timeout_s: float, fetch: Fetch
) -> tuple[PageResult, dict[str, Any] | None]:
    """One request, never raising: the result, and the decoded reply body when there was one."""
    started = time.perf_counter()
    status: int | None = None
    body: dict[str, Any] | None = None
    error: str | None = None
    try:
        resp = fetch(req.url(base_url), timeout_s)
        status = resp.status
        body = _decode(resp.body)
        if not (200 <= resp.status < 300):
            detail = body.get("detail") if body else None
            error = str(detail)[:200] if detail else None
    except FetchError as exc:
        error = str(exc)
    except (
        Exception
    ) as exc:  # noqa: BLE001 — a bug in a transport must cost one page, never the loop
        error = f"{type(exc).__name__}: {exc}"
    wall_ms = round((time.perf_counter() - started) * 1000)
    cache = body.get("cache") if body else None
    compute_ms = body.get("compute_ms") if body else None
    result = PageResult(
        asof=req.asof.isoformat(),
        path=req.path,
        params=_params_of(req),
        status=status,
        cache=cache if isinstance(cache, str) else None,
        compute_ms=compute_ms if isinstance(compute_ms, int) else None,
        wall_ms=wall_ms,
        error=error,
    )
    return result, body


@dataclass
class WarmRun:
    started_at: datetime
    finished_at: datetime
    base_url: str
    night: date | None
    asofs: list[date]
    timeout_s: float
    budget_s: float
    results: list[PageResult]
    wall_s: float

    def counts(self) -> dict[str, int]:
        asked = [r for r in self.results if not r.skipped]
        ok = [r for r in asked if r.ok()]
        return {
            "requests": len(asked),
            "ok": len(ok),
            "failed": len(asked) - len(ok),
            "skipped": len(self.results) - len(asked),
            "computed": sum(1 for r in ok if r.cache == "computed"),
            "hit": sum(1 for r in ok if r.cache == "hit"),
        }

    def payload(self) -> dict[str, Any]:
        return {
            "kind": KIND,
            "status": "done",
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "base_url": self.base_url,
            "night": self.night.isoformat() if self.night else None,
            "asofs": [a.isoformat() for a in self.asofs],
            "timeout_s": self.timeout_s,
            "budget_s": self.budget_s,
            "wall_s": round(self.wall_s, 1),
            "counts": self.counts(),
            "pages": [r.as_dict() for r in self.results],
        }

    def summary_line(self) -> str:
        c = self.counts()
        return (
            f"warm-dashboards: done · {len(self.asofs)} as-of(s) ({', '.join(a.isoformat() for a in self.asofs)}) · "
            f"{c['requests']} requests · {c['ok']} ok ({c['computed']} computed, {c['hit']} hit) · "
            f"{c['failed']} failed · {c['skipped']} skipped (budget {self.budget_s:g}s) · {self.wall_s:.1f} s"
        )


def run(
    asofs: Sequence[date],
    pages: Sequence[tuple[str, Params]],
    *,
    base_url: str = DEFAULT_BASE_URL,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    budget_s: float = DEFAULT_BUDGET_S,
    fetch: Fetch = http_fetch,
    night: date | None = None,
    clock: Callable[[], float] = time.monotonic,
    emit: Callable[[str], None] = lambda line: print(line, flush=True),
) -> WarmRun:
    """Issue ``plan(asofs, pages)`` one request at a time and return what happened. Never raises for a page: a
    non-2xx status, a timeout and a transport bug are each one result and one logged line, and the loop goes on.
    The budget is checked BEFORE each request (a request in flight finishes); the requests it never reached are
    listed as skipped. The Peer Table's follow-up reads run right after its scopes read."""
    started_at = datetime.now(timezone.utc)
    t0 = clock()
    results: list[PageResult] = []
    queue: list[Request] = plan(asofs, pages)
    i = 0
    while i < len(queue):
        req = queue[i]
        i += 1
        if clock() - t0 >= budget_s:
            for rest in queue[i - 1 :]:
                results.append(_skipped(rest, BUDGET_SKIP))
                emit(
                    f"warm-dashboards: {rest.asof.isoformat()} {rest.label()} · SKIPPED ({BUDGET_SKIP})"
                )
            break
        result, body = _request_one(req, base_url=base_url, timeout_s=timeout_s, fetch=fetch)
        results.append(result)
        emit(_result_line(result))
        if req.path == PEER_SCOPES_PATH:
            if result.ok() and body is not None:
                queue[i:i] = _peer_table_follow_ups(req.asof, body)
            else:
                # the default scope is named by the scopes reply, so without it there is nothing to ask for
                unknown = build_request(req.asof, PEER_TABLE_PATH, {"scope": "<default scope>"})
                results.append(
                    _skipped(unknown, "the scopes read failed, so the default scope is unknown")
                )
                emit(
                    f"warm-dashboards: {req.asof.isoformat()} {unknown.label()} · SKIPPED (the scopes read failed)"
                )
    return WarmRun(
        started_at=started_at,
        finished_at=datetime.now(timezone.utc),
        base_url=base_url,
        night=night,
        asofs=list(asofs),
        timeout_s=timeout_s,
        budget_s=budget_s,
        results=results,
        wall_s=clock() - t0,
    )


# --- the artifact ----------------------------------------------------------------------------------------------


def write_warm_run(payload: dict[str, Any], *, base_dir: Path | None = None) -> Path | None:
    """Write one run artifact atomically (``tmp`` + ``os.replace``), named by the started-at instant. Fail-open:
    ``None`` (logged) when the write fails — the record is best-effort, the warm-up it describes is not a fact.
    """
    run_dir = base_dir or WARM_RUNS_DIR
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        started = datetime.fromisoformat(payload["started_at"])
        path = run_dir / f"{started.strftime('%Y%m%dT%H%M%SZ')}.json"
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
        os.replace(tmp, path)
        return path
    except Exception:  # noqa: BLE001
        _log.exception("warm-dashboards run artifact write failed (fail-open)")
        return None


# --- the CLI ---------------------------------------------------------------------------------------------------


def switched_off(env: Mapping[str, str] | None = None) -> bool:
    """``ALPHADECK_WARM_DASHBOARDS`` set to 0 / false / off / no: the whole step is skipped. Unset or empty = on."""
    value = (env if env is not None else os.environ).get(KILL_SWITCH_ENV, "")
    return value.strip().lower() in {"0", "false", "off", "no"}


def _parse_date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{text!r} is not a YYYY-MM-DD date") from exc


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m pipeline.warm_dashboards",
        description="Warm the dashboards' cache: request each heavy page's default read once (GETs only).",
    )
    p.add_argument(
        "--night",
        type=_parse_date,
        default=None,
        help="the night whose pass just finished (default: the current night in market time); the as-of warmed "
        "is that night, the date the app's dial opens on until the next pass",
    )
    p.add_argument(
        "--asof",
        type=_parse_date,
        action="append",
        default=None,
        help="warm exactly this as-of instead of the derived ones (repeatable)",
    )
    p.add_argument(
        "--pages", default=None, help="comma-separated dashboard slugs to warm (default: all)"
    )
    p.add_argument(
        "--base",
        default=None,
        help=f"the API's base URL (default ${BASE_URL_ENV}, else {DEFAULT_BASE_URL})",
    )
    p.add_argument("--timeout-s", type=float, default=DEFAULT_TIMEOUT_S, help="per-request timeout")
    p.add_argument(
        "--budget-s", type=float, default=DEFAULT_BUDGET_S, help="total budget for the run"
    )
    p.add_argument("--dry-run", action="store_true", help="print the plan; make no request")
    p.add_argument(
        "--runs-dir",
        type=Path,
        default=None,
        help="where the run artifact goes (default data/warm_dashboards)",
    )
    return p


def main(
    argv: Sequence[str] | None = None,
    *,
    fetch: Fetch = http_fetch,
    env: Mapping[str, str] | None = None,
    emit: Callable[[str], None] = lambda line: print(line, flush=True),
) -> int:
    """The CLI. Exit 0 whatever a page did (usage errors exit 2, argparse's own)."""
    args = _build_parser().parse_args(argv)
    environ = env if env is not None else os.environ
    if switched_off(environ):
        emit(
            f"warm-dashboards: {KILL_SWITCH_ENV}={environ.get(KILL_SWITCH_ENV, '').strip()} "
            "— the dashboard cache warm-up is OFF"
        )
        return 0
    try:
        pages = select_pages(
            [s.strip() for s in (args.pages or "").split(",") if s.strip()] or None
        )
    except ValueError as exc:
        _build_parser().error(str(exc))
    night = args.night or current_night()
    asofs = list(dict.fromkeys(args.asof)) if args.asof else visit_asofs(night)
    base_url = args.base or environ.get(BASE_URL_ENV) or DEFAULT_BASE_URL
    if args.dry_run:
        emit(
            f"warm-dashboards: PLAN (dry run, no request) · base {base_url} · night {night.isoformat()} · "
            f"as-of(s) {', '.join(a.isoformat() for a in asofs)} · timeout {args.timeout_s:g}s · budget {args.budget_s:g}s"
        )
        for req in plan(asofs, pages):
            emit(f"warm-dashboards:   GET /dashboards/{req.path}?{req.query()}")
            if req.path == PEER_SCOPES_PATH:
                emit(
                    f"warm-dashboards:   GET /dashboards/{PEER_TABLE_PATH}?asof={req.asof.isoformat()}"
                    "&scope=<default scope>&key=<default key>  and  scope=all   (named by the scopes reply)"
                )
        return 0
    emit(
        f"warm-dashboards: start · base {base_url} · night {night.isoformat()} · "
        f"as-of(s) {', '.join(a.isoformat() for a in asofs)} · {len(plan(asofs, pages))} planned requests"
    )
    outcome = run(
        asofs,
        pages,
        base_url=base_url,
        timeout_s=args.timeout_s,
        budget_s=args.budget_s,
        fetch=fetch,
        night=night,
        emit=emit,
    )
    emit(outcome.summary_line())
    path = write_warm_run(outcome.payload(), base_dir=args.runs_dir)
    if path is not None:
        emit(f"warm-dashboards: run artifact {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
