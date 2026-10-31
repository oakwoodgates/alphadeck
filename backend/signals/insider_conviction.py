from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta, timezone
from typing import Any, NamedTuple
from uuid import UUID

from domain.config import DEFAULT_CONFIG, CallConfig
from domain.enums import Grade, Kind, Role
from domain.insider_internal_transfers import is_internal_transfer, screened_label
from domain.insider_joint_reports import (
    JOINT_ACCESSIONS,
    joint_accessions,
    joint_labels,
    roles_of,
)
from domain.market_time import edgar_night, view_day
from domain.signal import SignalEvent
from domain.splits import tape_scale
from signals.base import Detector, SignalPointInTimeData
from signals.common import entry_signal_is_live, fired_signal, source_provenance
from signals.registry import register_detector

DETECTOR_NAME = "insider_conviction"
# the Form 4 codes this detector reads (open-market OR private purchase) — declared on the Detector so the R8-A
# amendment classifier's signal code set is DERIVED from it (signals.horizons.insider_signal_codes)
TXN_CODES: frozenset[str] = frozenset({"P"})


def _is_senior(role: str | None, keywords: frozenset[str]) -> bool:
    if not role:
        return False
    r = role.lower()
    return any(k in r for k in keywords)


def _score(n_distinct: int, total_usd: float, senior: bool, cfg: CallConfig) -> float:
    # Conservative, bounded: scales with cluster breadth + seniority + size, capped below "certain".
    s = 0.4
    s += min(n_distinct, 3) * 0.1
    s += 0.2 if senior else 0.0
    s += min(total_usd / (cfg.insider_core_min_usd * 4.0), 1.0) * 0.2
    return round(min(s, 0.95), 4)


def _is_open_market_buy(
    txn: dict[str, Any],
    day_lows: dict[date, float],
    cfg: CallConfig,
    price_scale: Callable[[date], float] | None = None,
) -> bool:
    """Does this code-P purchase belong in the OPEN-MARKET conviction total?

    SEC transaction code 'P' is "open market OR PRIVATE purchase" — not a synonym for open-market. A
    primary-market subscription (IPO allocation / PIPE / private placement) files as code P at the OFFER
    price, which sits below the security's public trading range that day; counting it as open-market
    conviction inflated the call (PBLS: RA Capital's $394M IPO subscription at $20 vs a $29.65-$34.47 tape).
    We separate the two the only way the structured data allows — the buy's price vs the security's own EOD
    low for the day (see ``CallConfig.insider_offmarket_below_low_frac``). Two exclusions:

    - **below the day's low** → a primary-market/offer-price subscription, not an open-market purchase.
    - **above the absolute $ ceiling** (``insider_max_plausible_txn_usd``) → a physically-impossible row
      (bad source data: a $100,000/share price → a $2T buy) the price check misses.

    Recall-safe (#9): no day low → KEEP (never silently drop); a genuine open-market print is within
    [low, high]. **Splits (S2):** the Form 4 states the price AS TRADED while the stored tape is on the split
    list's scale (every bar before a listed split is adjusted), so ``price_scale(trade_date)`` (``domain.splits
    .tape_scale``) puts the price on the tape's scale before the compare — without it a genuine buy before a
    1-for-16 split ($0.30) read as far below its adjusted day low ($4.80) and was dropped. Only the per-share
    price is scaled; the $ total (shares x price) is split-invariant. Excluded rows STAY in
    ``fact_insider_txn`` + the display tape — only the CALL skips them.
    """
    if float(txn.get("usd") or 0.0) > cfg.insider_max_plausible_txn_usd:
        return False
    price = txn.get("price")
    low = day_lows.get(txn.get("valid_from"))
    if price is not None and low is not None:
        scale = price_scale(txn["valid_from"]) if price_scale is not None else 1.0
        if float(price) * scale < low * (1.0 - cfg.insider_offmarket_below_low_frac):
            return False
    return True


