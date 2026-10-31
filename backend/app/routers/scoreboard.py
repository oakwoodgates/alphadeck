from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query

from app.deps import get_conn, get_current_tenant, get_thesis_or_404
from app.schemas_api import (
    ActivistStakeOut,
    AnnotatedNightOut,
    CorporateEventOut,
    EditionCompanyDiffOut,
    EditionCutNoticeOut,
    EditionDiffCountsOut,
    EditionThesisDiffOut,
    InsiderBuyOut,
    InsiderSellOut,
    LateNightOut,
    PriceBar,
    ScoreboardEditionDiffResponse,
    ScoreboardEditionOut,
    ScoreboardEditionsResponse,
    ScoreboardMetricOut,
    ScoreboardPriceWindowOut,
    ScoreboardResponse,
    ScoreboardSummaryOut,
    _scoreboard_thesis_out,
)
from db.clock import db_now
from db.session import DEFAULT_TENANT_ID
from domain.market_time import known_at_for_asof, market_now, serve_view, view_day
from domain.settings import get_settings
from domain.splits import tape_scale
from domain.thesis import Thesis
from pipeline.schedule import expected_runs_behind, last_expected_asof, parse_run_at
from repositories import calls_repo, editions_repo, split_repo
from scoreboard.assemble import assemble_scoreboard
from scoreboard.diff import edition_diff
from scoreboard.overlays import (
    SMA_WARMUP_DAYS,
    UNIVERSE_LOOKBACK_DAYS,
    annotate_sma,
    episode_activist_stakes,
    episode_corporate_events,
    episode_insider_buys,
    episode_insider_sells,
    thesis_created_at,
    universe_floor,
)
from scoreboard.prices import PgRealizedPrices
from securities import master

router = APIRouter(prefix="/scoreboard", tags=["scoreboard"])


def _now() -> datetime:
    """The MARKET wall clock (``domain/market_time.market_now`` — an explicit ``ZoneInfo``) — a seam so
    tests pin the clock; the schedule math itself is pure over the injected now. The earmark this
    duplication carried is RESOLVED: both surfaces now share one contract in ``domain/market_time``, and
    the two ``_now`` wrappers survive only as the per-router monkeypatch seams the tests pin."""
    return market_now()


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
    include_annotated: bool | None = Query(
        None,
        description="the annotated-night reading: true = score rows annotated `exclude` (phantom "
        "arms, and rows dated a day with no market session) as before; false = set them aside like "
        "reconstructed rows; omitted = the server setting (ALPHADECK_SCOREBOARD_EXCLUDE_ANNOTATED, "
        "default: set aside). One reading covers every `exclude` whatever its rule. The response's "
        "summary.annotated_policy names the reading used",
    ),
    include_late: bool | None = Query(
        None,
        description="the late-night reading: true = score rows recorded after their night's next "
        "EDGAR open (06:00 ET the next weekday) as before; false = set them aside, so an on-time row "
        "for the night wins and a late-only night reads like a cron gap; omitted = the server setting "
        "(ALPHADECK_SCOREBOARD_EXCLUDE_LATE, default: set aside). The response's summary.late_policy "
        "names the reading used",
    ),
    edition: int | None = Query(
        None,
        ge=0,
        description="which EDITION to score: 0 = the original record (the calls log); N >= 1 = the "
        "stored re-run Edition N (404 if it does not exist); omitted = the NEWEST published edition (0 "
        "when nothing is published; ALPHADECK_SCOREBOARD_DEFAULT_EDITION, unset by default, overrides "
        "it, and an unpublished override falls back to the newest). The response's `edition` block "
        "names the edition served",
    ),
    conn: psycopg.Connection = Depends(get_conn),
    current_tenant: UUID = Depends(get_current_tenant),
) -> ScoreboardResponse:
    """The Scoreboard (SCORE): one EDITION scored as-of ``asof`` — a READ-ONLY pass over the
    immutable calls log (or a stored edition's frozen pages), the operator-decision log, and realized
    asof-capped prices. Edition 0 is the call-of-record: the RECORD is the scoring source, never a
    recompute (this holds the platform to what it actually said). Edition N >= 1 is a stored re-run of
    today's rules from each company's join date on the public clock, scored on read the same way. A
    request that names no edition reads the NEWEST published one (Edition 0 when nothing is published).
    Aggregate metrics judge only matured, non-censored episodes and gate below ``min_n`` — an
    instrument, not a claim, until n accrues.

    On Edition 0 three kinds of recorded row are set aside before scoring: reconstructed rows
    (always), rows annotated ``exclude`` (phantom arms, and rows dated a day with no market session;
    by the annotated-night policy, which ``include_annotated`` overrides per request), and rows
    recorded after their night's next EDGAR
    open (by the late-night policy, which ``include_late`` overrides per request). Every reading is
    available; nothing is deleted. An edition has none of those rows: its lists come back empty and
    both policies read ``n/a``.
    """
    chosen = _resolve_edition(conn, current_tenant, edition)
    result = assemble_scoreboard(
        conn,
        asof=asof,
        include_archived=include_archived,
        # None = the server setting; an explicit include_annotated inverts into the exclude policy
        exclude_annotated=None if include_annotated is None else not include_annotated,
        # L1 — the same tri-state for the late-night reading
        exclude_late=None if include_late is None else not include_late,
        edition=chosen,
    )
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
        names = master.names_for(conn, sids, tenant_id=tenant)
        theses_out.append(_scoreboard_thesis_out(t, ciks, tickers, names))
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
            reconstructed_nights=summary.reconstructed_nights if summary else [],
            annotated_policy=summary.annotated_policy if summary else "excluded",
            annotated_nights=[
                AnnotatedNightOut(**n.model_dump())
                for n in (summary.annotated_nights if summary else [])
            ],
            late_policy=summary.late_policy if summary else "excluded",
            late_nights=[
                LateNightOut(**n.model_dump()) for n in (summary.late_nights if summary else [])
            ],
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
        edition=(
            _edition_out(chosen)
            if chosen is not None
            else _record_edition_out(calls_repo.record_first(conn), edge)
        ),
    )


