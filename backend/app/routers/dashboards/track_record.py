"""The Track Record by Cap Tier dashboard's API (``docs/dashboards/track-record.md``): what the tape did after each
Armed call, by market-cap tier at the event, at fixed horizons, against IWM and SPY, with n and the hit rate in every
cell. It READS the Scoreboard's own arm episodes (``scoreboard.record.scoreboard_records``, the same editions and the
same honesty policies the Scoreboard route takes; unlike the Scoreboard, a request that names no edition reads the
RECORD, Edition 0, while the editions nightly is paused) and, for the companies on those episodes, each one's bars, share
facts and split list through the point-in-time view the Cockpit builds, the benchmarks' bars and which companies have
a data flag that reaches their price readings (``dashboards.baskets.price_flags``). The request's one
``serve_view(asof)`` pair (``known_at`` + ``clock``) is threaded into every one of those reads. The pure compute is
``dashboards/track_record.py``; the page's whole read is computed once per (edition, honesty policies, clock, as-of) in
the shared dashboard cache (``dashboards/cache.py``, ``docs/DASHBOARDS.md`` rule 7), and tiering, slicing, filtering
and the entry toggle are the page's. Its wire models live here, not in ``app/schemas_api.py`` (rule 8).

Display only (rule 1): no write, no LLM, no ``call_for_thesis``. It imports only the Scoreboard modules the
Scoreboard route already imports, so it loads in the lean prod image (no ``duckdb``).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict

from app.deps import get_conn, get_current_tenant
from app.routers.scoreboard import _resolve_edition
from dashboards.baskets import FlaggedCompany, price_flags
from dashboards.cache import cached
from dashboards.peer_table import Gap
from dashboards.track_record import (
    HORIZONS,
    MIN_N,
    SLUG,
    STALE_DAYS,
    Benchmarks,
    CompanyData,
    EpisodeIn,
    Market,
    TrackRecord,
    assemble,
    bars_of,
    benchmarks_of,
    build_row,
    market_of,
)
from db.session import DEFAULT_TENANT_ID
from domain.market_cap import CapTier
from domain.market_time import market_tz, serve_view
from domain.settings import get_settings
from domain.splits import listed_from_row
from repositories import editions_repo
from repositories.editions_repo import Edition
from scoreboard.late import resolve_exclude_late
from scoreboard.record import resolve_exclude_annotated, scoreboard_records
from securities import master
from signals.base import PointInTimeData

router = APIRouter(prefix="/track-record")

# Companies per point-in-time view. The page reads each company's WHOLE bar history (an episode can sit anywhere in
# it), so the chunk is smaller than the shared 100 the bounded display reads use.
READ_CHUNK = 40

# The share-fact columns the cap reads; the rest of the row is dropped as the view is read.
# F9: ``filing_accepted`` / ``recorded_at`` say when a count was PUBLIC (``dashboards.track_record.share_public_at``)
SHARE_COLUMNS = (
    "shares",
    "valid_from",
    "ads_ratio",
    "ads_ratio_status",
    "filing_accepted",
    "recorded_at",
)


class _Wire(BaseModel):
    # read straight off the compute's frozen dataclasses
    model_config = ConfigDict(from_attributes=True)


# --- the wire models -----------------------------------------------------------------------------------------


class TrackSpanOut(_Wire):
    """One horizon of one entry rule. ``counted``: the name's return from the entry close to the horizon bar's close
    (as traded, no dividends), with IWM's and SPY's returns over the same two dates (``null`` when that benchmark has
    no bar on one of them). ``maturing``: the horizon has not arrived yet. ``path_ended``: the market has had the
    sessions and the name's bars stop short of them (a delisting, a halt, a stale tape). ``none``: nothing to measure
    (no ``exit_by``, or no session between the entry and it). Only ``counted`` carries numbers. Returns are
    fractions (0.05 = 5%)."""

    status: Literal["counted", "maturing", "path_ended", "none"]
    end_date: date | None
    ret: float | None
    iwm: float | None
    spy: float | None


class TrackEntryOut(_Wire):
    """One entry rule's read of an episode: the entry bar (``null`` when the name has none yet) and a ``TrackSpanOut``
    per horizon (``h5`` / ``h10`` / ``h20`` / ``h60``: sessions of the name's own bars after the entry bar;
    ``exit_by``: the last bar on or before the episode's own ``exit_by``)."""

    entry_date: date | None
    entry_close: float | None
    spans: dict[str, TrackSpanOut]


class TrackCapOut(_Wire):
    """The approximate market cap (USD) AT THE EVENT and its tier: the as-traded close of the last session before the
    arm date times the newest share count whose cover date is before it (``close_date`` / ``shares_date`` are those
    two dates), ``ads_ratio`` applied when known, ``split_factor`` = the multiplier put on the count when a split in
    force sits between the count's date and the close's. ``gap`` says why a cap could not be pinned (``null`` when it
    is); ``tier`` is ``null`` then."""

    value: float | None
    tier: CapTier | None
    gap: Gap | None
    note: str | None
    close: float | None
    close_date: date | None
    shares: float | None
    shares_date: date | None
    ads_ratio: int | None
    split_factor: float | None


class TrackRowOut(_Wire):
    """One Scoreboard arm episode: identity, the entry attributes the filter chips use, the cap and liquidity at the
    event and both entry rules' reads. ``left_out`` (``flagged`` | ``censored_start`` | ``ingest_flagged``) = the
    episode is in no cell (its reason in ``left_out_note``) and carries identity only. ``liquidity`` is the 20-day
    average dollar volume band at the event (``under_1m`` | ``1m_10m`` | ``10m_plus``; ``null`` when fewer than 20
    volume-bearing sessions precede it)."""

    thesis_id: UUID
    thesis_name: str
    security_id: UUID
    ticker: str
    arm_date: date
    exit_by: date | None
    arm_until: date | None
    close_reason: str
    is_headline: bool
    key1_source: str | None
    entry_grade: str | None
    confirmation_grade: str | None
    co_arm_bucket: str
    left_out: Literal["flagged", "censored_start", "ingest_flagged"] | None
    left_out_note: str | None
    cap: TrackCapOut | None
    adv_usd: float | None
    liquidity: str | None
    arm_night: TrackEntryOut | None
    next_session: TrackEntryOut | None


class TrackHorizonCountOut(_Wire):
    """How the counted episodes read at one horizon of one entry rule."""

    counted: int
    maturing: int
    path_ended: int
    none: int


class TrackCountsOut(_Wire):
    """What the page rests on. ``episodes`` = every Scoreboard episode in the record at the as-of; ``counted`` = those
    in the cells; ``left_out_*`` = those set aside, each episode once (flagged price history first, then censored
    start, then ingest flag); ``unpinned_cap`` / ``unpinned_liquidity`` = counted episodes with no cap tier / no
    liquidity band; ``arm_night`` / ``next_session`` = per horizon, how the counted episodes read.
    """

    episodes: int
    counted: int
    left_out_flagged: int
    left_out_censored: int
    left_out_ingest: int
    unpinned_cap: int
    unpinned_liquidity: int
    arm_night: dict[str, TrackHorizonCountOut]
    next_session: dict[str, TrackHorizonCountOut]


class TrackCellOut(_Wire):
    """One table cell: n, how many were above zero (``held``) and the hit rate, the median and the quartiles of the
    excess (the raw return under "raw"), the medians of the names' and the benchmark's raw returns over the same
    episodes, and ``no_comparison`` = counted spans left out because the comparison had no bar. ``too_small`` = n is
    under ``min_n``: read nothing from it."""

    n: int
    held: int
    hit_rate: float | None
    median: float | None
    q1: float | None
    q3: float | None
    name_median: float | None
    bench_median: float | None
    no_comparison: int
    too_small: bool


class TrackTableRowOut(_Wire):
    """One row of the opening table: the slice key (a cap tier, ``unpinned``, or ``pooled`` LAST), its counted
    episodes and a cell per horizon."""

    key: str
    episodes: int
    cells: dict[str, TrackCellOut]


class TrackFlaggedOut(_Wire):
    """A company on a Scoreboard episode whose data flags reach its price readings at the as-of (``reason`` = the
    flags' labels): its episodes are left out of every cell."""

    security_id: UUID
    ticker: str
    reason: str


class TrackEditionOut(_Wire):
    """A Scoreboard edition: 0 = the original record (the calls log); N >= 1 = a stored re-run."""

    number: int
    label: str


class TrackRecordOut(_Wire):
    """The Track Record at the as-of: every episode's row (newest arm first), the counts, the companies left out for a
    flagged price history and the opening table (IWM, the next-session entry, by cap tier, every armed member; the page
    recomputes any other reading from ``rows`` with the same arithmetic). ``edition`` and the two policies are the
    Scoreboard's own (``annotated_policy`` / ``late_policy``: ``included`` | ``excluded`` | ``n/a`` for a stored
    edition, which has no such rows). ``edition`` is the one READ (the record when the request named none);
    ``newest_edition`` is the newest PUBLISHED edition (``null`` when none is), which the page offers beside the
    record. ``market_source`` = whose bar dates are the market's session clock;
    ``benchmarks_missing`` = a benchmark with no bar at the as-of; ``stale_days`` = how far a name's tape may sit
    behind the market before an unreached horizon reads path ended; ``min_n`` = the smallest n a cell may be read
    at. ``cards`` = cards with a recorded call, ``cards_unreadable`` = cards whose record could not be read.
    ``clock``: ``record`` (the live read) or ``public`` (a past as-of: what was public by the end of that date).
    ``cache`` / ``compute_ms`` = whether the read came from the shared dashboard cache and how long it took.
    """

    asof: date
    clock: Literal["record", "public"]
    edition: TrackEditionOut
    newest_edition: TrackEditionOut | None
    annotated_policy: Literal["included", "excluded", "n/a"]
    late_policy: Literal["included", "excluded", "n/a"]
    horizons: list[str]
    min_n: int
    market_source: Literal["SPY", "IWM"] | None
    benchmarks_missing: list[str]
    stale_days: int
    cards: int
    cards_unreadable: int
    counts: TrackCountsOut
    rows: list[TrackRowOut]
    flagged: list[TrackFlaggedOut]
    opening: list[TrackTableRowOut]
    cache: Literal["hit", "computed"]
    compute_ms: int


# --- the reads -----------------------------------------------------------------------------------------------


def read_companies(
    conn: psycopg.Connection,
    sids: list[UUID],
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
) -> dict[UUID, CompanyData]:
    """Each company's bars, share facts and split list through the request's point-in-time view (``known_at`` +
    ``clock`` from ``serve_view``), ``READ_CHUNK`` companies per view and UNBOUNDED in time: an episode can sit
    anywhere in a name's history. ``view_day`` is the view's market day, the scale a history view's bars are on.
    """
    out: dict[UUID, CompanyData] = {}
    for i in range(0, len(sids), READ_CHUNK):
        chunk = sids[i : i + READ_CHUNK]
        pit = PointInTimeData(
            conn,
            asof=asof,
            known_at=known_at,
            tenant_id=DEFAULT_TENANT_ID,
            basket=chunk,
            bounds=None,
            clock=clock,
        )
        view_day = pit.known_at.astimezone(market_tz()).date()
        for sid in chunk:
            out[sid] = CompanyData(
                bars=tuple(bars_of(pit.price_history(sid))),
                shares=tuple(
                    {k: f.get(k) for k in SHARE_COLUMNS} for f in pit.shares_outstanding_facts(sid)
                ),
                splits=tuple(listed_from_row(r) for r in pit.split_events(sid)),
                view_day=view_day,
            )
    return out


def read_benchmarks(
    conn: psycopg.Connection,
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
    stale_days: int,
) -> tuple[Benchmarks, Market]:
    """SPY's and IWM's bars at the as-of through the same view, and the market's session clock they give."""
    pit = PointInTimeData(
        conn,
        asof=asof,
        known_at=known_at,
        tenant_id=DEFAULT_TENANT_ID,
        bounds=None,
        clock=clock,
    )
    spy, iwm = bars_of(pit.benchmark_prices("SPY")), bars_of(pit.benchmark_prices("IWM"))
    return benchmarks_of(spy, iwm), market_of(spy, iwm, stale_days=stale_days)


def read_record(
    conn: psycopg.Connection,
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
    edition: Edition | None,
    exclude_annotated: bool,
    exclude_late: bool,
) -> TrackRecord:
    """The whole read: the Scoreboard's episodes (archived cards included, as the Scoreboard does), which companies
    have a price-reaching data flag, the bars of every company with an episode that will be counted, the benchmarks;
    then ``assemble``. Every read takes the request's ``known_at`` + ``clock``."""
    result, _, _ = scoreboard_records(
        conn,
        asof,
        include_archived=True,
        known_at=known_at,
        exclude_annotated=exclude_annotated,
        exclude_late=exclude_late,
        edition=edition,
    )
    stale_days = (
        get_settings().tape_stale_days or STALE_DAYS
    )  # 0 disables the monitor; keep a threshold
    scored = [
        (t, se)
        for t in result.theses
        if t.error is None
        for se in t.episodes
        if se.episode.arm_date <= asof  # an episode armed after the as-of is not an event
    ]
    sids = sorted({se.episode.security_id for _, se in scored}, key=str)
    tickers = master.tickers_for(conn, sids, tenant_id=DEFAULT_TENANT_ID)
    flags = price_flags(
        conn, sids, asof, known_at=known_at, clock=clock, tenant_id=DEFAULT_TENANT_ID
    )
    # only a company with an episode that will be COUNTED is read: a left-out episode carries identity only
    needed = sorted(
        {
            se.episode.security_id
            for _, se in scored
            if not (se.censored_start or se.ingest_flagged or se.episode.security_id in flags)
        },
        key=str,
    )
    companies = read_companies(conn, needed, asof, known_at, clock)
    bench, market = read_benchmarks(conn, asof, known_at, clock, stale_days)

    rows = []
    for t, se in scored:
        ep = se.episode
        rows.append(
            build_row(
                EpisodeIn(
                    thesis_id=t.thesis_id,
                    thesis_name=t.name,
                    security_id=ep.security_id,
                    ticker=tickers.get(ep.security_id) or "?",
                    arm_date=ep.arm_date,
                    exit_by=ep.exit_by,
                    arm_until=ep.arm_until,
                    close_reason=ep.close_reason,
                    is_headline=ep.is_headline,
                    key1_source=ep.key1_source,
                    entry_grade=ep.entry_grade.value if ep.entry_grade else None,
                    confirmation_grade=(
                        ep.confirmation_grade.value if ep.confirmation_grade else None
                    ),
                    co_arm_bucket=ep.co_arm_bucket,
                    censored_start=se.censored_start,
                    ingest_flagged=se.ingest_flagged,
                    ingest_note=se.ingest_note,
                    price_flag=flags.get(ep.security_id),
                ),
                companies.get(ep.security_id),
                bench,
                market,
                asof,
            )
        )
    return assemble(
        rows,
        flagged=[
            FlaggedCompany(sid, tickers.get(sid) or "?", reason) for sid, reason in flags.items()
        ],
        market=market,
        bench=bench,
        cards=result.n_with_record,
        cards_unreadable=sum(1 for t in result.theses if t.error is not None),
    )


# --- the route -----------------------------------------------------------------------------------------------


def _newest_edition(conn: psycopg.Connection, tenant: UUID) -> Edition | None:
    """The newest PUBLISHED edition (what the page offers beside the record); ``None`` = nothing is published.
    Not ``_default_edition``: the Scoreboard's default can be pinned by its server setting, and the page's
    selector says "newest published"."""
    return editions_repo.newest(conn, tenant_id=tenant)


@router.get("", response_model=TrackRecordOut)
def track_record(
    asof: date = Query(..., description="as-of date; nothing published after it is read"),
    edition: int = Query(
        0,
        ge=0,
        description="which Scoreboard EDITION to read: 0 (the default) = the original record (the calls log); "
        "N >= 1 = the stored re-run Edition N (404 if it does not exist). Unlike the Scoreboard, a request that "
        "names no edition reads the record, not the newest published edition, while the editions nightly is "
        "paused; the response's `newest_edition` names the newest published one",
    ),
    include_annotated: bool | None = Query(
        None,
        description="the Scoreboard's annotated-night reading: true = keep rows annotated `exclude` (phantom "
        "arms); false = set them aside; omitted = the server setting. The response names the reading used",
    ),
    include_late: bool | None = Query(
        None,
        description="the Scoreboard's late-night reading: true = keep rows recorded after their night's next "
        "EDGAR open; false = set them aside; omitted = the server setting. The response names the reading used",
    ),
    conn: psycopg.Connection = Depends(get_conn),
    current_tenant: UUID = Depends(get_current_tenant),
) -> TrackRecordOut:
    """What the tape did after each arm episode the Scoreboard records, by market-cap tier at the event: one row per
    episode with its cap tier and liquidity band, and, for the next session's entry and the arm night's, the return at
    5, 10, 20 and 60 sessions of the name's own bars and to the episode's own exit_by, with IWM's and SPY's returns
    over the same dates. The Scoreboard's own episodes and honesty filters: a censored start, an ingest-flagged
    episode and a company with a flagged price history are left out of every cell and counted. Reads the record
    (Edition 0) unless an edition is named. Display only; a past as-of reads what was public by the end of that
    date, and a horizon that has not arrived by then is not counted.
    """
    known_at, clock = serve_view(asof)
    chosen = _resolve_edition(
        conn, current_tenant, edition
    )  # 0 = the record; the Scoreboard's own 404
    if chosen is None:
        exclude_annotated = resolve_exclude_annotated(
            None if include_annotated is None else not include_annotated
        )
        exclude_late = resolve_exclude_late(None if include_late is None else not include_late)
    else:  # a stored edition has no reconstructed, annotated or late rows: the readings do not apply
        exclude_annotated = exclude_late = False
    number = chosen.number if chosen is not None else 0
    newest = _newest_edition(conn, current_tenant)  # one small read, outside the cache
    got = cached(
        conn,
        SLUG,
        (number, not exclude_annotated, not exclude_late, clock),
        asof,
        lambda: read_record(conn, asof, known_at, clock, chosen, exclude_annotated, exclude_late),
    )
    page = got.value
    return TrackRecordOut.model_validate(
        {
            "asof": asof,
            "clock": clock,
            "edition": {
                "number": number,
                "label": chosen.label if chosen is not None else editions_repo.EDITION_0_LABEL,
            },
            "newest_edition": (
                None if newest is None else {"number": newest.number, "label": newest.label}
            ),
            "annotated_policy": (
                "n/a" if chosen is not None else "excluded" if exclude_annotated else "included"
            ),
            "late_policy": (
                "n/a" if chosen is not None else "excluded" if exclude_late else "included"
            ),
            "horizons": list(HORIZONS),
            "min_n": MIN_N,
            "market_source": page.market_source,
            "benchmarks_missing": list(page.benchmarks_missing),
            "stale_days": page.stale_days,
            "cards": page.cards,
            "cards_unreadable": page.cards_unreadable,
            "counts": page.counts,
            "rows": list(page.rows),
            "flagged": list(page.flagged),
            "opening": list(page.opening),
            "cache": got.status,
            "compute_ms": got.compute_ms,
        }
    )