def _norm_entity(name: str | None) -> str:
    """Minimal normalization for a self-filing name match: casefold + collapse whitespace + drop a trailing
    period ('Roivant Sciences Ltd.' -> 'roivant sciences ltd'). Deliberately does NOT strip corporate
    suffixes (CORP/INC/LP): a self-filing has the SAME name on both sides, so no stripping is needed to match
    — and stripping would only widen the match and risk excluding a real, non-self buy (recall-safe, #9).
    """
    if not name:
        return ""
    return " ".join(name.strip().casefold().rstrip(".").split())


def _is_issuer_self(txn: dict[str, Any], issuer_name: str | None) -> bool:
    """Did the ISSUER file this Form 4 on ITSELF (reporting owner == issuer)?

    SEC code 'P' is filed by any acquirer, not just the officers/directors the open-market-purchase
    literature (Lakonishok-Lee) is about. The cleanest false positive is the company filing on its own stock
    — KYOCERA-on-KYOCERA ($690M @ $21.75), Roivant-on-Roivant ($350M @ $21): a buyback / treasury / ADR
    mechanic priced AT the market (so the price screen keeps it), never personal insider conviction (#3). We
    recognize it two ways, most-robust first:

    - **CIK equality** — ``rpt_owner_cik == issuer_cik`` (both captured from the filing; migration 0024). The
      canonical match; present on rows ingested after the capture, and it flows into replay via ``SELECT *``.
    - **name equality** — the filer name equals the issuer name (the row's captured ``issuer_name`` or, for a
      row ingested before the capture, the security's ``security_master`` name passed as ``issuer_name``).
      This is what makes the screen effective on ALREADY-ingested rows with no backfill.

    Recall-safe (#9): a self-filing is the ONLY time the filer name equals the issuer name, and the failure
    mode is one-directional — a missing CIK / name-format mismatch simply KEEPS the row (never drops a real
    buy). Excluded rows STAY in ``fact_insider_txn`` + the display tape; only the open-market conviction total
    skips them.
    """
    oc, ic = txn.get("rpt_owner_cik"), txn.get("issuer_cik")
    if oc and ic and str(oc).strip().lstrip("0") == str(ic).strip().lstrip("0"):
        return True
    filer = _norm_entity(txn.get("insider_name"))
    issuer = _norm_entity(txn.get("issuer_name") or issuer_name)
    return bool(filer) and filer == issuer


# The US "American Depositary" line NEVER carries the home-market foreign symbol in its title ("American
# Depositary Shares (TSM)"), so the containment test in ``_is_foreign_ordinary`` already keeps it; this token
# guard is belt-and-suspenders — a title positively naming a depositary instrument is the US ADR we hold and
# is KEPT even if it also cited the ordinary symbol (recall-safe #9). Both SEC spellings covered.
_ADR_TITLE_TOKENS = ("depositary", "depository")


def _norm_title(s: str | None) -> str:
    """casefold + collapse whitespace (the ``_norm_entity`` idiom) — so a foreign-symbol containment test is
    case/spacing-insensitive ("2330.TW" matches "Common Shares (2330.tw)")."""
    if not s:
        return ""
    return " ".join(str(s).casefold().split())


