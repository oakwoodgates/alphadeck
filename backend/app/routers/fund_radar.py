"""The fund launch radar's display API (docs/FUND_RADAR.md §"What you see"): the tape of fund filings, one card's
matching launches as of a night, and the reversible add-to-ETFs pair.

DISPLAY ONLY (Kind B): every GET reads the stored facts (``fact_fund_*``) and derives stages and card matches on
read — no SEC request, no LLM (#3), and no ``calls/`` import (the radar is structurally outside the call path; a
fund also sits in the silent ETFs link, so it never votes). The attach / detach pair is the operator's click
(#10: the radar recommends, the operator decides), reversible (#1), and goes through the SAME resolve and the
SAME promote validation the Workbench uses, so a fund can never land outside the ETFs link.
"""

from __future__ import annotations

import threading
from datetime import date, timedelta
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query

from app.deps import get_conn, get_current_tenant, get_thesis_or_404
from app.routers.workbench import _fund_ids, resolve_etf
from app.schemas_api import (
    FundAttachOut,
    FundAttachRequest,
    FundFilingOut,
    FundLaunchCountsOut,
    FundLaunchesOut,
    FundMatchOut,
    FundNameHitOut,
    FundOut,
    FundStageOut,
    FundTapeCountsOut,
    RadarFundsOut,
    ResolveEtfRequest,
)
from domain.enums import Authorship
from domain.market_time import market_today
from domain.thesis import (
    ETFS_LABEL,
    BasketMember,
    Thesis,
    fund_conflicts,
    silent_conflicts,
    with_silent_links,
)
from radar import fund_match, fund_repo, fund_stage
from radar.fund_cards import card_inputs
from repositories import thesis_repo
from securities import master

router = APIRouter(tags=["radar"])


def _stage_out(st: fund_stage.FundStage) -> FundStageOut:
    return FundStageOut(
        filed=st.filed,
        effective=st.effective,
        ticker=st.ticker,
        listed=st.listed,
        current=st.current,  # type: ignore[arg-type]
    )


def _match_out(card: fund_match.CardInput, cm: fund_match.CardMatch) -> FundMatchOut:
    th = cm.theme
    return FundMatchOut(
        thesis_id=UUID(card.thesis_id),
        thesis_name=card.name,
        tier=th.tier if th is not None else None,  # type: ignore[arg-type]
        signal_terms=list(th.signal) if th is not None else [],
        broad_terms=list(th.broad) if th is not None else [],
        name_hits=[
            FundNameHitOut(ticker=h.ticker, link=h.link, token=h.token, via=h.via)  # type: ignore[arg-type]
            for h in cm.names
        ],
    )


def _match_rank(m: FundMatchOut) -> tuple[int, str]:
    """A signal-tier theme match first, then broad, then name-only; ties by card name."""
    return ({"signal": 0, "broad": 1}.get(m.tier or "", 2), m.thesis_name)


def _filings(
    launches: list[fund_stage.FundLaunch],
    matches: dict[str, list[FundMatchOut]],
    *,
    in_etfs: set[str] | None = None,
    only_matched_funds: bool = False,
    activity: dict[str, date] | None = None,
) -> list[FundFilingOut]:
    """Group launches by the filing that first announced them (newest first). ``only_matched_funds`` keeps just
    the funds with a match (the per-card route). ``activity`` (fund key -> its newest in-window stage date) orders
    the filings by their newest activity instead of their filing date (the per-card route)."""
    by_acc: dict[str, list[fund_stage.FundLaunch]] = {}
    for lc in launches:
        by_acc.setdefault(lc.accession, []).append(lc)
    out: list[FundFilingOut] = []
    for acc, lcs in by_acc.items():
        funds = [
            FundOut(
                key=lc.key,
                series_id=lc.series_id,
                name=lc.name,
                stage=_stage_out(lc.stage),
                symbols=list(lc.symbols),
                matches=sorted(matches.get(lc.key, []), key=_match_rank),
                in_etfs=bool(in_etfs and any(s in in_etfs for s in lc.symbols)),
            )
            for lc in lcs
            if not only_matched_funds or matches.get(lc.key)
        ]
        if not funds:
            continue
        first = lcs[0]
        out.append(
            FundFilingOut(
                accession=acc,
                form=first.form,
                trust=first.trust,
                public_date=first.public_date,
                url=first.source_ref,
                private_to_public=first.private_to_public,
                funds=funds,
            )
        )
    if activity is None:
        out.sort(key=lambda f: (f.public_date, f.accession), reverse=True)
        return out
    newest = {f.accession: max(activity.get(x.key, f.public_date) for x in f.funds) for f in out}
    out.sort(key=lambda f: (newest[f.accession], f.accession), reverse=True)
    return out


