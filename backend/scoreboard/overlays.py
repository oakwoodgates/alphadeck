"""Pure + as-of overlay helpers for the drawer chart (Slice A, widened in Slice B): per-bar SMA
context and the window's dated event families.

The additions ride the ``price-window`` response, all under the SAME no-lookahead discipline as the
bars themselves (``docs/CALL_LOGIC.md`` / invariant #1):

- ``annotate_sma`` — a PURE rolling mean over the realized closes, computed with a warm-up read so the
  window's LEFT edge is honest (the value at ``start`` sees the ~200 prior closes) and ``None`` where too
  little history exists (never back-padded). It reads no clock and no DB — the router hands it the
  asof-capped bars ``PgRealizedPrices.bars_between`` already returns (never a forked as-of path).
- ``episode_insider_buys`` — the window's individual code-P purchases, read via the shared
  bitemporal ``as_of`` (latest version per natural key, so a corrected row never double-counts), each
  CLASSIFIED by the display rail's buy-character screen (``_screen`` — the same predicates the
  NamePanel's open-market definition composes), so the chart's dots reconcile with the panel's
  net-flow figure. Band 03 S2c (operator option (a)): the set-aside rows (primary-market /
  implausible) now RIDE the wire too, tagged by ``character``, instead of being ``continue``-d past —
  the FE greys + labels them (WB #2: pruning hides, it never vanishes; #9: more visible, never
  dropped). Only the non-set-aside subset is what the panel's open-market figure counts.
- ``episode_insider_sells`` (Slice B) — the code-S mirror: every stored sale in-window, each labeled
  with the CALL side's screen bucket (``signals.insider_sell._screen``) so the ledger shows why a sale
  did or didn't count toward the sell cluster. Labels only — this read never touches the detector's
  cluster/score math, and everything except ``kept`` renders greyed-not-hidden on the FE (WB #2).
- ``episode_corporate_events`` (Slice B) — every stored 8-K in-window (``fact_corporate_event``),
  items unresolved shipping honestly as ``None``. NO item cut here: loudness is a display concern; a
  server-side cut would be a silent filter (#9).
- ``episode_activist_stakes`` (Slice B) — every stored 13D/G-family filing in-window
  (``fact_activist_stake``), unresolved filer identity shipping as ``None`` (kept, never dropped, #9);
  13G rows ride too — the fire policy lives in the detector, the passive-grey lives in the FE.

The transaction axis is the load-bearing bit: every event is positioned by ``valid_from`` (the
transaction / filed date) and GATED by ``recorded_at <= known_at`` — we only surface what we'd have
KNOWN by ``known_at``. ``known_at_for_asof`` caps that at the request's as-of so a scrubbed-back
Scoreboard hides not just later bars but later-RECORDED events (the IBM "ingested 166d after its event
date" case), exactly the honesty a forward reader owes.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from uuid import UUID

import psycopg

from db.bitemporal import as_of
from domain.config import DEFAULT_CONFIG
from signals.display.insider_flow import BUY, SELL, _screen
from signals.insider_sell import _FOREIGN, _SELF
from signals.insider_sell import _screen as _sell_screen

# Warm-up buffer read BEHIND the window start so the leftmost returned bar carries an honest SMA200
# (needs ~200 prior TRADING days; 300 calendar days ≈ 205-215 trading days clears it). A young security
# or a short ingested history simply yields ``None`` at the left — the gap is honest, not padded.
SMA_WARMUP_DAYS = 300
SMA_WINDOWS: tuple[int, ...] = (50, 200)

# The overlay's RELEVANCE floor (Slice A R1): events older than the thesis-existed window are off-story —
# a thesis born 2026-07 does not plot a 2020 insider buy. This is NOT a recall cut (#9): the excluded
# events are genuinely PRE-THESIS. The floor is ``max(created_at − 365d, first_bar)`` — a year of run-up
# before the thesis was created, never earlier than the first bar we hold.
UNIVERSE_LOOKBACK_DAYS = 365


def _f(x: Any) -> float | None:
    """Coerce a nullable numeric column (``Decimal``/``None``) to ``float``/``None`` (the prices twin)."""
    return float(x) if x is not None else None


def thesis_created_at(conn: psycopg.Connection, thesis_id: UUID) -> date:
    """The thesis's ``created_at`` DATE (the ``thesis`` table is single-row operational, not bitemporal).
    Read directly here — ``get_thesis_or_404`` returns the domain ``Thesis`` (which carries no ``created_at``),
    so the router pulls the one column it needs for the relevance floor without widening the domain model.
    The row exists (the caller already loaded the thesis)."""
    with conn.cursor() as cur:
        cur.execute("SELECT created_at FROM thesis WHERE id = %s", [thesis_id])
        row = cur.fetchone()
    return row["created_at"].date()


def security_issuer_name(
    conn: psycopg.Connection, tenant_id: UUID, security_id: UUID
) -> str | None:
    """The security's registered ``security_master`` name — the self-filing identity screen's
    name-fallback input (Band 03 S2c). Read directly here, the ``thesis_created_at`` precedent: one
    column this module needs, no domain-model widening. A missing row / NULL name returns ``None``,
    which simply DISABLES the name fallback (CIK equality on the row still matches; missing identity
    is never "self" — the recall-safe one-directional failure mode, #9)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT name FROM security_master WHERE tenant_id = %s AND id = %s",
            [tenant_id, security_id],
        )
        row = cur.fetchone()
    return row["name"] if row else None


