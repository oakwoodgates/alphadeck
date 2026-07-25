from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query

from app.deps import get_conn, get_current_tenant, get_thesis_or_404
from app.schemas_api import (
    InsiderBuyOut,
    PriceBar,
    ScoreboardMetricOut,
    ScoreboardPriceWindowOut,
    ScoreboardReplayResponse,
    ScoreboardReplayThesisOut,
    ScoreboardResponse,
    ScoreboardSummaryOut,
    _scoreboard_episode_out,
    _scoreboard_thesis_out,
)
from db.session import DEFAULT_TENANT_ID
from domain.settings import get_settings
from domain.thesis import Thesis
from pipeline.schedule import expected_runs_behind, last_expected_asof, parse_run_at
from replay.metrics import MetricResult
from repositories import calls_repo
from scoreboard.artifact import read_snapshot
from scoreboard.assemble import assemble_scoreboard
from scoreboard.overlays import (
    SMA_WARMUP_DAYS,
    UNIVERSE_LOOKBACK_DAYS,
    annotate_sma,
    episode_insider_buys,
    known_at_for_asof,
    thesis_created_at,
    universe_floor,
)
from scoreboard.prices import PgRealizedPrices
from securities import master

router = APIRouter(prefix="/scoreboard", tags=["scoreboard"])


def _now() -> datetime:
    """Container-local wall clock (compose pins ``TZ=America/New_York``) — a seam so tests pin the
    clock; the schedule math itself is pure over the injected now. Copied from ``admin.py`` (one
    contract, two surfaces — the earmark until a durable ``market_today()`` lands)."""
    return datetime.now()


def _run_at() -> time:
    """The schedule wall time off ``Settings.cron_run_at`` (env ``ALPHADECK_CRON_AT`` — the same var
    the sidecar + admin read), so the Scoreboard's staleness models the SAME schedule. A malformed
    value is a DEPLOY error → a loud, actionable 500. Copied from ``admin.py``."""
    raw = get_settings().cron_run_at
    try:
        return parse_run_at(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=500, detail=f"ALPHADECK_CRON_AT is malformed ({raw!r}): {exc}"
        ) from exc


@router.get("", response_model=ScoreboardResponse)
def get_scoreboard(
    asof: date = Query(..., description="score the record as-of this date (caps both axes)"),
    include_archived: bool = Query(
        True,
        description="archived theses ride the record by default (archiving stops accrual, "
        "never erases the record); false is the explicit, reversible filter",
    ),
    conn: psycopg.Connection = Depends(get_conn),
) -> ScoreboardResponse:
    """The Scoreboard (SCORE): the call-of-record scored as-of ``asof`` — a READ-ONLY pass over
    the immutable calls log, the operator-decision log, and realized asof-capped prices. The
    RECORD is the scoring source, never a recompute (replay re-derives history; this holds the
    platform to what it actually said). Aggregate metrics judge only matured, non-censored
    episodes and gate below ``min_n`` — an instrument, not a claim, until n accrues.
    """
    result = assemble_scoreboard(conn, asof=asof, include_archived=include_archived)
    theses_out = []
    for t in result.theses:
        # Resolve tickers/CIKs under the THESIS's tenant (the get_call precedent): episode names
        # + the trigger evidence's names, so provenance links attribute correctly per tenant.
        sids = (
            {e.episode.security_id for e in t.episodes}
            | {tr.security_id for e in t.episodes for tr in e.triggers_at_arm}
            | {s.security_id for s in t.operator_spans if s.security_id is not None}
        )
        tenant = t.tenant_id or DEFAULT_TENANT_ID
        ciks = master.ciks_for(conn, sids, tenant_id=tenant)
        tickers = master.tickers_for(conn, sids, tenant_id=tenant)
        theses_out.append(_scoreboard_thesis_out(t, ciks, tickers))
    summary = result.summary  # assemble_scoreboard always fills it

    # Record freshness (compute-on-read; the read still writes nothing) — the same staleness the admin
    # page shows, surfaced where the Board-vs-Scoreboard confusion happened. record_edge is the
    # UNCAPPED calls-log MAX(asof) ("is the record current NOW"), independent of the request asof — so
    # it is identical whether the view is scrubbed to the past or to today; the FE shows it only on the
    # live view (asof >= today). Measured vs the last EXPECTED Mon-Fri+RUN_AT run (never raw
    # today - edge); edge None = the record has never begun (quiet: days_behind None, stale False).
    run_at = _run_at()
    now = _now()
    edge = calls_repo.record_edge(conn)
    expected = last_expected_asof(now, run_at)
    days_behind = expected_runs_behind(edge, expected)

    return ScoreboardResponse(
        asof=result.asof,
        generated_at=datetime.now(timezone.utc).isoformat(),
        summary=ScoreboardSummaryOut(
            n_theses=result.n_theses,
            n_with_record=result.n_with_record,
            n_episodes=result.n_episodes,
            n_open=result.n_open,
            n_matured=result.n_matured,
            n_censored=result.n_censored,
            n_ingest_flagged=result.n_ingest_flagged,
            n_takes=result.n_takes,
            n_passes=result.n_passes,
            n_overrides=result.n_overrides,
            n_voided=result.n_voided,
            n_eligible=summary.n_eligible if summary else 0,
            record_began=summary.record_began if summary else None,
            banner=summary.banner if summary else "",
            min_n=summary.min_n if summary else 0,
            next_maturity=summary.next_maturity if summary else None,
            n_maturing_30d=summary.n_maturing_30d if summary else 0,
            projected_min_n_date=summary.projected_min_n_date if summary else None,
            metrics=[
                ScoreboardMetricOut(
                    name=m.name,
                    claim=m.claim,
                    n=m.n,
                    insufficient_n=m.insufficient_n,
                    summary=m.summary,
                    detail=m.detail,
                    note=m.note,
                )
                for m in (summary.metrics if summary else [])
            ],
            record_edge=edge,
            expected_asof=expected,
            days_behind=days_behind,
            stale=bool(days_behind),  # None (never begun) and 0 (current) are both quiet
            today=now.date(),
        ),
        theses=theses_out,
    )