def _is_foreign_ordinary(txn: dict[str, Any]) -> bool:
    """Is this transaction in the issuer's FOREIGN/ORDINARY line, mis-filed on its US ADR's tape? (S2c)

    A dual-listed foreign issuer files ONE Form 4 stream under ONE CIK covering BOTH its US ADR and its
    home-market ordinary shares; ingest resolves that CIK to the single (ADR) ``security_master`` row and
    stamps EVERY transaction with the ADR's ``security_id`` — so the home-market ordinary buys (ESPP/LTI)
    land on the ADR's tape (TSM: 2330.TW ESPP buys under the TSM ADR; #3). The ADR and the ordinary line are
    DIFFERENT instruments (a 5:1 ratio, a different currency + price), so an ordinary row is not personal
    conviction in the ADR we hold.

    POSITIVE, keep-when-ambiguous (#9). Screen ONLY when the issuer DECLARES a foreign trading symbol
    (``issuer_foreign_symbol`` — its OWN structured tell that it is dual-listed) AND the transaction's
    ``security_title`` positively NAMES that foreign symbol ("Common Shares (2330.TW)" contains 2330.TW;
    Petrobras titles the row bare "PETR4"). The genuine ADR row ("American Depositary Shares (TSM)") does NOT
    contain the foreign symbol -> KEPT + fires; a title positively naming a depositary instrument is likewise
    KEPT (the token guard). A row with NO declared foreign symbol (a US issuer) or a NULL/absent title (rows
    ingested before this capture) is KEPT — a one-directional failure mode, exactly like the issuer-self
    screen. Excluded rows STAY in ``fact_insider_txn`` + the display tape; only the CALL skips them.

    Verified on the full TSM Form 4 tape (174 filings): 186 home-market rows screened (178 of them
    code-P buys), all 9 genuine ADR rows kept, 0 misclassified. The effect on the call: the pre-fix
    conviction read "31 insiders bought $1.0M across 48 txns" (riding the mis-attributed ESPP/LTI rows);
    with the screen it reads the genuine "2 insiders bought $329,810 across 4 txns" (ADR only).
    """
    fsym = txn.get("issuer_foreign_symbol")
    title = _norm_title(txn.get("security_title"))
    if not fsym or not title:
        return False  # not dual-listed / no title -> KEPT (keep-when-ambiguous, #9)
    if any(tok in title for tok in _ADR_TITLE_TOKENS):
        return False  # the title names the US depositary line we hold -> KEPT
    return _norm_title(fsym) in title


class _Candidate(NamedTuple):
    """One CANDIDATE anchor's fully-evaluated cluster (see ``score``'s anchor walk).

    Only QUALIFYING candidates are built (total >= ``insider_min_usd`` and live for their own
    grade), so the selection below is a pure ranking — ``is_core`` first, then ``anchor`` — never a
    second round of floors. ``anchor`` is the cluster's latest TRADE date (its composition); ``public`` is
    the night its latest filing became public (its CLOCK — ``_public_night``, F8).
    """

    is_core: bool
    anchor: date
    buys: list[dict[str, Any]]
    total_usd: float
    n_distinct: int
    senior: bool
    liveness: int
    public: date


# what a buy's provenance says when its row carries no SEC acceptance (its clock is then its trade date)
NO_ACCEPTANCE = "no acceptance stored"


def _acceptance(txn: dict[str, Any]) -> datetime | None:
    """The buy's SEC acceptance (``accepted``) as an aware instant, or ``None`` when the row carries none. NEVER
    ``recorded_at``: when we stored a row is bookkeeping, and a clock reading it made the card depend on the
    ingest — two ingests of one filing wrote different cards, every re-version (a repair, a re-parse)
    re-recorded it, and the live read moved a stale buy's clock to a repair's night (fresh again, able to
    re-arm). A naive value (no engine returns one) is read as UTC."""
    v = txn.get("accepted")
    if isinstance(v, datetime):
        return v if v.tzinfo is not None else v.replace(tzinfo=timezone.utc)
    return None


def _buy_night(txn: dict[str, Any]) -> date:
    """One buy's clock day: the night its Form 4 was public (``edgar_night`` of its acceptance — a time inside
    EDGAR's hours is its own market date, one past midnight belongs to the night whose read first holds it,
    F6), never before its own trade (an acceptance stored before the trade is bad data); a buy with no
    acceptance keeps its TRADE date, the rule before F8 (the read still decides whether the row is visible at
    all, so this is never a lookahead — it only keeps that buy's window where it was)."""
    at = _acceptance(txn)
    trade = txn["valid_from"]
    return trade if at is None else max(trade, edgar_night(at))


def _public_night(buys: Sequence[dict[str, Any]], asof: date) -> date:
    """THE INSIDER CLOCK (F8, operator decision 2026-10-31, rule log ``2026-11-insider-clock-from-filing``): the
    latest ``_buy_night`` among the cluster's KEPT buys — the night its latest filing became public (a buy with
    no acceptance stored counts from its trade date) — never after ``asof``: only the live read of a past night
    (F1's morning view, which reads everything held by now) can hold a filing public after that night; it
    counts from the night read. Deterministic in the filings alone: no stored time reaches it."""
    return min(max(_buy_night(t) for t in buys), asof)