def universe_floor(created_at: date, first_bar: date | None) -> date:
    """The overlay event floor (R1): ``max(created_at − 365d, first_bar)``. A thesis older than its price
    history floors at the first bar (nothing to plot before we have prices); a thesis younger than its
    history floors at ``created_at − 365d`` (a year of run-up, not the whole tape). ``first_bar`` None
    (no bars yet) → the created floor stands. Pure."""
    created_floor = created_at - timedelta(days=UNIVERSE_LOOKBACK_DAYS)
    return created_floor if first_bar is None else max(created_floor, first_bar)


def annotate_sma(bars: list[dict[str, Any]], windows: tuple[int, ...] = SMA_WINDOWS) -> list[dict]:
    """Annotate each bar with trailing simple moving averages of ``close`` — PURE, no clock, no DB.

    ``bars`` must be ascending by date, each carrying a numeric ``close`` (the shape ``bars_between``
    returns). For each window ``w`` a ``sma{w}`` field is written: the mean of the trailing ``w`` closes
    INCLUDING the bar, or ``None`` where fewer than ``w`` closes precede it (the honest LEFT-edge gap —
    never back-padded, never invented). Returns NEW dicts; the input list is untouched. The router calls
    it with the default ``(50, 200)`` (→ ``sma50``/``sma200``); tests pass small windows for readability.
    """
    closes = [b["close"] for b in bars]
    out: list[dict] = []
    for i, b in enumerate(bars):
        row = dict(b)
        for w in windows:
            row[f"sma{w}"] = sum(closes[i + 1 - w : i + 1]) / w if i + 1 >= w else None
        out.append(row)
    return out


def known_at_for_asof(asof: date, now: datetime | None = None) -> datetime:
    """The transaction-axis cap for the insider read: ``min(now, end-of-asof-day)``.

    A LIVE view (``asof`` today / future) reads at ``now`` — everything disclosed by this moment. A
    scrubbed-back ``asof`` caps ``known_at`` at that day's end, so a buy DISCLOSED (``recorded_at``) after
    the as-of is absent — the two-axis no-lookahead a forward reader owes (invariant #1). Distinct from the
    price read, which stays at ``now`` (a price bar's ``valid_from == d``, so its valid-axis cap already
    carries the honesty; an insider filing lags its transaction by days-to-months, so its transaction axis
    must cap too).
    """
    now = now or datetime.now(timezone.utc)
    asof_eod = datetime.combine(asof, time.max, tzinfo=timezone.utc)
    return min(now, asof_eod)


