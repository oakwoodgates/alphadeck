"""Insider-selling cluster risk (Band 03, Slice S1) — the risk-side mirror of ``insider_conviction``.

A RISK signal (never an entry trigger): clustered DISCRETIONARY open-market sales (Form 4 code ``S``)
by insiders feed the counter-case and a setup-strength haircut — and nothing else. Per the
literature's asymmetry (people sell for many reasons — diversification, taxes, a house — while they
buy for one), sales are weak predictors: the score is CAPPED at ``insider_sell_max_score``, strictly
below ``risk_block_severity``, so a sell cluster can NEVER withhold an arm in v1 (operator decision 1,
2026-08-16). Grade-blind like dilution (``grade=None``, no ``dearm_grade`` — this is not a de-arm).

Screens (each one recall-safe on the CALL only — every row STAYS in ``fact_insider_txn`` and the raw
display tape, ``signals/display/insider_flow.py``, which shows sells unscreened; #9):

- **10b5-1 planned sales** (``aff_10b5_1 is True``) are near-noise and are screened OUT of the
  cluster, with the excluded count + $ NAMED in the label/provenance (#6). ``None`` (unknown — the
  pre-Dec-2022 norm) is KEPT: absence must never assert "planned" *or* "discretionary", and on the
  risk side keeping unknowns errs cautious (a louder counter-case). This is the first reader of the
  tri-state flag migration 0022 captured; the BUY detector still does not read it.
- **Issuer self-filings** (the company transacting its own stock — buyback/treasury/ADR mechanics)
  are never personal insider supply; same identity screen as the buy side.
- **Foreign/ordinary rows mis-filed on an ADR's tape** (a dual-listed issuer's home-market ordinary
  sale — 2330.TW under the TSM ADR) are the WRONG instrument, screened out the same way as the buy
  side (``_is_foreign_ordinary``, S2c). Positive + keep-when-ambiguous: only a title that names the
  declared foreign symbol is screened; a depositary title / no foreign symbol / NULL title is KEPT.
- **Below-day's-low sales** are discounted registered secondaries — real supply, but a *different*
  risk family (the dilution tape's future job), not open-market selling pressure; set aside, named.
  No above-high screen (the buy side's forward-split rationale, mirrored).
- **Implausible rows** (> ``insider_max_plausible_txn_usd``) are bad source data (#3).

Freshness is DETECTOR-ENFORCED: the assembler never ages risk signals (``active_risk`` has no
liveness filter), and a sell cluster is the first event-shaped risk needing an explicit window — a
cluster whose anchor is older than ``insider_sell_liveness_days`` drops out of the re-derived stream
(``entry_signal_is_live``, the shared inclusive helper), so replay stays honest.

MASTER SWITCH (``insider_sell_enabled``) — **DEFAULT ON since the operator flip of 2026-08-19**, on
the sig-lab pass (shipped OFF as operator decision 2). Measured safe on real prod data before the
flip: 58 fires across 5 theses, ZERO arm-withholdings, ZERO de-arms — and "cannot withhold" is
guaranteed BY CONSTRUCTION anyway (the score ceiling above sits strictly below
``risk_block_severity``). The existing seeded goldens are additionally safe structurally: the
committed seed Form 4s carry no code-S rows at all. ``detect`` remains GATED — set the dial False
and it no-ops (registered either way); the pure ``score`` is UNGATED (the math stays testable), and
``replay.run``'s ``--insider-sell`` / ``ALPHADECK_INSIDER_SELL`` set it explicitly for the backtest.

The ``_is_senior`` / ``_norm_entity`` / ``_is_issuer_self`` / ``_is_foreign_ordinary`` (+ its
``_norm_title`` / ``_ADR_TITLE_TOKENS``) helpers DUPLICATE the buy side
(``signals/insider_conviction.py``) with this pointer rather than extracting a shared module: this
slice explicitly does not touch the buy detector's OWN calibration decisions — the display seam's
documented duplicate-with-pointer pattern. If the buy side's screens recalibrate, re-sync these by hand.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from uuid import UUID

from domain.config import DEFAULT_CONFIG, CallConfig
from domain.enums import Kind, Role
from domain.signal import SignalEvent
from signals.base import Detector, SignalPointInTimeData
from signals.common import entry_signal_is_live, fired_signal, source_provenance
from signals.registry import register_detector

DETECTOR_NAME = "insider_sell"

# The screen buckets a code-S row can land in (exactly one each — deterministic attribution). Only
# KEPT rows form the cluster; the others are counted and named (label / provenance detail), never
# silently dropped (#6/#9).
_KEPT = "kept"
_PLANNED = "planned"  # aff_10b5_1 is True — a pre-planned 10b5-1 sale (near-noise)
_SELF = "self"  # the issuer filing on itself — never personal insider supply
_BELOW_LOW = "below_low"  # below the day's tape — a discounted secondary, a different risk family
_IMPLAUSIBLE = "implausible"  # physically-impossible $ — bad source data (#3)
_FOREIGN = "foreign"  # a home-market ordinary line mis-filed on the ADR's tape — the wrong instrument (S2c)

# The COMPLETE vocabulary ``_screen`` can return — the single source of truth for the sell-screen
# buckets, co-located with the constants so a dev ADDING a bucket updates it right HERE (beside the
# constant and the ``return`` branch). The drift-pin test (``tests/scoreboard/test_overlays.py``)
# iterates this set through ``scoreboard.overlays.sell_character_wire`` and asserts every value lands
# in ``InsiderSellOut.character``'s Literal — so a new bucket added WITHOUT its wire-map entry + Literal
# value fails THAT test loudly, never as a runtime response-validation 500 on ``/scoreboard/price-window``.
SELL_SCREEN_BUCKETS: frozenset[str] = frozenset(
    {_KEPT, _PLANNED, _SELF, _BELOW_LOW, _IMPLAUSIBLE, _FOREIGN}
)


def _is_senior(role: str | None, keywords: frozenset[str]) -> bool:
    # duplicated from insider_conviction._is_senior (see module docstring) — ONE definition of
    # "senior" in substance: both read cfg.insider_senior_role_keywords.
    if not role:
        return False
    r = role.lower()
    return any(k in r for k in keywords)


def _norm_entity(name: str | None) -> str:
    # duplicated from insider_conviction._norm_entity (see module docstring): casefold + collapse
    # whitespace + drop a trailing period; deliberately no corporate-suffix stripping (a self-filing
    # has the SAME name on both sides; stripping would widen the match and risk excluding a real,
    # non-self sale — recall-safe, #9).
    if not name:
        return ""
    return " ".join(name.strip().casefold().rstrip(".").split())


def _is_issuer_self(txn: dict[str, Any], issuer_name: str | None) -> bool:
    # duplicated from insider_conviction._is_issuer_self (see module docstring): CIK equality is
    # canonical (migration 0024); the name fallback covers rows ingested before the CIK capture. A
    # missing CIK / name mismatch KEEPS the row (the one-directional failure mode, #9).
    oc, ic = txn.get("rpt_owner_cik"), txn.get("issuer_cik")
    if oc and ic and str(oc).strip().lstrip("0") == str(ic).strip().lstrip("0"):
        return True
    filer = _norm_entity(txn.get("insider_name"))
    issuer = _norm_entity(txn.get("issuer_name") or issuer_name)
    return bool(filer) and filer == issuer


# duplicated from insider_conviction (see module docstring — the buy/sell duplicate-with-pointer pattern):
# the ADR/dual-listed screen. Re-sync by hand if the buy side's rule changes.
_ADR_TITLE_TOKENS = ("depositary", "depository")


def _norm_title(s: str | None) -> str:
    # duplicated from insider_conviction._norm_title: casefold + collapse whitespace.
    if not s:
        return ""
    return " ".join(str(s).casefold().split())


def _is_foreign_ordinary(txn: dict[str, Any]) -> bool:
    """duplicated from insider_conviction._is_foreign_ordinary (see that docstring — the full derivation).

    Is this SALE in the issuer's foreign/ordinary home-market line, mis-filed on its US ADR's tape? Screen
    ONLY when the issuer declares a foreign trading symbol AND the title positively names it (a depositary
    title, or no declared symbol / a NULL title, is KEPT — keep-when-ambiguous, #9). A pure per-row
    predicate; the excluded sale STAYS on the tape + the display flow, only the CALL's cluster skips it.
    """
    fsym = txn.get("issuer_foreign_symbol")
    title = _norm_title(txn.get("security_title"))
    if not fsym or not title:
        return False
    if any(tok in title for tok in _ADR_TITLE_TOKENS):
        return False
    return _norm_title(fsym) in title


def _screen(
    txn: dict[str, Any],
    day_lows: dict[date, float],
    issuer_name: str | None,
    cfg: CallConfig,
) -> str:
    """Which bucket does this code-S row land in? Ordered most-structural first (implausible data, then
    the wrong-INSTRUMENT foreign line, then identity, then price, then the plan flag) so a row tripping
    several screens has ONE deterministic attribution. Absent price context only disables the below-low
    screen — a sale with no day low is KEPT (recall-safe, #9: we cannot prove it was off-market). Note the
    direction of error is inverted vs the buy side: over-screening SELLS makes the platform MORE bullish,
    which is exactly why every set-aside is counted and named (#6)."""
    if float(txn.get("usd") or 0.0) > cfg.insider_max_plausible_txn_usd:
        return _IMPLAUSIBLE
    if _is_foreign_ordinary(txn):  # S2c: a home-market ordinary sale mis-filed on the ADR's tape
        return _FOREIGN
    if _is_issuer_self(txn, issuer_name):
        return _SELF
    price = txn.get("price")
    low = day_lows.get(txn.get("valid_from"))
    if price is not None and low is not None:
        if float(price) < low * (1.0 - cfg.insider_offmarket_below_low_frac):
            return _BELOW_LOW
    if txn.get("aff_10b5_1") is True:  # only an EXPLICIT True screens; None (unknown) is kept
        return _PLANNED
    return _KEPT


def _score(n_distinct: int, total_usd: float, senior: bool, cfg: CallConfig) -> float:
    # Conservative, bounded — the buy-side _score idiom with the RISK CEILING as the clamp: a base
    # plus breadth (distinct sellers), a seniority bump, and dollar-total scaling, clamped to
    # insider_sell_max_score (< risk_block_severity — operator decision 1: a sell cluster haircuts
    # setup strength and feeds the counter-case; it can never withhold an arm in v1). The uncapped
    # sum tops out above the ceiling on purpose, so the clamp genuinely BINDS under an extreme
    # cluster (the ceiling test proves it) and lifting the ceiling later is a visible config diff.
    s = 0.25
    s += min(n_distinct, 4) * 0.05
    s += 0.10 if senior else 0.0
    s += min(total_usd / (cfg.insider_sell_min_usd * 8.0), 1.0) * 0.15
    return round(min(s, cfg.insider_sell_max_score), 4)


def score(
    txns: list[dict[str, Any]],
    security_id: UUID,
    asof: date,
    cfg: CallConfig = DEFAULT_CONFIG,
    day_lows: dict[date, float] | None = None,
    issuer_name: str | None = None,
) -> SignalEvent | None:
    """Pure: score a clustered discretionary open-market sell episode into a RISK SignalEvent (or None).

    Reads only sales (code 'S'); never fires on buys. The screens (see ``_screen``) are supplied by
    ``detect`` from the same point-in-time view the buy side uses; absent ``day_lows``/``issuer_name``
    only disables that one screen (nothing over-excluded).

    **Anchor selection (the walk — the buy side's mechanics, mirrored).** An episode is an anchor date
    plus the kept sales inside ``[anchor - insider_sell_cluster_window_days, anchor]``. Every distinct
    KEPT-sale date is a CANDIDATE anchor, walked newest → oldest; a candidate QUALIFIES iff its anchor
    is inside the freshness window AND the episode clears every floor (total $, distinct sellers,
    senior-required). The MOST RECENT qualifying episode fires — there is no grade axis on a risk, so
    unlike the buy side there is nothing to prefer over recency. Freshness here is grade-INDEPENDENT,
    so once a candidate anchor fails it every older one is staler and the walk stops.

    Anchoring unconditionally on the single most-recent kept sale (the shape before this) let one lone
    late sale RE-ANCHOR the episode onto itself, fail ``insider_sell_min_distinct``, and silence a
    still-live multi-seller cluster — i.e. MORE insider selling read MORE bullish, the risk-side
    mirror of the buy side's shadowed-CORE bug.

    The event's ``asof`` is the chosen anchor (so the card's event_date and the freshness clock agree),
    and the screened counts named in the label/provenance are framed on the CHOSEN episode's window
    (planned/self/below-low/implausible rows falling inside ``[anchor - window, anchor]``) — the work
    behind THIS cluster, not all history. UNGATED by the master switch (``detect`` holds the gate).
    """
    lows = day_lows or {}
    rows = [
        t
        for t in txns
        if t.get("txn_code") == "S" and t.get("valid_from") is not None and t["valid_from"] <= asof
    ]
    if not rows:
        return None
    buckets: dict[str, list[dict[str, Any]]] = {
        _KEPT: [],
        _PLANNED: [],
        _SELF: [],
        _BELOW_LOW: [],
        _IMPLAUSIBLE: [],
        _FOREIGN: [],
    }
    for t in rows:
        buckets[_screen(t, lows, issuer_name, cfg)].append(t)
    kept = buckets[_KEPT]
    if not kept:
        return None

    # THE ANCHOR WALK (see the docstring): each distinct kept-sale date is a candidate FIRE date,
    # newest -> oldest; the first one whose episode clears freshness AND every floor fires. Walking
    # (rather than pinning the single most-recent sale) is what stops a lone late sale from
    # re-anchoring onto itself and silencing a still-live multi-seller cluster.
    anchor: date | None = None
    cluster: list[dict[str, Any]] = []
    total_usd = 0.0
    distinct: set[str | None] = set()
    senior = False
    for candidate_anchor in sorted({t["valid_from"] for t in kept}, reverse=True):
        # Freshness floor on the anchor, detector-enforced (the assembler never ages risks). It is
        # grade-INDEPENDENT here, so once a candidate is stale every OLDER one is too — stop.
        if not entry_signal_is_live(candidate_anchor, cfg.insider_sell_liveness_days, asof):
            break
        lo = candidate_anchor - timedelta(days=cfg.insider_sell_cluster_window_days)
        c = [t for t in kept if lo <= t["valid_from"] <= candidate_anchor]
        c_usd = float(sum(float(t.get("usd") or 0) for t in c))
        if c_usd < cfg.insider_sell_min_usd:
            continue
        c_distinct = {t.get("insider_name") for t in c if t.get("insider_name")}
        # "clustered" is the load-bearing word — one big sale is the many-reasons case
        if len(c_distinct) < cfg.insider_sell_min_distinct:
            continue
        c_senior = any(
            _is_senior(t.get("insider_role"), cfg.insider_senior_role_keywords) for t in c
        )
        if cfg.insider_sell_require_senior and not c_senior:
            continue
        anchor, cluster, total_usd, distinct, senior = (
            candidate_anchor,
            c,
            c_usd,
            c_distinct,
            c_senior,
        )
        break  # the most recent qualifying episode — no grade axis on a risk to prefer over recency
    if anchor is None:
        return None
    floor = anchor - timedelta(days=cfg.insider_sell_cluster_window_days)

    def _in_window(t: dict[str, Any]) -> bool:
        return floor <= t["valid_from"] <= anchor

    # the named screens, framed on the SAME episode window — the work shown beside the number (#6)
    planned_w = [t for t in buckets[_PLANNED] if _in_window(t)]
    planned_usd = float(sum(float(t.get("usd") or 0) for t in planned_w))
    below_low_w = [t for t in buckets[_BELOW_LOW] if _in_window(t)]
    self_w = [t for t in buckets[_SELF] if _in_window(t)]
    implausible_w = [t for t in buckets[_IMPLAUSIBLE] if _in_window(t)]
    foreign_w = [t for t in buckets[_FOREIGN] if _in_window(t)]
    unknown_plan = [t for t in cluster if t.get("aff_10b5_1") is None]

    label = (
        f"{len(distinct)} insider{'s' if len(distinct) != 1 else ''}"
        f"{' incl. senior officer' if senior else ''} sold "
        f"${total_usd:,.0f} open-market (code S) across {len(cluster)} txns"
    )
    if planned_w:
        label += (
            f"; {len(planned_w)} planned-sale (10b5-1) txn{'s' if len(planned_w) != 1 else ''} "
            f"(${planned_usd:,.0f}) screened"
        )
    if below_low_w:
        label += (
            f"; {len(below_low_w)} below-day-low txn{'s' if len(below_low_w) != 1 else ''} "
            f"set aside (not open-market)"
        )

    # the work behind the number, stamped on every accession ref (the dilution shared-detail idiom)
    detail = {
        "total_usd": round(total_usd, 2),
        "distinct_sellers": len(distinct),
        "txn_count": len(cluster),
        "senior_in_cluster": senior,
        "anchor": anchor.isoformat(),
        "cluster_window_days": cfg.insider_sell_cluster_window_days,
        "planned_screened": len(planned_w),
        "planned_screened_usd": round(planned_usd, 2),
        "unknown_plan_kept": len(unknown_plan),
        "self_filings_screened": len(self_w),
        "below_low_set_aside": len(below_low_w),
        "implausible_dropped": len(implausible_w),
        "foreign_ordinary_screened": len(foreign_w),  # S2c: wrong-instrument rows off the ADR tape
    }
    by_accession = {t["accession"] for t in cluster if t.get("accession")}
    return fired_signal(
        detector=DETECTOR_NAME,
        security_id=security_id,
        role=Role.RISK_SIGNAL,
        kind=Kind.INSIDER_SELL,
        grade=None,  # a risk is ungraded; and no dearm_grade — this is not a de-arm
        score=_score(len(distinct), total_usd, senior, cfg),
        label=label,
        alpha_liveness_days=None,  # matching dilution/breakdown: freshness is enforced ABOVE
        provenance=[source_provenance("form4", acc, detail=detail) for acc in sorted(by_accession)],
        asof=anchor,
    )


def detect(
    pit: SignalPointInTimeData,
    security_id: UUID,
    asof: date,
    cfg: CallConfig = DEFAULT_CONFIG,
) -> SignalEvent | None:
    """Risk signal — clustered discretionary insider selling. Reads Form 4 sales via the
    point-in-time view; from the SAME as-of view (no lookahead) it builds the below-low price screen
    and the issuer-self identity screen, exactly like the buy side's ``detect``.

    MASTER SWITCH — **DEFAULT ON since the operator flip of 2026-08-19** (shipped OFF as operator
    decision 2): no-ops when ``cfg.insider_sell_enabled`` is off, so an explicit False still keeps
    every sell event out of the stream / counter-case / setup strength. Stays REGISTERED either way
    (the registry-list test expects it). The pure ``score`` is UNGATED."""
    if not cfg.insider_sell_enabled:
        return None
    day_lows = {
        b["d"]: float(b["low"]) for b in pit.price_history(security_id) if b.get("low") is not None
    }
    return score(
        pit.insider_txns(security_id),
        security_id,
        asof,
        cfg,
        day_lows=day_lows,
        issuer_name=pit.security_name(security_id),
    )


DETECTOR = register_detector(Detector(name=DETECTOR_NAME, detect=detect))