def _cluster_at(
    p_buys: list[dict[str, Any]], anchor: date, asof: date, w: float, cfg: CallConfig
) -> _Candidate | None:
    """Evaluate the cohesion cluster anchored on ``anchor`` — or None if it does not qualify.

    The window is bounded on BOTH sides, ``[anchor - insider_cluster_window_days, anchor]``: the
    upper bound is what keeps a LATER buy out of an older anchor's total (an unbounded gather would
    let the walk double-count the very buy that shadowed this anchor). Floors + grade + graded
    liveness are exactly the single-anchor rules, unchanged — only WHICH anchors get asked is new.
    The window is on TRADE dates (what the cluster IS); the liveness counts from the night its latest
    filing became public (``_public_night``, F8 — when the market could first read it).
    """
    floor = anchor - timedelta(days=cfg.insider_cluster_window_days)
    buys = [t for t in p_buys if floor <= t["valid_from"] <= anchor]
    total_usd = float(
        sum(float(t.get("usd") or 0) * (w if t.get("aff_10b5_1") is True else 1.0) for t in buys)
    )
    if total_usd < cfg.insider_min_usd:
        return None
    distinct = {t.get("insider_name") for t in buys if t.get("insider_name")}
    # every role a row speaks for — a joint-report survivor carries its co-reporters' too, so a buy a senior
    # officer co-reported stays senior when it is counted once (domain.insider_joint_reports)
    senior = any(
        _is_senior(role, cfg.insider_senior_role_keywords) for t in buys for role in roles_of(t)
    )
    # core via a multi-insider cluster, OR a single strong senior buy above the high floor (calibration)
    is_core = senior and (
        (len(distinct) >= cfg.insider_core_min_distinct and total_usd >= cfg.insider_core_min_usd)
        or total_usd >= cfg.insider_strong_single_usd
    )
    liveness = (
        cfg.insider_core_alpha_liveness_days if is_core else cfg.insider_flip_alpha_liveness_days
    )
    # Freshness floor at the GRADED horizon (mirrors volume_breakout): drop the cluster once its edge
    # has decayed for its grade, so re-derivation/replay stays honest and a flip can't linger for months.
    # F8: the window counts from the night the cluster's latest filing was public, not its trade date — a
    # Form 4 is public a median 2 days after the trade, and a late one could otherwise expire before anyone
    # could read it.
    public = _public_night(buys, asof)
    if not entry_signal_is_live(public, liveness, asof):
        return None
    return _Candidate(is_core, anchor, buys, total_usd, len(distinct), senior, liveness, public)


def _accession_detail(
    buys: Sequence[dict[str, Any]], accessions: set[str]
) -> dict[str, dict[str, Any]]:
    """Each cited filing's work (#6, F8): ``traded`` = the trade dates of the cluster's buys it reports (a joint
    report's co-reporter filings list the trade they share); ``accepted`` = the SEC acceptance the clock read
    for the buys under its OWN accession, or ``NO_ACCEPTANCE`` when none is stored (that buy's clock is its
    trade date) — absent for a co-reporter's filing, which the read holds no row of. Only filing facts, never
    a stored time, so the card is the same on every ingest of the same filings."""
    traded: dict[str, set[date]] = {acc: set() for acc in accessions}
    accepted: dict[str, datetime | None] = {}
    for t in buys:
        own = str(t["accession"]) if t.get("accession") else None
        cited = {str(a) for a in t.get(JOINT_ACCESSIONS) or ()}
        if own is not None:
            cited.add(own)
        for acc in cited:
            traded.setdefault(acc, set()).add(t["valid_from"])
        if own is not None:
            at = _acceptance(t)
            prev = accepted.get(own)
            accepted[own] = at if prev is None or (at is not None and at > prev) else prev
    # keys in jsonb's own order (shorter first, then bytewise: "traded" before "accepted"), so a card stored in a
    # jsonb column (calls, edition pages) reads back byte-identical to the engine's
    out: dict[str, dict[str, Any]] = {}
    for acc in accessions:
        d: dict[str, Any] = {"traded": [x.isoformat() for x in sorted(traded.get(acc, ()))]}
        if acc in accepted:
            at = accepted[acc]
            d["accepted"] = NO_ACCEPTANCE if at is None else at.astimezone(timezone.utc).isoformat()
        out[acc] = d
    return out