def episode_insider_buys(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID,
    security_id: UUID,
    start: date,
    end: date,
    asof: date,
    known_at: datetime,
    day_lows: dict[date, float],
) -> list[dict[str, Any]]:
    """The window's code-P insider buys, as-of-capped on both axes, each carrying its ``character``.

    Read via the shared bitemporal ``as_of`` (``valid_from <= asof`` on the valid axis, ``recorded_at <=
    known_at`` on the transaction axis, and the LATEST version per natural key — so a corrected/superseded
    row never double-counts). Then: keep code-P only, keep transactions inside the drawn window
    ``[start, min(end, asof)]`` (``asof`` already caps the read; ``end`` bounds a matured episode whose
    exit_by is in the past), and CLASSIFY each buy via the display rail's ``_screen`` (Band 03 S2c) —
    the issuer name (``security_issuer_name``) feeds its self-filing identity check. Set-aside rows
    (``primary_market`` / ``implausible``) are KEPT on the wire, tagged, instead of dropped — the FE
    greys + labels them (option (a); WB #2 / #9) — so the panel's open-market figure equals the
    NON-set-aside subset, and the ledger now shows WHY a buy did or didn't count (#6). The input rows
    are never mutated (new dicts out, #9).

    DEFERRED (operator decision 3): a ``self_filing`` buy is labeled but NOT set aside — the panel's
    90d net-flow still counts it (``insider_flow._is_open_market_buy`` is identity-blind); re-basing
    that figure is a separate, operator-signed decision. See ``signals/display/insider_flow.py``.

    Each row carries TWO honest clocks (the MRVL two-clock fix): ``d`` = ``valid_from`` (the chip's x,
    the transaction date); ``disclosed`` = ``accepted::date`` — the real SEC acceptance date (the tooltip's
    honest disclosure lag), ``None`` when unresolved (pre-backfill / unresolvable — the FE falls back to the
    "ingested" line, #9); ``ingested`` = ``recorded_at::date`` — our ingest time, rendered as a SECOND line
    only when it differs from ``disclosed`` (on the rebuilt demo that surfaces the 326d ingest lag beside the
    ~2d disclosure). Plus who / role / shares / $ / the 10b5-1 plan flag / ``character``. Sorted by
    transaction date (then ``ingested`` — always present) — the frontend numbers chronologically.
    """
    issuer_name = security_issuer_name(conn, tenant_id, security_id)
    rows = as_of(
        conn,
        "fact_insider_txn",
        security_id=security_id,
        asof=asof,
        known_at=known_at,
        tenant_id=tenant_id,
    )
    buys: list[dict[str, Any]] = []
    for r in rows:
        if r.get("txn_code") != BUY:  # code 'P' — open-market OR private purchase
            continue
        vf: date = r["valid_from"]
        if vf < start or vf > end:  # position window (end bounds a matured, past-exit episode)
            continue
        buys.append(
            {
                "d": vf,
                "insider_name": r.get("insider_name"),
                "insider_role": r.get("insider_role"),
                "shares": _f(r.get("shares")),
                "usd": _f(r.get("usd")),
                "aff_10b5_1": r.get("aff_10b5_1"),
                # two honest clocks: disclosed = the real SEC acceptance date (accepted), None when
                # unresolved (#9 fallback -> the FE's "ingested" line); ingested = our recorded_at
                "disclosed": r["accepted"].date() if r.get("accepted") else None,
                "ingested": r["recorded_at"].date(),
                # the buy's server-classified character (deterministic field predicates, #3);
                # set-asides ride greyed-and-labeled instead of hidden (option (a) — WB #2)
                "character": _screen(r, day_lows, issuer_name),
            }
        )
    # sort by transaction date, tiebreak on ingested (always present; disclosed can be NULL)
    buys.sort(key=lambda b: (b["d"], b["ingested"]))
    return buys