# --- the in-process cache -----------------------------------------------------------------------------
# MEASURED on dev (24-month backfill, 65 live cards): reading every card against ~1,000 funds in the window took
# ~60 s before the matcher's prefilters and ~1-3 s after; loading the fund rows is its own cost. So two small
# caches, both cleared by "something new happened", never by a clock: the loaded fund facts (keyed on the three
# fund tables' newest `recorded_at`) and the computed tape (keyed on those plus the cards' change marker: how
# many cards, the newest `updated_at`, the newest roster snapshot). A new market day changes the window, so it
# rides in the tape key. Single worker app (`assert_single_worker`), so in-process is the whole cache.

_CACHE_LOCK = threading.Lock()
_FACTS: dict[str, object] = {}
_TAPES: dict[tuple, tuple[object, tuple[list[FundFilingOut], FundTapeCountsOut]]] = {}
_MAX_TAPES = 16


def clear_cache() -> None:
    with _CACHE_LOCK:
        _FACTS.clear()
        _TAPES.clear()


def _facts_token(conn: psycopg.Connection) -> tuple:
    with conn.cursor() as cur:
        cur.execute("""SELECT (SELECT max(recorded_at) FROM fact_fund_filing) AS a,
                      (SELECT max(recorded_at) FROM fact_fund_series) AS b,
                      (SELECT max(recorded_at) FROM fact_fund_symbol) AS c""")
        r = cur.fetchone()
    return (r["a"], r["b"], r["c"])


def _cards_token(conn: psycopg.Connection) -> tuple:
    with conn.cursor() as cur:
        cur.execute("""SELECT (SELECT count(*) FROM thesis) AS n,
                      (SELECT max(updated_at) FROM thesis) AS u,
                      (SELECT max(taken_at) FROM basket_snapshot) AS s""")
        r = cur.fetchone()
    return (r["n"], r["u"], r["s"])


def _facts(
    conn: psycopg.Connection,
) -> tuple[list[fund_repo.SeriesView], list[fund_repo.SymbolRow]]:
    """The stored fund facts (launch series only), from the cache while the fund tables are unchanged."""
    token = _facts_token(conn)
    with _CACHE_LOCK:
        if _FACTS.get("token") == token:
            return _FACTS["views"], _FACTS["symbols"]  # type: ignore[return-value]
    views = fund_repo.series_views(conn, launches_only=True)
    symbols = fund_repo.symbol_rows(conn)
    with _CACHE_LOCK:
        _FACTS.update(token=token, views=views, symbols=symbols)
    return views, symbols


def _tape(
    conn: psycopg.Connection, days: int, include_private: bool
) -> tuple[list[FundFilingOut], FundTapeCountsOut]:
    """The whole-window tape (every filing, matched or not) and its counts; the route trims to matched_only."""
    today = market_today()
    key = (today, days, include_private)
    token = (_facts_token(conn), _cards_token(conn))
    with _CACHE_LOCK:
        hit = _TAPES.get(key)
        if hit is not None and hit[0] == token:
            return hit[1]
    cutoff = today - timedelta(days=days)
    views, symbols = _facts(conn)
    launches = fund_stage.derive_launches(views, symbols)
    window = [lc for lc in launches if lc.public_date >= cutoff]
    held = [lc for lc in window if lc.private_to_public]
    shown = window if include_private else [lc for lc in window if not lc.private_to_public]

    matches: dict[str, list[FundMatchOut]] = {}
    for card in card_inputs(conn, thesis_repo.list_all(conn)):
        for cm in fund_match.match_launches(shown, card):
            matches.setdefault(cm.launch.key, []).append(_match_out(card, cm))

    filings = _filings(shown, matches)
    matched_funds = sum(1 for lc in shown if matches.get(lc.key))
    counts = FundTapeCountsOut(
        filings=len(filings),
        funds=len(shown),
        matched_funds=matched_funds,
        unmatched_funds=len(shown) - matched_funds,
        private_held_back_funds=0 if include_private else len(held),
        private_held_back_filings=0 if include_private else len({lc.accession for lc in held}),
    )
    with _CACHE_LOCK:
        if len(_TAPES) >= _MAX_TAPES:
            _TAPES.pop(next(iter(_TAPES)))
        _TAPES[key] = (token, (filings, counts))
    return filings, counts