# --- editions (S4) -------------------------------------------------------------------------------------


def _default_edition(conn: psycopg.Connection, tenant: UUID) -> editions_repo.Edition | None:
    """The edition a read with no ``edition`` serves: the NEWEST published edition (operator decision
    2026-10-23), so the default moves on its own when a new edition is cut; ``None`` = Edition 0, the record,
    which is also what an empty store serves. ``Settings.scoreboard_default_edition`` is an optional
    override: 0 pins the record, N >= 1 pins Edition N, and an override naming an edition this tenant has not
    published falls back to the newest — the default view never 404s."""
    override = get_settings().scoreboard_default_edition
    if override == 0:
        return None
    if override is not None:
        pinned = editions_repo.get_by_number(conn, tenant_id=tenant, number=override)
        if pinned is not None:
            return pinned
    return editions_repo.newest(conn, tenant_id=tenant)


def _resolve_edition(
    conn: psycopg.Connection, tenant: UUID, requested: int | None
) -> editions_repo.Edition | None:
    """The edition a read serves: ``None`` = Edition 0 (the record). An omitted ``edition`` reads the default
    (``_default_edition``: the newest published, never a 404); 0 is the record; an explicit N >= 1 that this
    tenant has not published is a 404."""
    if requested is None:
        return _default_edition(conn, tenant)
    if requested == 0:
        return None
    found = editions_repo.get_by_number(conn, tenant_id=tenant, number=requested)
    if found is None:
        raise HTTPException(status_code=404, detail=f"edition {requested} not found")
    return found


def _record_edition_out(first: date | None, last: date | None) -> ScoreboardEditionOut:
    return ScoreboardEditionOut(
        number=0, label=editions_repo.EDITION_0_LABEL, first_night=first, last_night=last
    )