def _metric_out(m: MetricResult) -> ScoreboardMetricOut:
    return ScoreboardMetricOut(
        name=m.name,
        claim=m.claim,
        n=m.n,
        insufficient_n=m.insufficient_n,
        summary=m.summary,
        detail=m.detail,
        note=m.note,
    )


@router.get("/replay", response_model=ScoreboardReplayResponse)
def get_scoreboard_replay(
    conn: psycopg.Connection = Depends(get_conn),
) -> ScoreboardReplayResponse:
    """The HISTORICAL (replayed) panel — replayed history served from the operator-kicked artifact
    (``python -m scoreboard.replay_snapshot``, dev venv only: replay needs the .[replay] extra the
    lean image deliberately lacks). A RECOMPUTE by construction — today's code + dials over
    historical facts, the not-bitemporal basket caveat riding the banner — NEVER the record and
    never merged with it: separate artifact, separate endpoint, metrics never pooled with the live
    summary. ``available=false`` when no artifact exists (or it fails validation — absence, not an
    outage). Read-only; the container's artifact mount is read-only besides.
    """
    snap = read_snapshot()
    if snap is None:
        return ScoreboardReplayResponse(available=False)
    theses_out = []
    for t in snap.theses:
        sids = {e.episode.security_id for e in t.episodes} | {
            tr.security_id for e in t.episodes for tr in e.triggers_at_arm
        }
        tenant = t.tenant_id or DEFAULT_TENANT_ID
        ciks = master.ciks_for(conn, sids, tenant_id=tenant)
        tickers = master.tickers_for(conn, sids, tenant_id=tenant)
        theses_out.append(
            ScoreboardReplayThesisOut(
                thesis_id=t.thesis_id,
                name=t.name,
                ticker=t.ticker,
                basket_size=t.basket_size,
                episodes=[_scoreboard_episode_out(e, ciks, tickers) for e in t.episodes],
            )
        )
    return ScoreboardReplayResponse(
        available=True,
        generated_at=snap.generated_at,
        window_start=snap.window_start,
        window_end=snap.window_end,
        known_at_pin=snap.known_at_pin,
        record_began=snap.record_began,
        window_overlaps_record=snap.window_overlaps_record,
        banner=snap.banner,
        min_n=snap.min_n,
        n_theses=snap.n_theses,
        n_episodes=snap.n_episodes,
        n_censored=snap.n_censored,
        n_eligible=snap.n_eligible,
        metrics=[_metric_out(m) for m in snap.metrics],
        theses=theses_out,
    )