def score(
    txns: list[dict[str, Any]],
    security_id: UUID,
    asof: date,
    cfg: CallConfig = DEFAULT_CONFIG,
    day_lows: dict[date, float] | None = None,
    issuer_name: str | None = None,
    price_scale: Callable[[date], float] | None = None,
) -> SignalEvent | None:
    """Pure: score an open-market insider cluster into a Key-1 SignalEvent (or None).

    Reads only open-market purchases (code 'P'); never fires on sales. Two screens keep a code-P row OUT of
    the open-market conviction total (both supplied by ``detect`` from the point-in-time view; both
    recall-safe, #9):

    - ``day_lows`` maps a trade date to the security's EOD low that day → screens out primary-market
      subscriptions (IPO/PIPE offer-price buys that file as code P but transact below the public tape) and
      physically-impossible rows (see ``_is_open_market_buy``); ``price_scale`` puts each as-traded price
      on that tape's split scale first (S2; absent = 1.0).
    - ``issuer_name`` is the security's name → screens out a SELF-FILING (the issuer filing a Form 4 on its
      own stock — a buyback/treasury/ADR mechanic, priced AT market so the price screen keeps it), see
      ``_is_issuer_self``.
    - the row's own ``security_title`` + ``issuer_foreign_symbol`` (captured at ingest) → screens out a
      home-market ORDINARY-share row mis-filed on a US ADR's tape (a dual-listed issuer's 2330.TW ESPP buys
      under the TSM ADR), see ``_is_foreign_ordinary``. Pure per-row predicate — no external screen input.
    - the read's ``INTERNAL_TRANSFER`` tag (``domain.insider_internal_transfers``, applied in the shared seam) →
      a buy matched to a sale between LINKED filers (AMKR: James Kim -> his children's Sujoda Investments) is no
      purchase: left out, and the ones inside the chosen cluster's window are named in the label
      ("; 1 internal-transfer txn ($18,999,995) screened"). A private purchase between unrelated people is NOT
      tagged and still counts (the operator's decision 3).

    Absent/``None`` ``day_lows``/``issuer_name`` only disables that one screen (nothing over-excluded).

    **Anchor selection (the walk).** A cluster is one EPISODE of buying: an anchor date plus the kept
    buys inside ``[anchor - insider_cluster_window_days, anchor]``. Every distinct kept-buy date is a
    CANDIDATE anchor; each is evaluated independently (total, distinct, seniority, grade, and the
    GRADED liveness — a flip decays in weeks, a CORE cluster over months) and QUALIFIES iff its total
    clears ``insider_min_usd`` and it is still live for its own grade. Among the qualifying candidates
    the strongest fires: **prefer CORE, then the most recent anchor** — ``catalyst_conviction``'s
    selection precedent. None qualify → None.

    Anchoring UNCONDITIONALLY on the single most-recent buy (the shape before this) let one small,
    late, non-senior buy SHADOW an older still-live CORE: the new anchor's 30-day window excluded the
    big buy, the grade fell to flip, and the flip's 18-day liveness then expired the whole conviction —
    so *more* insider buying read *less* bullish, contradicting this docstring's own "the lookback
    never drops a still-live conviction". The walk is what makes that sentence true.

    **The clock starts at the filing (F8, operator decision 2026-10-31, rule log
    ``2026-11-insider-clock-from-filing``).** The cluster's COMPOSITION is on trade dates (the walk, the
    window, the grade and the selection above are unchanged); its CLOCK is the night its LATEST filing became
    public (``_public_night``: the latest night among the CHOSEN cluster's kept buys, each buy's being the night
    of its SEC acceptance, or its trade date when no acceptance is stored — never a stored time). The event's
    ``asof`` — its fire date, so ``event_date``, the liveness
    test, ``exit_by`` and the arm date — is that night: a cluster traded Jan 30 → Feb 25 whose last Form 4 was
    accepted Feb 27 fires Feb 27. Before F8 it fired on the latest trade (Feb 25), so a normal filing spent 2-4
    days of an 18-day flip window before anyone could read it, and a late one could expire unseen. The trade
    dates stay in the label ("traded 2026-01-30 to 2026-02-25") and on every accession's provenance
    (``detail["traded"]``, beside its ``detail["accepted"]`` instant or "no acceptance stored"). **What the walk still bounds:** a
    candidate anchor (trade date) older than the longest graded liveness before ``asof`` is never asked, so a
    trade more than ``max(core, flip liveness)`` days old cannot fire however late its filing (named: it keeps
    ``horizons()`` — the read window — exact). The provenance is the chosen cluster's accessions.
    Grade rule (§3, config-driven): core if a senior officer + >= N distinct insiders + >= $ threshold.
    """
    lows = day_lows or {}
    # DORMANT 10b5-1 planned-buy weight (Band 03 S2c, `insider_10b5_1_buy_weight`): scales a KEPT
    # planned buy's $ contribution; a planned buy is PRESENT (distinct / senior / anchor) iff w > 0
    # (w == 0.0 is a FULL screen — the buy leaves the survivor set here, so the anchor moves). At the
    # 1.0 default this hook is byte-identity (`x * 1.0` is IEEE-exact; the fold order is unchanged) —
    # flipping the dial is a measured config decision, never a code change. Tri-state: only an
    # explicit `aff_10b5_1 is True` weighs; None/False weigh 1.0 (#9). See docs/CALL_LOGIC.md §3.
    w = cfg.insider_10b5_1_buy_weight
    # an INTERNAL TRANSFER (a buy matched to a sale between linked filers — domain.insider_internal_transfers,
    # tagged in the read) is no purchase at all: kept on the read, left out here, NAMED in the label below (#6)
    transfers = [
        t
        for t in txns
        if t.get("txn_code") in TXN_CODES
        and t.get("valid_from") is not None
        and t["valid_from"] <= asof
        and is_internal_transfer(t)
    ]
    p_buys = [
        t
        for t in txns
        if t.get("txn_code") in TXN_CODES
        and t.get("valid_from") is not None
        and t["valid_from"] <= asof
        and not is_internal_transfer(t)
        and _is_open_market_buy(t, lows, cfg, price_scale)
        and not _is_issuer_self(t, issuer_name)
        and not _is_foreign_ordinary(
            t
        )  # S2c: a home-market ordinary row mis-filed on the ADR's tape
        and not (w == 0.0 and t.get("aff_10b5_1") is True)
    ]
    if not p_buys:
        return None
    # THE ANCHOR WALK (see the docstring): every distinct kept-buy date is a candidate anchor, newest
    # -> oldest, each evaluated on its own window so unrelated buys months apart aren't fused into one
    # cluster. Stopping short of the newest anchor is the whole point — a small late buy must not shadow
    # an older, still-live CORE. Stamping the event at the CHOSEN cluster's public night (F8 — not the query
    # asof, and no longer its trade date) anchors exit_by/liveness to when the market could first read that
    # conviction; that night is ALSO the date shown on the call-card trigger row (event_date). See
    # docs/CALL_LOGIC.md §6.
    # The walk stops once an anchor (a TRADE date) is older than the LONGEST configured horizon. Before F8 no
    # older candidate could be live; since F8 one filed late could be, and the stop deliberately keeps it out
    # (a trade that old is stale however late its filing — the docstring), which keeps horizons() exact.
    earliest_live_anchor = asof - timedelta(
        days=max(cfg.insider_core_alpha_liveness_days, cfg.insider_flip_alpha_liveness_days)
    )
    candidates: list[_Candidate] = []
    for candidate_anchor in sorted({t["valid_from"] for t in p_buys}, reverse=True):
        if candidate_anchor < earliest_live_anchor:
            break
        found = _cluster_at(p_buys, candidate_anchor, asof, w, cfg)
        if found is not None:
            candidates.append(found)
    if not candidates:
        return None
    # the strongest LIVE conviction: prefer a CORE cluster, then the most recent anchor — the same
    # selection key catalyst_conviction uses to pick among live catalysts.
    best = max(candidates, key=lambda c: (c.is_core, c.anchor))
    # the cluster's filings — a joint report's co-reporters' filings included (the work shown, #6)
    by_accession = joint_accessions(best.buys)
    detail = _accession_detail(best.buys, by_accession)
    # the trade dates — the cluster's composition — stay on the label now that the fire date is the filing's
    traded = sorted({t["valid_from"] for t in best.buys})
    span = (
        traded[0].isoformat()
        if len(traded) == 1
        else f"{traded[0].isoformat()} to {traded[-1].isoformat()}"
    )
    label = (
        f"{best.n_distinct} insider{'s' if best.n_distinct != 1 else ''}"
        f"{' incl. senior officer' if best.senior else ''} bought "
        f"${best.total_usd:,.0f} open-market (code P) across {len(best.buys)} txns (traded {span})"
    )
    # the internal transfers inside THIS cluster's window, named beside the number (only when present — a card
    # without one keeps its exact label, no record churn)
    floor = best.anchor - timedelta(days=cfg.insider_cluster_window_days)
    screened = screened_label([t for t in transfers if floor <= t["valid_from"] <= best.anchor])
    if screened:
        label += f"; {screened}"
    joint = joint_labels(best.buys)  # trades several persons reported, counted once (named, #6)
    if joint:
        label += f"; counted once: {'; '.join(joint)}"
    return fired_signal(
        detector=DETECTOR_NAME,
        security_id=security_id,
        role=Role.ENTRY_TRIGGER,
        kind=Kind.INSIDER,
        grade=Grade.CORE if best.is_core else Grade.FLIP,
        score=_score(best.n_distinct, best.total_usd, best.senior, cfg),
        label=label,
        alpha_liveness_days=best.liveness,
        provenance=[
            source_provenance("form4", acc, detail=detail[acc]) for acc in sorted(by_accession)
        ],
        asof=best.public,
    )