def _edition_out(e: editions_repo.Edition) -> ScoreboardEditionOut:
    return ScoreboardEditionOut(
        number=e.number,
        label=e.label,
        causes=list(e.causes),
        cut=e.data_known_by,
        first_night=e.first_night,
        last_night=e.last_night,
        n_pages=e.n_pages,
        n_entries=e.n_entries,
        n_entries_estimated=e.n_entries_estimated,
        engine_fingerprint=e.engine_fingerprint,
        config_hash=e.config_hash,
        code_sha=e.code_sha,
        published_at=e.created_at,
    )


@router.get("/editions", response_model=ScoreboardEditionsResponse)
def get_scoreboard_editions(
    conn: psycopg.Connection = Depends(get_conn),
    current_tenant: UUID = Depends(get_current_tenant),
) -> ScoreboardEditionsResponse:
    """Every edition the Scoreboard can show: Edition 0 (the original record, the calls log — always
    present, never stored) first, then each stored re-run edition ascending, with its cut, nights, join-date
    counts and engine fingerprint. ``default`` = the edition ``GET /scoreboard`` serves when the request
    names none: the newest published edition (0 only when nothing is published), unless the server's
    override pins another. ``notice`` = the quiet line for seven days after the automatic cut published the
    newest edition (null otherwise). Read-only."""
    stored = editions_repo.list_editions(conn, tenant_id=current_tenant)
    chosen = _default_edition(conn, current_tenant)
    notice = editions_repo.cut_notice(
        conn, tenant_id=current_tenant, now=datetime.now(timezone.utc)
    )
    return ScoreboardEditionsResponse(
        default=chosen.number if chosen is not None else 0,
        editions=[
            _record_edition_out(calls_repo.record_first(conn), calls_repo.record_edge(conn)),
            *(_edition_out(e) for e in stored),
        ],
        notice=(
            EditionCutNoticeOut(
                number=notice.number,
                previous=notice.previous,
                label=notice.label,
                line=notice.line(),
                published_at=notice.published_at,
                n_company_nights_changed=notice.n_company_nights_changed,
                unexplained=notice.unexplained,
            )
            if notice is not None
            else None
        ),
    )