@router.get("/price-window", response_model=ScoreboardPriceWindowOut)
def get_price_window(
    security_id: UUID = Query(..., description="the episode's basket-member security to chart"),
    start: date = Query(
        ...,
        description="ADVISORY window start (the FE's per-episode cache anchor). The server owns the "
        "effective floor = max(thesis.created_at − 365d, first_bar) and loads [floor, end] regardless; "
        "the response echoes that effective floor as ``start``",
    ),
    end: date = Query(
        ...,
        description="window end — the episode's exit_by; the series is still capped at asof "
        "server-side, so a future exit_by never widens it past asof",
    ),
    asof: date = Query(
        ..., description="score as-of this date — the series is CAPPED here (no-lookahead)"
    ),
    thesis: Thesis = Depends(get_thesis_or_404),
    current_tenant: UUID = Depends(get_current_tenant),
    conn: psycopg.Connection = Depends(get_conn),
) -> ScoreboardPriceWindowOut:
    """One episode's realized daily OHLCV bars over ``[start, end]`` — with SMA 50/200 context and the
    window's open-market insider buys — for the Scoreboard drawer's chart (Slice 3, extended in Slice A).
    The SAME asof-capped read the scorer runs (``PgRealizedPrices``; ``bars_between`` shares
    ``closes_between``'s cap/known_at), served on demand instead of embedded in the ledger payload (which
    stays lean). The line draws ``close``; open/high/low/volume ride the wire for a later candlestick.

    No-lookahead (invariant #1) is enforced SERVER-SIDE and never trusted to the client, on BOTH axes:
    - the price reader caps the valid-time axis at ``cap = asof`` (``d <= asof``), so a client passing a
      future ``end`` still gets only bars ``<= asof`` — the window can never be widened past the as-of. Its
      ``known_at`` stays the reader's default (now), matching the live scorer's construction, so the line
      matches the bars behind the ledger's numbers.
    - the insider read caps the transaction axis too (``known_at_for_asof(asof)`` = ``min(now, asof-EOD)``),
      so a scrubbed-back as-of hides not just later bars but later-DISCLOSED buys — the honesty a filing's
      days-to-months disclosure lag demands (the IBM 166-day case), the price bar's ``valid_from == d`` does
      not need.

    RELEVANCE FLOOR (Slice A R1): the server bounds the whole window (bars + SMA + insider_buys) to
    ``[floor, end]`` where ``floor = max(thesis.created_at − 365d, first_bar)`` — a thesis born 2026-07 does
    not plot a 2020 buy (off-story, NOT a recall cut). The requested ``start`` is advisory; the response
    ``start`` is the effective floor so the FE knows the loaded extent (it loads the whole universe and pans
    the visible range). The floor is ADDITIVE to the no-lookahead caps above, never a replacement.

    SMA is computed over a WARM-UP read (``floor − SMA_WARMUP_DAYS`` through ``end``, asof-capped) so the
    left edge is honest, then only bars ``>= floor`` are returned, each annotated (``None`` where too little
    history exists — never padded). Prices read under the THESIS'S tenant; a thesis the deployment
    tenant can't see is a 404. ``source`` names the fact table the bars came from (invariant #6).
    ``start``/``end``/``security_id`` ride as bound params — the SQL range fragment stays a trusted literal.
    """
    tenant = thesis.tenant_id or DEFAULT_TENANT_ID
    if tenant != current_tenant:
        # Deferred auth: ``get_thesis_or_404`` loads by id only (no tenant filter), so scope the read to
        # the deployment's tenant HERE — a thesis owned by another tenant is not visible (404, not a leak).
        raise HTTPException(status_code=404, detail="thesis not found")
    # cap=asof carries the no-lookahead guarantee on the valid axis; known_at defaults to now (the scorer's
    # transaction-axis config), so the chart matches the bars behind the ledger's numbers.
    reader = PgRealizedPrices(conn, tenant_id=tenant, cap=asof)
    # R1: the BACKEND owns the relevance floor (it has the thesis) = max(created_at − 365d, first_bar). The
    # loaded window is [floor, end] REGARDLESS of the requested ``start`` (advisory now — the FE loads the
    # whole universe and pans to it). Warm-up reads BEHIND the created floor so an SMA at ``floor`` sees its
    # real prior closes (the same asof-capped ``bars_between`` — never a forked as-of path). The relevance
    # floor is ADDITIVE to the no-lookahead caps (cap=asof, known_at≤asof), never a replacement.
    created_at = thesis_created_at(conn, thesis.id)
    created_floor = created_at - timedelta(days=UNIVERSE_LOOKBACK_DAYS)
    warmup = reader.bars_between(security_id, created_floor - timedelta(days=SMA_WARMUP_DAYS), end)
    floor = universe_floor(created_at, warmup[0]["d"] if warmup else None)
    annotated = annotate_sma(warmup)
    window_bars = [b for b in annotated if b["d"] >= floor]
    # The offer-price insider screen needs the EOD low on each trade date — built from the SAME asof-capped
    # price view (no lookahead); an absent low keeps the buy (recall-safe, #9).
    day_lows = {b["d"]: b["low"] for b in warmup if b.get("low") is not None}
    buys = episode_insider_buys(
        conn,
        tenant_id=tenant,
        security_id=security_id,
        start=floor,  # events bounded to the relevance window, regardless of the requested start
        end=end,
        asof=asof,
        known_at=known_at_for_asof(asof),
        day_lows=day_lows,
    )
    return ScoreboardPriceWindowOut(
        thesis_id=thesis.id,
        security_id=security_id,
        start=floor,  # the EFFECTIVE floor — the FE reads it to know the loaded extent (R1)
        end=end,
        asof=asof,
        source="fact_price_eod",
        bars=[PriceBar(**b) for b in window_bars],
        insider_buys=[InsiderBuyOut(**b) for b in buys],
    )