@router.get("/radar/funds", response_model=RadarFundsOut)
def fund_tape(
    days: int = Query(90, ge=1, le=730, description="window: public within the last N days"),
    include_private: bool = Query(
        False, description="also show old confidential filings SEC released (held back by default)"
    ),
    matched_only: bool = Query(
        True, description="keep only filings with a fund that fits a live card (counts stay whole)"
    ),
    conn: psycopg.Connection = Depends(get_conn),
) -> RadarFundsOut:
    """The fund launch tape: one row per FILING, newest first by public date, each with its funds (stage dates,
    symbols) and every live card each fund fits (theme read with its tier, name read with its token). The counts
    always cover the WHOLE window, so the UI can say "N more" (keep-it-visible); ``matched_only`` only trims the
    rows. Old confidential filings SEC released are held back (counted) unless ``include_private``. Display
    only: reads stored facts, derives on read, makes no request."""
    filings, counts = _tape(conn, days, include_private)
    if matched_only:
        filings = [f for f in filings if any(fund.matches for fund in f.funds)]
    return RadarFundsOut(
        filings=filings,
        counts=counts,
        window_days=days,
        include_private=include_private,
        matched_only=matched_only,
    )


def _etfs_symbols(conn: psycopg.Connection, thesis: Thesis) -> set[str]:
    """The upper-cased symbols of the card's ETFs-link rows (by the row's ticker and by its master ticker)."""
    rows = [m for m in thesis.basket if m.segment == ETFS_LABEL]
    syms = {m.ticker.strip().upper() for m in rows if m.ticker}
    sids = [m.security_id for m in rows if m.security_id]
    for t in master.tickers_for(conn, sids).values():
        if t:
            syms.add(t.strip().upper())
    return syms


@router.get("/theses/{thesis_id}/fund-launches", response_model=FundLaunchesOut)
def fund_launches(
    asof: date | None = Query(None, description="the night to read as of (default: today)"),
    days: int = Query(90, ge=1, le=730),
    thesis: Thesis = Depends(get_thesis_or_404),
    conn: psycopg.Connection = Depends(get_conn),
) -> FundLaunchesOut:
    """This card's matching fund launches as of a night (invariant #1, the public clock). A fund is in the window
    ``asof - days``..``asof`` when ANY of its stage dates (filed, effective, ticker, listed) falls inside it, each
    derived ONLY from facts public by ``asof`` (a later 485BPOS, or a symbol first seen after ``asof``, never
    shows). A ticker or listing counts as activity only when it is STRICTLY LATER than the night the fund's new-fund
    row was first stored (we never saw a symbol arrive for a fund we had only just learned about; recorded_at is used
    only to judge an observed arrival, never as a history gate). Filings are ordered by their newest in-window activity. Counted
    by FILING and by SPONSOR (not by fund), split by tier, plus name hits. ``in_etfs`` reads the card's basket as
    it stands now.
    Old confidential filings SEC released are left out. Display only: it never feeds the call."""
    return read_fund_launches(conn, thesis, asof or market_today(), days)


def read_fund_launches(
    conn: psycopg.Connection, thesis: Thesis, asof: date, days: int = 90
) -> FundLaunchesOut:
    """The pure reader behind ``GET /theses/{id}/fund-launches``. See the route for the rules."""
    views, symbols = fund_stage.visible_as_of(*_facts(conn), asof)
    cutoff = asof - timedelta(days=days)
    starts = fund_stage.symbol_starts(symbols)
    # a fund is in the window when ANY of its stage dates falls inside it (it gets its ticker months after its
    # filing); a ticker or listing is activity only if strictly later than the night the new-fund row was first stored
    activity: dict[str, date] = {}
    window: list[fund_stage.FundLaunch] = []
    for lc in fund_stage.derive_launches(views, symbols):
        if lc.private_to_public:
            continue
        inside = [
            d for d in fund_stage.activity_dates(lc.stage, lc.first_stored) if cutoff <= d <= asof
        ]
        if inside:
            activity[lc.key] = max(inside)
            window.append(lc)
    card = card_inputs(conn, [thesis])[0]
    matches: dict[str, list[FundMatchOut]] = {}
    for cm in fund_match.match_launches(window, card):
        matches.setdefault(cm.launch.key, []).append(_match_out(card, cm))

    filings = _filings(
        window,
        matches,
        in_etfs=_etfs_symbols(conn, thesis),
        only_matched_funds=True,
        activity=activity,
    )

    def best_tier(f: FundFilingOut) -> str | None:
        tiers = {m.tier for fund in f.funds for m in fund.matches}
        return "signal" if "signal" in tiers else ("broad" if "broad" in tiers else None)

    counts = FundLaunchCountsOut(
        filings=len(filings),
        sponsors=len({f.trust for f in filings}),
        funds=sum(len(f.funds) for f in filings),
        signal_filings=sum(1 for f in filings if best_tier(f) == "signal"),
        broad_filings=sum(1 for f in filings if best_tier(f) == "broad"),
        name_filings=sum(
            1 for f in filings if any(m.name_hits for fund in f.funds for m in fund.matches)
        ),
    )
    return FundLaunchesOut(
        thesis_id=thesis.id,
        asof=asof,
        window_days=days,
        counts=counts,
        filings=filings,
        symbol_history_since=min(starts.values()) if starts else None,
    )