def detect(
    pit: SignalPointInTimeData,
    security_id: UUID,
    asof: date,
    cfg: CallConfig = DEFAULT_CONFIG,
) -> SignalEvent | None:
    """Key 1 — insider conviction (warms). Reads open-market purchases via the point-in-time view.

    From the SAME as-of view (no lookahead) it also builds the two screens for the open-market total: the
    per-day low map (``price_history``) that drops primary-market/off-market code-P rows
    (``_is_open_market_buy``), and the security's name (identity, not a bitemporal fact) that drops a
    self-filing — the issuer filing a Form 4 on its own stock (``_is_issuer_self``; rows carrying the
    issuer/owner CIKs match canonically without the name). A buy older than the earliest bar has no low → kept (#9).
    """
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
        price_scale=tape_scale(pit.split_events(security_id), through=view_day(pit.known_at)),
    )


def horizons(cfg: CallConfig) -> dict[str, int | None]:
    """The READ-HORIZON declaration (``signals/horizons.py``): the furthest-back event date ``detect``
    can ever need per table, from the SAME dials ``score`` runs with.

    - ``fact_insider_txn`` — the anchor walk stops at ``asof - max(core, flip liveness)`` (the
      ``earliest_live_anchor`` line in ``score``) and each candidate gathers buys inside
      ``[anchor - insider_cluster_window_days, anchor]``, so the oldest buy that can ever enter a
      qualifying cluster is ``asof - (max liveness + cluster window)``. An older row would sit in
      ``p_buys`` but can never be a candidate anchor nor fall inside one's window.
    - ``fact_price_eod`` — the SAME horizon, deliberately: the bars feed ONLY the per-day low map for
      ``_is_open_market_buy``, and a buy outside the anchor walk is never scored, so its day-low is never
      needed; a buy inside the walk has its trade-date bar inside this window.
    """
    days = (
        max(cfg.insider_core_alpha_liveness_days, cfg.insider_flip_alpha_liveness_days)
        + cfg.insider_cluster_window_days
    )
    return {"fact_insider_txn": days, "fact_price_eod": days}


DETECTOR = register_detector(
    Detector(name=DETECTOR_NAME, detect=detect, horizons=horizons, txn_codes=TXN_CODES)
)