# The sell-character WIRE values (``schemas_api.InsiderSellOut.character``). The screen's internal
# buckets ship verbatim except the two whose short names would be cryptic on a public contract —
# mapped to the buy side's existing vocabulary (``self_filing``) and the S2c term
# (``foreign_ordinary``). The drift-pin test (``tests/scoreboard/test_overlays.py``) proves every
# ``_screen`` bucket lands inside the schema's Literal, so a future new bucket fails THERE loudly,
# never as a runtime response-validation 500.
_SELL_CHARACTER_WIRE: dict[str, str] = {_SELF: "self_filing", _FOREIGN: "foreign_ordinary"}


def sell_character_wire(bucket: str) -> str:
    """The wire value for a sell-screen bucket (identity except ``self``/``foreign`` — see the map)."""
    return _SELL_CHARACTER_WIRE.get(bucket, bucket)


def episode_insider_sells(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID,
    security_id: UUID,
    start: date,
    end: date,
    asof: date,
    known_at: datetime,
    day_lows: dict[date, float],
) -> list[dict[str, Any]]:
    """The window's code-S insider sales, as-of-capped on both axes, each carrying its ``character``
    (Slice B — the sell mirror of ``episode_insider_buys``).

    Same shared bitemporal ``as_of`` read (``valid_from <= asof`` + ``recorded_at <= known_at`` + the
    latest version per natural key), same window filter ``[start, min(end, asof)]``, same two honest
    clocks (``disclosed`` = ``accepted::date`` or ``None``; ``ingested`` = ``recorded_at::date``), same
    sort. Each sale is bucketed by the CALL side's screen (``signals.insider_sell._screen`` — the same
    predicates the risk detector's cluster composes: implausible $, foreign/ordinary mis-filing,
    issuer self-filing, below-day-low secondary, 10b5-1 planned), the bucket translated to the wire
    vocabulary via ``sell_character_wire``. EVERY row rides — a screened sale is greyed + labeled on
    the FE, never hidden (WB #2 / #9); only ``kept`` rows are what the detector's cluster counts.

    DIAL-MIRROR CAVEAT: the screen runs with ``DEFAULT_CONFIG`` pinned (this display read has no
    ``CallConfig`` plumbing), so a deployment running non-default insider dials could show ledger
    labels that drift from the call's actual cluster screens — the display rail's accepted posture
    (the same mirror-the-dials stance as ``signals/display/insider_flow``'s module constants). Labels
    only: this read never touches the ``insider_sell`` detector's cluster/score math.
    """
    issuer_name = security_issuer_name(conn, tenant_id, security_id)
    rows = as_of(
        conn,
        "fact_insider_txn",
        security_id=security_id,
        asof=asof,
        known_at=known_at,
        tenant_id=tenant_id,
    )
    sells: list[dict[str, Any]] = []
    for r in rows:
        if r.get("txn_code") != SELL:  # code 'S' only — the buy overlay owns code P
            continue
        vf: date = r["valid_from"]
        if vf < start or vf > end:  # position window (end bounds a matured, past-exit episode)
            continue
        sells.append(
            {
                "d": vf,
                "insider_name": r.get("insider_name"),
                "insider_role": r.get("insider_role"),
                "shares": _f(r.get("shares")),
                "usd": _f(r.get("usd")),
                "aff_10b5_1": r.get("aff_10b5_1"),
                "disclosed": r["accepted"].date() if r.get("accepted") else None,
                "ingested": r["recorded_at"].date(),
                # the CALL-side screen bucket (deterministic field predicates, #3), wire-mapped;
                # screened rows ride greyed-and-labeled instead of hidden (WB #2)
                "character": sell_character_wire(
                    _sell_screen(r, day_lows, issuer_name, DEFAULT_CONFIG)
                ),
            }
        )
    sells.sort(key=lambda s: (s["d"], s["ingested"]))
    return sells


