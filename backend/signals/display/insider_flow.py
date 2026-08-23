"""Insider net flow (trailing 90d, open-market only) — the ambient context beside the trigger.

The insider *detector* fires on cluster patterns; this member just states the tape of ingested
Form 4s: open-market buys vs sells (codes P/S only — awards, tax, and option codes are noise here),
counts, distinct buyers, dollar totals, and the dates of the last buy/sell. Because the block CLAIMS
"open-market", code-P buys are screened against the security's own EOD low the SAME way the call is
(``backend/signals/insider_conviction.py``): an offer-price IPO/PIPE/placement subscription files as
code P yet transacts below the public tape, so it drops out of the buy total — and the set-aside
subscription $ is NAMED in the basis, never silently dropped (recall-safe, #9). See
``_is_open_market_buy`` and ``docs/CALL_LOGIC.md`` §3. Zero rows in the window is honestly zero
*ingested* filings, never a proof of no filings — the basis says so.

It also emits a **30-day sub-window** (``buy_count_30d`` / ``distinct_buyers_30d``): the subset of
the ALREADY-screened open-market buys whose ``valid_from`` falls inside the tighter trailing window —
a pure filter on the same rows (no re-screen, no lookahead). The Cockpit basket surfaces both as the
**Ins 30d / Ins 90d** columns (``{open-market buys}/{distinct buyers}`` per window).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from uuid import UUID

from signals.display.base import (
    DisplayBasis,
    DisplayEvent,
    DisplayHeadline,
    DisplayMember,
    DisplayMetric,
    DisplayPointInTimeData,
    DisplaySignal,
)
from signals.display.registry import register_display_member

MEMBER_NAME = "insider_flow_90d"
LABEL = "Insider flow (90d, open-market)"
WINDOW_DAYS = 90  # trailing window: valid_from in (asof-90, asof] — 90 days ending at asof
# the tighter sub-window (Ins 30d): valid_from in (asof-30, asof], a pure slice of the SAME screened
# 90d buys — no re-screen, no lookahead (the 90d rows are already ``<= asof``)
WINDOW_DAYS_SHORT = 30

BUY, SELL = "P", "S"

# --- what counts as an OPEN-MARKET code-P buy (mirrors the call's screen, re-implemented locally) ---
# These are DISPLAY module constants, deliberately NOT ``CallConfig``: the display seam is structurally
# forbidden from importing the call's dial set (``signals/display/base.py`` + the pin in
# ``tests/signals/display/test_registry.py``). They INTENTIONALLY mirror the call's
# ``insider_offmarket_below_low_frac`` (0.10) and ``insider_max_plausible_txn_usd`` (2e9); they are not
# wired to it, so if the call recalibrates, re-tune these by hand to keep the panel and the card agreeing.
# this fraction below the day's own EOD low, a code-P buy is an offer-price subscription, not open-market
OFFMARKET_BELOW_LOW_FRAC = 0.10
# a "purchase" above this $ is bad source data (a $100k/share price → a $2T row), never an open-market buy
MAX_PLAUSIBLE_TXN_USD = 2_000_000_000.0

# --- buy CHARACTER attribution (Band 03 S2c) — the display-side labels behind the Scoreboard's per-buy
# chips / event ledger. Each code-P row lands in exactly ONE character (``_screen`` below, deterministic
# field predicates only, #3). These are the wire values (``schemas_api.InsiderBuyOut.character``).
OPEN_MARKET = "open_market"  # passed the available screens — NEVER "proven discretionary"
SELF_FILING = (
    "self_filing"  # the issuer filing a Form 4 on ITSELF — a buyback/treasury/ADR mechanic
)
PRIMARY_MARKET = "primary_market"  # an offer-price subscription (IPO/PIPE/placement) — set aside
IMPLAUSIBLE = "implausible"  # physically-impossible $ — bad source data — set aside

# The COMPLETE set of characters ``_screen`` can return — the single source of truth for the buy
# character vocabulary, co-located with the constants so a dev ADDING a character updates it right HERE
# (beside the constant and the ``return`` branch). UNLIKE the sell side these ARE the wire values
# verbatim (no translation map — ``episode_insider_buys`` ships ``_screen``'s output as-is), so the
# drift-pin test (``tests/signals/display/test_insider_flow.py``) asserts this set equals
# ``InsiderBuyOut.character``'s Literal DIRECTLY — a new character added without its Literal value (or an
# orphan Literal value) fails THAT test loudly, never as a runtime response-validation 500 on
# ``/scoreboard/price-window``.
BUY_SCREEN_CHARACTERS: frozenset[str] = frozenset(
    {OPEN_MARKET, SELF_FILING, PRIMARY_MARKET, IMPLAUSIBLE}
)


def _usd_sum(rows: list[dict[str, Any]]) -> tuple[float, int]:
    """Sum the priced rows; the count of rows WITHOUT a $ value rides back for the honest note."""
    total = sum(float(r["usd"]) for r in rows if r.get("usd") is not None)
    unpriced = sum(1 for r in rows if r.get("usd") is None)
    return total, unpriced


def _usd_compact(v: float) -> str:
    """$3.4M-style magnitude for the headline prose (the direction word carries the sign)."""
    v = abs(v)
    for div, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if v >= div:
            s = f"{v / div:.1f}".rstrip("0").rstrip(".")
            return f"${s}{suffix}"
    return f"${v:.0f}"


def _is_implausible(txn: dict[str, Any]) -> bool:
    """Physically-impossible $ (bad source data — a $100k/share price → a $2T row), never a real buy (#3)."""
    return float(txn.get("usd") or 0.0) > MAX_PLAUSIBLE_TXN_USD


def _is_below_day_low(txn: dict[str, Any], day_lows: dict[date, float]) -> bool:
    """Priced materially below the security's own EOD low that day → an offer-price primary-market
    subscription (an IPO allocation / PIPE / placement files as code P at the OFFER price — PBLS: RA
    Capital's $394M subscription at $20 vs a $29.65-$34.47 tape). Recall-safe (#9): no day low / no
    price → False (KEEP — we cannot prove it was off-market); a genuine open-market print is within the
    day's [low, high], so this cannot exclude one (save a name that REVERSE-split between the buy and
    asof, a documented limitation shared with the call)."""
    price = txn.get("price")
    low = day_lows.get(txn.get("valid_from"))
    return (
        price is not None
        and low is not None
        and float(price) < low * (1.0 - OFFMARKET_BELOW_LOW_FRAC)
    )


def _norm_entity(name: str | None) -> str:
    # duplicated from insider_conviction._norm_entity (the display seam cannot import the call path —
    # ``base.py`` + the ``test_registry.py`` import pin; re-sync by hand if the call side changes):
    # casefold + collapse whitespace + drop a trailing period; deliberately NO corporate-suffix
    # stripping (a self-filing has the SAME name on both sides; stripping would widen the match and
    # risk excluding a real, non-self buy — recall-safe, #9).
    if not name:
        return ""
    return " ".join(name.strip().casefold().rstrip(".").split())


def _is_issuer_self(txn: dict[str, Any], issuer_name: str | None) -> bool:
    # duplicated from insider_conviction._is_issuer_self (see _norm_entity above — the seam's
    # duplicate-with-pointer pattern): the ISSUER filing a Form 4 on ITSELF (KYOCERA-on-KYOCERA $690M,
    # Roivant-on-Roivant $350M — a buyback/treasury/ADR mechanic priced AT the market, so the price
    # screen keeps it). CIK equality is canonical (migration 0024); the name fallback covers rows
    # ingested before the capture. A missing CIK / name mismatch KEEPS the row (the one-directional
    # failure mode — missing identity is never "self", #9).
    oc, ic = txn.get("rpt_owner_cik"), txn.get("issuer_cik")
    if oc and ic and str(oc).strip().lstrip("0") == str(ic).strip().lstrip("0"):
        return True
    filer = _norm_entity(txn.get("insider_name"))
    issuer = _norm_entity(txn.get("issuer_name") or issuer_name)
    return bool(filer) and filer == issuer


def _screen(txn: dict[str, Any], day_lows: dict[date, float], issuer_name: str | None) -> str:
    """Which CHARACTER does this code-P buy carry? Ordered most-structural first (implausible data,
    then identity, then price — mirroring ``insider_sell._screen``) so a row tripping several screens
    has ONE deterministic attribution. Pure field predicates only (#3); the row is never mutated.

    ``aff_10b5_1`` is deliberately NOT read here: a 10b5-1 planned buy is still an open-market buy —
    the tri-state plan flag rides BESIDE the character on the wire (and renders only on an explicit
    ``True``; ``None``/``False`` are never asserted "planned" or "discretionary", #9). ``OPEN_MARKET``
    means "passed the available screens", never "proven discretionary" — a buy with no day-low context
    stays ``OPEN_MARKET`` (recall-safe: absent context only disables that one screen).
    """
    if _is_implausible(txn):
        return IMPLAUSIBLE
    if _is_issuer_self(txn, issuer_name):
        return SELF_FILING
    if _is_below_day_low(txn, day_lows):
        return PRIMARY_MARKET
    return OPEN_MARKET


def _is_open_market_buy(txn: dict[str, Any], day_lows: dict[date, float]) -> bool:
    """Does this code-P buy belong in the OPEN-MARKET total? (mirrors the call's screen, re-implemented
    locally — the display seam can't import ``signals.insider_conviction`` / ``CallConfig``; see ``base.py``).

    A thin composition of the SAME predicates ``_screen`` orders (one source of truth each), matching
    ``backend/signals/insider_conviction.py`` / ``docs/CALL_LOGIC.md`` §3: below the day's low
    (``_is_below_day_low``) → an offer-price subscription; above the $ ceiling (``_is_implausible``) →
    bad source data. **Deliberately identity-blind** — the panel's 90d net-flow counts a self-filing as
    a buy today (the tape and the call disagree on that class; the re-base is DEFERRED — operator
    decision 3, Band 03 S2c), so this predicate must NOT consult ``_is_issuer_self``. The one corner
    where the two therefore differ: a self-filing priced below the day's low reads character
    ``SELF_FILING`` (identity is the more explanatory label) but stays OUT of this net-flow total
    (below-low) — the same deferred tape-vs-call disagreement, in the other direction.

    Recall-safe (#9): no day low → KEEP (never silently drop). Set-aside rows stay in
    ``fact_insider_txn``; only this open-market READING skips them (and the basis note says how much).
    """
    return not _is_implausible(txn) and not _is_below_day_low(txn, day_lows)


def _basis_note(offmarket: list[dict[str, Any]]) -> str:
    """The epistemics that ride every payload — plus, when the screen fired, WHAT it set aside (#6 show
    the work; #9 named, never silently dropped)."""
    note = "from ingested Form 4 only — zero is zero ingested, not proven-zero filings"
    if offmarket:
        set_aside, _ = _usd_sum(offmarket)
        n = len(offmarket)
        note += (
            f"; screened {n} off-market code-P {'buy' if n == 1 else 'buys'} "
            f"(~{_usd_compact(set_aside)}) — offer-price subscriptions / implausible rows, not open-market"
        )
    return note


def _flow_headline(
    buys: list[dict[str, Any]], sells: list[dict[str, Any]], net_usd: float, unpriced: int
) -> DisplayHeadline | None:
    """The at-a-glance flow state — present ONLY when the window actually has open-market flow.

    A quiet name adds no "no flow" line to the panel's top strip (honest loudness: the strip marks
    the exception; the section's zero metrics still carry the quiet-is-information read)."""
    if not buys and not sells:
        return None
    if net_usd > 0:
        key, glyph, label = "net_buying", "up", f"net buying {_usd_compact(net_usd)}"
    elif net_usd < 0:
        key, glyph, label = "net_selling", "down", f"net selling {_usd_compact(net_usd)}"
    else:
        key, glyph, label = "net_flat", "flat", "flat net flow"
    bits = [
        f"{len(buys)} buy{'s' if len(buys) != 1 else ''}",
        f"{len(sells)} sell{'s' if len(sells) != 1 else ''}",
    ]
    if unpriced:
        bits.append(f"{unpriced} unpriced")
    return DisplayHeadline(
        key=key, glyph=glyph, label=f"{label} ({WINDOW_DAYS}d)", detail=" · ".join(bits)
    )


def _count(key: str, label: str, value: int) -> DisplayMetric:
    return DisplayMetric(key=key, label=label, value=float(value), unit="count")


def _usd(key: str, label: str, value: float, unpriced: int) -> DisplayMetric:
    note = f"{unpriced} txns without $ value" if unpriced else None
    return DisplayMetric(key=key, label=label, value=round(value, 2), unit="usd", note=note)


def compute(
    rows: list[dict[str, Any]],
    asof: date,
    day_lows: dict[date, float] | None = None,
) -> DisplaySignal | None:
    """Pure trailing-window flow over ingested Form 4 rows (any order; filtered here).

    ``day_lows`` maps a trade date to the security's EOD low that day (``display`` builds it from the SAME
    as-of price view — no lookahead). Because this block CLAIMS "open-market", code-P buys are screened
    against it exactly as the call is (``_is_open_market_buy``): an offer-price IPO/PIPE subscription files
    as code P yet transacts below the public tape, so it drops out of the buy total (and is NAMED in the
    basis, never silently dropped). Absent/empty ``day_lows`` screens nothing on price (recall-safe, #9).
    Only buys are screened — the offer-price conflation is a buy-side phenomenon; sells are the raw code-S
    tape.
    """
    if not rows:
        return None  # nothing ingested for this name — nothing to say, honestly absent
    lows = day_lows or {}
    start = asof - timedelta(days=WINDOW_DAYS)
    windowed = [r for r in rows if start < r["valid_from"] <= asof]
    buys: list[dict[str, Any]] = []
    # set-aside off-market / implausible code-P rows — named in the basis, never silently dropped (#9)
    # DEFERRED (Band 03 S2c, operator decision 3): this 90d net-flow still counts an issuer SELF-FILING
    # as a buy (identity-blind ``_is_open_market_buy``) while the call screens it — the tape and the
    # call knowingly disagree on that class. The Scoreboard ledger now LABELS self-filings
    # (``_screen`` → ``character``); re-basing this figure is a separate, operator-signed decision.
    offmarket: list[dict[str, Any]] = []
    for r in windowed:
        if r.get("txn_code") != BUY:
            continue
        (buys if _is_open_market_buy(r, lows) else offmarket).append(r)
    sells = [r for r in windowed if r.get("txn_code") == SELL]
    # the 30d sub-window: the subset of the ALREADY-screened open-market buys whose valid_from sits
    # inside the tighter trailing window (asof-30, asof]. A pure filter on the same rows — no
    # re-screen, no lookahead (buys are already <= asof); mirrors the 90d boundary (day 29 in, day 30
    # out, exactly as the 90d window includes day 89 and excludes day 90).
    start_short = asof - timedelta(days=WINDOW_DAYS_SHORT)
    buys_30d = [r for r in buys if r["valid_from"] > start_short]
    buy_usd, buys_unpriced = _usd_sum(buys)
    sell_usd, sells_unpriced = _usd_sum(sells)

    metrics = [
        _count("buy_count", "buys", len(buys)),
        _count("sell_count", "sells", len(sells)),
        _count("distinct_buyers", "buyers", len({r.get("insider_name") for r in buys})),
        # the 30d sub-window counts (Ins 30d) — same screened rows, tighter window
        _count("buy_count_30d", "buys 30d", len(buys_30d)),
        _count("distinct_buyers_30d", "buyers 30d", len({r.get("insider_name") for r in buys_30d})),
        _usd("buy_usd", "buy $", buy_usd, buys_unpriced),
        _usd("sell_usd", "sell $", sell_usd, sells_unpriced),
        _usd("net_usd", "net $", buy_usd - sell_usd, buys_unpriced + sells_unpriced),
    ]
    events: list[DisplayEvent] = []
    if buys:
        events.append(
            DisplayEvent(
                key="last_buy",
                label="last open-market buy",
                date=max(r["valid_from"] for r in buys),
                direction="up",
            )
        )
    if sells:
        events.append(
            DisplayEvent(
                key="last_sell",
                label="last open-market sell",
                date=max(r["valid_from"] for r in sells),
                direction="down",
            )
        )
    basis = DisplayBasis(
        source="fact_insider_txn",
        params={
            "window_days": WINDOW_DAYS,
            "window_days_short": WINDOW_DAYS_SHORT,
            "codes": [BUY, SELL],
            "offmarket_below_low_frac": OFFMARKET_BELOW_LOW_FRAC,
            "max_plausible_txn_usd": MAX_PLAUSIBLE_TXN_USD,
        },
        window_start=start + timedelta(days=1),
        window_end=asof,
        note=_basis_note(offmarket),
    )
    return DisplaySignal(
        kind=MEMBER_NAME,
        label=LABEL,
        headline=_flow_headline(buys, sells, buy_usd - sell_usd, buys_unpriced + sells_unpriced),
        metrics=metrics,
        events=events,
        basis=basis,
    )


def display(pit: DisplayPointInTimeData, security_id: UUID, asof: date) -> DisplaySignal | None:
    """Read ingested Form 4 rows via the point-in-time view; the pure ``compute`` windows them.

    Also builds the per-day EOD-low map from the SAME as-of price view (no lookahead) so ``compute`` can
    screen offer-price primary-market subscriptions out of the "open-market" buy total — the same screen the
    call applies (``backend/signals/insider_conviction.py``). A buy older than the earliest bar we hold has
    no low → kept (#9).
    """
    day_lows = {
        b["d"]: float(b["low"]) for b in pit.price_history(security_id) if b.get("low") is not None
    }
    return compute(pit.insider_txns(security_id), asof, day_lows=day_lows)


MEMBER = register_display_member(DisplayMember(name=MEMBER_NAME, compute=display))