@router.get("/editions/diff", response_model=ScoreboardEditionDiffResponse)
def get_scoreboard_edition_diff(
    before_number: int = Query(
        ...,
        alias="from",
        ge=0,
        description="the EARLIER edition: 0 = the original record (its honest default read), N >= 1 = "
        "the stored Edition N (404 if it does not exist)",
    ),
    after_number: int = Query(
        ...,
        alias="to",
        ge=1,
        description="the LATER edition, a stored re-run (404 if it does not exist); it must be newer "
        "than `from` (422 otherwise)",
    ),
    conn: psycopg.Connection = Depends(get_conn),
    current_tenant: UUID = Depends(get_current_tenant),
) -> ScoreboardEditionDiffResponse:
    """What changed from one edition to a later one (editions S6) — computed on read, never stored, and
    read-only. Every thesis is compared on the nights BOTH editions cover, through the earlier of the two
    editions' last nights (the earlier edition's last night once the later one has reached it), so a night
    neither could disagree about is never a change. Edition 0 is read the way the default Scoreboard reads
    it: reconstructed rows never, annotated and late rows under the server's default policies.

    Per company-night: armed or not in each edition, and — on a night both arm the company — its verdict,
    entry grade, conviction grade and exit-by; a night with no page reads that edition's last page before
    it. Per episode, paired on (thesis, company, arm date): matched (listed only when the run differs),
    moved (the same company armed in both over overlapping windows), appeared, vanished — each scored
    arm -> exit-by on realized closes through the last night, with the delta on a pair. Pages only one
    edition holds are reported as spans; a run lying wholly on them is counted, never compared, while one
    that armed there and runs into the shared nights is a change (``armed_before_window``).
    Grouped thesis -> company, with headline counts. ``cause`` is filled only when the automatic cut (S7)
    published the later edition from the earlier one (``causes`` true): what its trial proved, or what the
    trial's scope implies (late data, a repair or late data, not separated); any other pair has null
    causes — a cause is never guessed."""
    if before_number >= after_number:
        raise HTTPException(
            status_code=422,
            detail=f"`from` must be an earlier edition than `to` (got {before_number} -> "
            f"{after_number})",
        )
    before = _resolve_edition(conn, current_tenant, before_number)
    after = _resolve_edition(conn, current_tenant, after_number)
    before_out = (
        _edition_out(before)
        if before is not None
        else _record_edition_out(calls_repo.record_first(conn), calls_repo.record_edge(conn))
    )
    assert after is not None  # `to` >= 1: a stored edition, or the 404 above
    after_out = _edition_out(after)
    lasts = [before_out.last_night, after_out.last_night]
    through = min(d for d in lasts if d is not None) if None not in lasts else None
    result = edition_diff(conn, before=before, after=after, through=through)
    theses_out = []
    for t in result.theses:
        sids = [c.security_id for c in t.companies]
        tenant = t.tenant_id or DEFAULT_TENANT_ID
        tickers = master.tickers_for(conn, sids, tenant_id=tenant) if sids else {}
        names = master.names_for(conn, sids, tenant_id=tenant) if sids else {}
        theses_out.append(
            EditionThesisDiffOut(
                **t.model_dump(exclude={"companies", "tenant_id"}),
                companies=[
                    EditionCompanyDiffOut(
                        **c.model_dump(),
                        ticker=tickers.get(c.security_id),
                        company_name=names.get(c.security_id),
                    )
                    for c in t.companies
                ],
            )
        )
    return ScoreboardEditionDiffResponse(
        before=before_out,
        after=after_out,
        through=result.through,
        first_night=result.first_night,
        counts=EditionDiffCountsOut(**result.counts.model_dump()),
        theses=theses_out,
        causes=result.causes,
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
    edition: int | None = Query(
        None,
        ge=0,
        description="the EDITION the drawer belongs to (the `/scoreboard` param's semantics, so "
        "omitted = the newest published edition): an edition >= 1 reads every dated overlay on the "
        "PUBLIC clock, a live as-of included; 0 (or omitted with nothing published) = the record's "
        "reading, unchanged",
    ),
    thesis: Thesis = Depends(get_thesis_or_404),
    current_tenant: UUID = Depends(get_current_tenant),
    conn: psycopg.Connection = Depends(get_conn),
) -> ScoreboardPriceWindowOut:
    """One episode's realized daily OHLCV bars over ``[start, end]`` — with SMA 50/200 context and four
    dated event families — for the Scoreboard drawer's chart (Slice 3, extended in Slice A, widened in
    Slice B). The SAME asof-capped read the scorer runs (``PgRealizedPrices``; ``bars_between`` shares
    ``closes_between``'s cap/known_at), served on demand instead of embedded in the ledger payload (which
    stays lean). The line draws ``close``; open/high/low/volume ride the wire for a later candlestick.

    Each insider buy carries its server-classified ``character`` (Band 03 S2c — deterministic field
    predicates, #3): ``open_market`` / ``self_filing`` / ``primary_market`` / ``implausible``. Set-aside
    rows (the last two) ride the wire grayed-and-labeled on the FE instead of being dropped (WB #2 / #9),
    so the event ledger shows why a buy did or didn't count toward the panel's open-market flow (#6).

    Slice B widens the overlay with three more families through the SAME window + knowability gate:
    - ``insider_sells`` — every code-S sale, labeled with the CALL side's screen bucket (wire-mapped:
      ``kept`` / ``planned`` / ``self_filing`` / ``below_low`` / ``implausible`` / ``foreign_ordinary``).
      DIAL-MIRROR CAVEAT: characters are classified with ``DEFAULT_CONFIG`` pinned, so a deployment on
      non-default insider dials could show labels that drift from the call's actual cluster screens —
      the display rail's accepted posture (labels only, never the detector's math).
    - ``corporate_events`` — EVERY stored 8-K in-window, ``items`` ``null`` when unresolved (honest,
      never dropped). NO server-side item cut — loudness is a display concern (#9).
    - ``activist_stakes`` — the 13D/G tape, both naming eras; unresolved filer identity / ``pct_owned``
      ship as ``null``, the row kept (#9). 13G rows ride — the fire policy stays in the detector.

    No-lookahead (invariant #1) is enforced SERVER-SIDE and never trusted to the client, on BOTH axes:
    - the price reader caps the valid-time axis at ``cap = asof`` (``d <= asof``), so a client passing a
      future ``end`` still gets only bars ``<= asof`` — the window can never be widened past the as-of. Its
      ``known_at`` stays the reader's default (now), matching the live scorer's construction, so the line
      matches the bars behind the ledger's numbers.
    - the insider read caps the transaction axis too (``known_at_for_asof(asof)`` = ``min(now, the end of the
      as-of's night)`` — EDGAR's next open, 06:00 ET on the next weekday),
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
    # S2 — the bars above are the stored tape on the split list's scale (read as of the reader's
    # ``known_at``, the scorer's axis) and a Form 4 price is as traded: the screens put it on the tape's
    # scale with the splits the store held at that same instant (``domain.splits.tape_scale``) — the call's
    # own rule
    price_scale = tape_scale(
        split_repo.as_of_rows(
            conn, tenant_id=tenant, security_ids=[security_id], known_at=reader.known_at
        )[security_id],
        through=view_day(reader.known_at),
    )
    # ONE knowability cap for every event family (Slice B): min(now, end of the asof's night — EDGAR's
    # next open, F6) — the two-axis
    # no-lookahead every dated overlay read shares (invariant #1).
    # ``now`` from the DATABASE clock (``db.clock``): for a PAST asof the night cap dominates and
    # the host clock never binds, but for asof == TODAY ``min(now, night end)`` IS ``now`` — and that
    # bound is compared against database-stamped ``recorded_at`` values. One clock, per db/clock.py.
    # WHICH axis it bounds (operator decision 2026-10-21): a PAST asof is a history view, judged on the
    # PUBLIC clock — an event counts from when it became public (``db.public_clock``), not from when we
    # ingested it; a live asof stays on the record clock (where the two coincide) — ``serve_view``'s rule.
    known_at = known_at_for_asof(asof, now=db_now(conn))
    _, clock = serve_view(asof)
    # Editions S4: an edition is a history view on the PUBLIC clock end to end, so its drawer reads every
    # overlay there — a live as-of included (where the two clocks coincide anyway, ``db.public_clock``).
    # The same shared ``clock="public"`` reads #433 gave a past as-of; no edition-specific clock.
    if _resolve_edition(conn, current_tenant, edition) is not None:
        clock = "public"
    buys = episode_insider_buys(
        conn,
        tenant_id=tenant,
        security_id=security_id,
        start=floor,  # events bounded to the relevance window, regardless of the requested start
        end=end,
        asof=asof,
        known_at=known_at,
        clock=clock,
        day_lows=day_lows,
        price_scale=price_scale,
    )
    sells = episode_insider_sells(
        conn,
        tenant_id=tenant,
        security_id=security_id,
        start=floor,
        end=end,
        asof=asof,
        known_at=known_at,
        clock=clock,
        day_lows=day_lows,  # the below-day-low secondary screen reads the SAME asof-capped lows
        price_scale=price_scale,
    )
    corp_events = episode_corporate_events(
        conn,
        tenant_id=tenant,
        security_id=security_id,
        start=floor,
        end=end,
        asof=asof,
        known_at=known_at,
        clock=clock,
    )
    stakes = episode_activist_stakes(
        conn,
        tenant_id=tenant,
        security_id=security_id,
        start=floor,
        end=end,
        asof=asof,
        known_at=known_at,
        clock=clock,
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
        insider_sells=[InsiderSellOut(**s) for s in sells],
        corporate_events=[CorporateEventOut(**e) for e in corp_events],
        activist_stakes=[ActivistStakeOut(**s) for s in stakes],
    )
