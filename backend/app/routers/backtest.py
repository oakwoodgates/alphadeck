"""``/backtest`` — the research surface, served from artifacts and never from a computation.

A backtest run is a multi-hour job written by a CLI in a venv carrying the `.[replay]` extra. These
routes only READ the JSON it left behind, which is the same shape the Scoreboard's replay panel already
has and is what keeps the job out of a web request.

**Dev/sig-only BY DATA AVAILABILITY, with no build flag.** On prod the store directory simply does not
exist, `available` comes back false, and the page renders one quiet line. That is the `scoreboard_replay`
idiom: absence is a legitimate state, never a 500, and there is no environment variable anyone can get
wrong.

**Read-only, and structurally so.** Nothing here writes, nothing here opens a point-in-time view, and no
simulated row has ever been near `calls`. The container's mount of the store is `:ro` besides.

**The five labels are BACKEND-authored** and ride every response, rendered verbatim by the front end (the
`ingest_note` precedent). They are the caveat that makes a number on this surface readable — public
clock, counterfactual universe, survivorship, adjusted closes, recompute-not-the-record — and composing
them client-side would let the page and the artifact drift apart.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query

from app.schemas_api import (
    BacktestLedgerOut,
    BacktestRunResponse,
    BacktestRunsResponse,
    BacktestRunSummaryOut,
    BacktestSweepRefOut,
    BacktestSweepResponse,
    BacktestSweepsResponse,
    ScoreboardMetricOut,
    ScoreboardReplayThesisOut,
    _scoreboard_episode_out,
)
from backtest import artifact
from backtest.ledger import BacktestLedger
from backtest.manifest import LABELS

router = APIRouter(prefix="/backtest", tags=["backtest"])


def _ledger_out(ledger: BacktestLedger) -> BacktestLedgerOut:
    """Project the ledger artifact onto the Scoreboard's wire models.

    The identity maps come out of the ARTIFACT, not out of `security_master`: the run resolved the
    names it reports at the moment it ran, and an immutable artifact whose tickers silently re-resolve
    years later is not immutable. It also keeps this router free of a database entirely — a structural
    guarantee rather than a discipline, and a test pins the import graph.
    """
    ciks: dict[UUID, str | None] = {}
    tickers: dict[UUID, str | None] = {}
    names: dict[UUID, str | None] = {}
    for sid, ref in ledger.securities.items():
        try:
            key = UUID(sid)
        except ValueError:  # a key that is not an id resolves nothing; the row still renders
            continue
        ciks[key], tickers[key], names[key] = ref.cik, ref.ticker, ref.name
    snap = ledger.snapshot
    return BacktestLedgerOut(
        banner=snap.banner,
        min_n=snap.min_n,
        n_theses=snap.n_theses,
        n_episodes=snap.n_episodes,
        n_censored=snap.n_censored,
        n_eligible=snap.n_eligible,
        metrics=[ScoreboardMetricOut.model_validate(m.model_dump()) for m in snap.metrics],
        theses=[
            ScoreboardReplayThesisOut(
                thesis_id=t.thesis_id,
                name=t.name,
                ticker=t.ticker,
                basket_size=t.basket_size,
                episodes=[_scoreboard_episode_out(e, ciks, tickers, names) for e in t.episodes],
            )
            for t in snap.theses
        ],
    )


@router.get("/runs", response_model=BacktestRunsResponse)
def list_runs() -> BacktestRunsResponse:
    """The run registry, newest first, plus how many runs have touched each dial.

    `available: false` when this stack has no backtest store — the normal state on prod.
    """
    if not artifact.store_exists():
        return BacktestRunsResponse(available=False)
    runs = artifact.list_runs()
    return BacktestRunsResponse(
        available=True,
        runs=[BacktestRunSummaryOut(**r.model_dump()) for r in runs],
        dial_trials=artifact.dial_trial_counts(runs),
    )


@router.get("/runs/{run_id}", response_model=BacktestRunResponse)
def read_run(run_id: str) -> BacktestRunResponse:
    """One run: its manifest, its pooled view, its episodes and its per-thesis ledger.

    An unknown run id returns `available: false` rather than a 404, so the page degrades exactly as it
    does when the whole store is absent — one code path on the front end, and a stale bookmark reads as
    "not here" rather than as an error. A run written before the ledger existed simply has none, and the
    drill-down says so rather than rendering an empty table.
    """
    payload = artifact.read_run(run_id)
    if payload is None:
        return BacktestRunResponse(available=False, run_id=run_id)
    ledger = payload["ledger"]
    return BacktestRunResponse(
        available=True,
        run_id=run_id,
        manifest=payload["manifest"],
        pooled=payload["pooled"],
        episodes=payload["episodes"],
        ledger=_ledger_out(ledger) if ledger is not None else None,
        labels=list(LABELS),
    )


@router.get("/sweeps", response_model=BacktestSweepsResponse)
def list_sweeps() -> BacktestSweepsResponse:
    """Every KEPT curve as a header, newest pass first — what a curve switcher is built from.

    A pass writes one curve per dial and `sweep.json` holds only the last of them, so before this the
    other five were unreachable from the surface the moment the sixth was written. Headers only: the
    points stay behind `/backtest/sweep`, because a listing that parsed every point of every curve would
    make the page's cost grow with the store's history.
    """
    if not artifact.store_exists():
        return BacktestSweepsResponse(available=False)
    return BacktestSweepsResponse(
        available=True,
        sweeps=[BacktestSweepRefOut(**r) for r in artifact.list_sweeps()],
        labels=list(LABELS),
    )


@router.get("/sweep", response_model=BacktestSweepResponse)
def read_sweep(
    pass_id: str | None = Query(default=None, description="the pass whose curve to serve"),
    dial: str | None = Query(default=None, description="the curve's dial key within that pass"),
    metric_slice: str | None = Query(
        default=None,
        description=(
            "the algorithm slice the curve was READ on, e.g. key1_source=ratified_catalyst. Empty "
            "string selects the pooled reading specifically; omitted matches either."
        ),
    ),
) -> BacktestSweepResponse:
    """One sweep curve: the LATEST by default, or a named one from the pass's kept copies.

    Unselected, this is `sweep.json` at the store root — latest-only, unchanged, so a link made before
    the curves became addressable still resolves to what it always did. Selected, it is the kept copy
    under `sweeps/`, and the selection takes THREE parts because one pass may carry the same dial twice —
    read pooled and read on one family — and those are two different measurements.

    A selection that matches nothing returns `available: false` rather than a 404, the same way an unknown
    run does: one code path on the front end, and a stale link reads as "not here" rather than as an
    error. Each point cites its own run_id either way, so the runs behind any curve stay addressable
    through `/backtest/runs/{id}`.
    """
    sweep = artifact.read_sweep(pass_id=pass_id, dial=dial, metric_slice=metric_slice)
    if sweep is None:
        return BacktestSweepResponse(
            available=False, pass_id=pass_id, dial=dial, metric_slice=metric_slice
        )
    return BacktestSweepResponse(
        available=True,
        sweep=sweep,
        pass_id=pass_id,
        dial=dial,
        metric_slice=metric_slice,
        labels=list(LABELS),
    )