def _symbol(req: FundAttachRequest) -> str:
    sym = req.symbol.strip().upper()
    if not sym:
        raise HTTPException(status_code=422, detail="a fund symbol is required")
    return sym


@router.post("/radar/funds/attach", response_model=FundAttachOut)
def fund_attach(
    req: FundAttachRequest,
    conn: psycopg.Connection = Depends(get_conn),
    tenant_id: UUID = Depends(get_current_tenant),
) -> FundAttachOut:
    """Add a fund to a card's ETFs link — the operator's click (#10). The symbol resolves through the SAME
    function as ``POST /workbench/securities/resolve-etf`` (lookup-or-create, ``instrument_kind='etf'``); the
    member is built like the Workbench's "Surface an ETF" does (role "ETF sleeve", segment ETFs, a model draft,
    signed off, no conviction) and the card is re-written whole from the stored copy, so nothing it carries
    (context roles, catalysts, kill criteria...) is wiped. The write goes through the promote's own rules
    (``silent_conflicts`` / ``fund_conflicts``), so a fund can never land outside ETFs. Idempotent: already in
    ETFs returns ``already`` and writes nothing. Prices are NOT pulled here (the screen does, as Surface an ETF
    does). Reversible via detach (#1)."""
    thesis = thesis_repo.get(conn, req.thesis_id)
    if thesis is None:
        raise HTTPException(status_code=404, detail="thesis not found")
    sec = resolve_etf(ResolveEtfRequest(ticker=_symbol(req)), conn, tenant_id)
    if any(m.security_id == sec.security_id and m.segment == ETFS_LABEL for m in thesis.basket):
        return FundAttachOut(
            thesis_id=thesis.id, security_id=sec.security_id, ticker=sec.ticker, already=True
        )
    member = BasketMember(
        ticker=sec.ticker,
        role="ETF sleeve",
        security_id=sec.security_id,
        segment=ETFS_LABEL,
        conviction=None,
        authored_by=Authorship.SYSTEM_DRAFTED,
        signed_off=True,
    )
    updated = thesis.model_copy(
        update={
            "basket": [*thesis.basket, member],
            "segments": with_silent_links(thesis.segments),
        }
    )
    problems = [
        *silent_conflicts(updated.basket),
        *fund_conflicts(updated.basket, _fund_ids(conn, updated, tenant_id)),
    ]
    if problems:
        raise HTTPException(
            status_code=422,
            detail="; ".join(problems)
            + f" — {ETFS_LABEL} holds funds only, and every fund goes there.",
        )
    thesis_repo.upsert(conn, updated)
    conn.commit()
    return FundAttachOut(
        thesis_id=thesis.id, security_id=sec.security_id, ticker=sec.ticker, added=True
    )


@router.post("/radar/funds/detach", response_model=FundAttachOut)
def fund_detach(
    req: FundAttachRequest,
    conn: psycopg.Connection = Depends(get_conn),
    tenant_id: UUID = Depends(get_current_tenant),
) -> FundAttachOut:
    """The attach inverse (#1): remove ONLY that fund's row from the card's ETFs link, returning to the prior
    state (prices and facts stay; a re-attach re-binds the same id). Not there is the idempotent no-op
    (``removed=False``)."""
    thesis = thesis_repo.get(conn, req.thesis_id)
    if thesis is None:
        raise HTTPException(status_code=404, detail="thesis not found")
    sym = _symbol(req)
    sid = master.ids_for_tickers(conn, [sym], tenant_id=tenant_id).get(sym)
    if sid is None:
        return FundAttachOut(thesis_id=thesis.id, ticker=sym, removed=False)
    kept = [m for m in thesis.basket if not (m.security_id == sid and m.segment == ETFS_LABEL)]
    if len(kept) == len(thesis.basket):
        return FundAttachOut(thesis_id=thesis.id, security_id=sid, ticker=sym, removed=False)
    thesis_repo.upsert(conn, thesis.model_copy(update={"basket": kept}))
    conn.commit()
    return FundAttachOut(thesis_id=thesis.id, security_id=sid, ticker=sym, removed=True)


__all__ = ["router"]