def episode_corporate_events(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID,
    security_id: UUID,
    start: date,
    end: date,
    asof: date,
    known_at: datetime,
) -> list[dict[str, Any]]:
    """The window's stored 8-K filings (Slice B) — EVERY one, as-of-capped on both axes, no item cut.

    Read via the shared bitemporal ``as_of`` over ``fact_corporate_event`` (migration 0038): the
    latest version per accession, so a filing whose ``items`` resolved after first ingest reads with
    its real codes — and a ``known_at`` pinned BEFORE the resolve honestly reads ``items=None`` (the
    not-yet-resolved past view). ``d`` = ``valid_from`` = ``filed`` — an 8-K is knowable exactly when
    EDGAR disseminates it, so the one event clock plus ``ingested`` (``recorded_at::date``) suffices.
    ``url`` is the row's ``source_ref`` (the EDGAR filing-index URL, #6). NO server-side item cut:
    loudness is a display concern, and a cut here would be a silent filter (#9) — the FE decides how
    quietly the common filing renders.
    """
    rows = as_of(
        conn,
        "fact_corporate_event",
        security_id=security_id,
        asof=asof,
        known_at=known_at,
        tenant_id=tenant_id,
    )
    events: list[dict[str, Any]] = []
    for r in rows:
        vf: date = r["valid_from"]
        if vf < start or vf > end:
            continue
        items = r.get("items")
        events.append(
            {
                "d": vf,
                "form": r["form"],
                # None = not-yet-resolved — shipped as null, rendered honestly, never dropped (#9)
                "items": list(items) if items is not None else None,
                "url": r["source_ref"],
                "ingested": r["recorded_at"].date(),
            }
        )
    events.sort(key=lambda e: (e["d"], e["ingested"]))
    return events


def episode_activist_stakes(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID,
    security_id: UUID,
    start: date,
    end: date,
    asof: date,
    known_at: datetime,
) -> list[dict[str, Any]]:
    """The window's stored 13D/G-family filings (Slice B) — both naming eras, as-of-capped on both axes.

    Read via the shared bitemporal ``as_of`` over ``fact_activist_stake`` (migration 0039): the latest
    version per accession, so a filing whose filer identity / ``pct_owned`` resolved after first
    ingest reads enriched. ``d`` = ``valid_from`` = ``filed`` (knowability — never the in-document
    event date, gold-doc trap #4). Unresolved identity ships as ``None`` — ``filer_name`` /
    ``filer_cik`` / ``pct_owned`` null, the row KEPT (#9, never dropped). NO fire-policy filtering:
    13G rows and amendments ride too — the fire policy (13D-family originals only) lives in the
    detector; the FE mirrors it as display weight (13G-family greyed-passive), never as an omission.
    ``url`` is the row's ``source_ref`` (the EDGAR filing-index URL, #6).
    """
    rows = as_of(
        conn,
        "fact_activist_stake",
        security_id=security_id,
        asof=asof,
        known_at=known_at,
        tenant_id=tenant_id,
    )
    stakes: list[dict[str, Any]] = []
    for r in rows:
        vf: date = r["valid_from"]
        if vf < start or vf > end:
            continue
        stakes.append(
            {
                "d": vf,
                "form": r["form"],
                "filer_name": r.get("filer_name"),  # None = identity unresolved — kept (#9)
                "filer_cik": r.get("filer_cik"),
                "pct_owned": _f(r.get("pct_owned")),  # structured-era only; None = unparsed
                "url": r["source_ref"],
                "ingested": r["recorded_at"].date(),
            }
        )
    stakes.sort(key=lambda s: (s["d"], s["ingested"]))
    return stakes
