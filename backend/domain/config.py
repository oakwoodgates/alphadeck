from __future__ import annotations

from pydantic import Field, model_validator

from domain.base import DomainModel
from domain.enums import CatalystType, Grade, Kind, Role


class CorporateEventItemPolicy(DomainModel):
    """One 8-K item code's CALL-POLICY row (Band 03 S3) — the evidence/policy seam's policy half.

    The ``fact_corporate_event`` tape stores objective facts (form / items / filed / accession);
    THIS map is where an item code becomes a signal: role (trigger vs risk), grade + catalyst type
    (trigger side), score, and the per-item liveness window. Applied on READ by the two corporate
    detectors — never baked into stored rows, so retuning a value re-derives every call with zero
    data repair. ``kind`` is DERIVED from role (trigger ⇒ ``CATALYST``, risk ⇒ ``CORPORATE_RISK``)
    and "severe" is DERIVED from ``score >= risk_block_severity`` — neither is a stored field, so a
    flag can never contradict the score (one truth; the dilution/breakdown convention).
    """

    role: Role
    grade: Grade | None = None  # trigger side only (a risk is ungraded — SignalEvent's contract)
    catalyst_type: CatalystType | None = None  # trigger side only
    score: float
    liveness_days: int  # the item's edge-persistence window, anchored on the filing date

    @model_validator(mode="after")
    def _policy_contract(self) -> "CorporateEventItemPolicy":
        # Mirror the SignalEvent taxonomy contract at CONFIG time, so a bad policy edit fails loud
        # at import rather than at the first fire: a trigger item must carry the grade + type its
        # fired event needs; a risk item must not carry a grade.
        if self.role is Role.ENTRY_TRIGGER and (self.grade is None or self.catalyst_type is None):
            raise ValueError("an entry_trigger item policy must carry grade and catalyst_type")
        if self.role is Role.RISK_SIGNAL and self.grade is not None:
            raise ValueError("a risk_signal item policy must not carry a grade")
        return self


class CallConfig(DomainModel):
    """Every ``TODO(operator)`` threshold lives here — never hardcoded in the assembler.

    Start conservative. These are starting defaults to calibrate against real calls
    (CALL_LOGIC §2/§3/§7), not claims of precision. The block-vs-penalize behavior of the
    risk-veto is fixed; only its severity threshold is calibrated.
    """

    # --- state transitions (§2) ---
    warming_min_entry_triggers: int = 1
    arming_requires_confirmation: bool = True  # two-key gate: conviction warms, confirmation arms
    # THEME_CONVICTION (M5b) is a Key-1 conviction so the existing co-location arming "just works"; it is
    # an operator-ratified, thesis-level FALLBACK (the weaker key, capped at flip). Distinguish it from
    # name-sourced convictions via `own_conviction_kinds` (below) — never a hardcoded {INSIDER, CATALYST}.
    # ACTIVIST_STAKE (Band 03 S5) joins as a name-sourced conviction: membership here is what wires
    # co-location arming + the own_conviction ranking with zero assembler edits — INERT until its
    # master switch (activist_stake_enabled below) flips, since no event of the kind enters the
    # stream while the detector no-ops.
    conviction_kinds: frozenset[Kind] = frozenset(
        {Kind.INSIDER, Kind.CATALYST, Kind.THEME_CONVICTION, Kind.ACTIVIST_STAKE}
    )
    confirmation_kinds: frozenset[Kind] = frozenset({Kind.TECHNICAL_BREAKOUT, Kind.LAGGARD})

    # --- risk-veto (§2) ---
    risk_block_severity: float = (
        0.7  # a fired risk signal with score >= this withholds the Armed call
    )

    # --- confidence (§7) ---
    single_detector_cap: float = 0.80  # a one-detector call never reads "high"
    risk_penalty_per_signal: float = 0.10  # confidence cut per active risk signal (scaled by score)

    # --- dilution severity (used by the dilution detector in M4a) ---
    # gross overhang (% of shares outstanding) that scales to the block severity; a convert's potential
    # dilution is scored against this (STARTING calibration). HIMS's ~6% lands well below the block.
    dilution_overhang_severe_pct: float = 25.0

    # --- insider_conviction (Key 1) — grade rule (§3); STARTING calibration, not precision ---
    # cohesion window: open-market buys within this many days of the most-recent buy count as ONE
    # cluster (so unrelated buys months apart aren't fused). ~7-30d is the standard insider-cluster
    # window in the literature; 30 is the conservative upper end (UNH's CEO+CFO+director buys span
    # 3 days, well within it). How long a fired cluster stays LIVE is the graded alpha-liveness window
    # below, not this window.
    insider_cluster_window_days: int = 30
    insider_min_usd: float = 10_000.0  # below this open-market total, no signal
    insider_core_min_distinct: int = 2
    insider_core_min_usd: float = 100_000.0
    # A single strong open-market buy by a senior insider also warms as CORE (e.g. HIMS: one
    # director, ~$1.2M). This high floor is the discriminator for the single-buy path.
    insider_strong_single_usd: float = 500_000.0
    insider_senior_role_keywords: frozenset[str] = frozenset(
        {"chief executive", "ceo", "chief financial", "cfo", "president", "director", "officer"}
    )
    # --- what counts as an OPEN-MARKET purchase (§3) — STARTING calibration ---
    # SEC transaction code 'P' is "open market OR PRIVATE purchase" — it is NOT a synonym for open-market.
    # A primary-market subscription (an IPO allocation, a PIPE, a private placement) by a pre-IPO holder /
    # crossover fund files as code P at the OFFER price, which sits BELOW the stock's public trading range
    # that day. Counting it as open-market insider conviction inflated the call (PBLS: RA Capital's $394M
    # IPO subscription at $20 vs a $29.65-$34.47 tape → a fake half-billion CORE). We recover the intent of
    # the Lakonishok-Lee open-market literature by cross-checking each buy's price against the security's
    # OWN EOD low for that day — the only structured field that separates a subscription from an open-market
    # buy (code P alone cannot). A buy priced this fraction or more BELOW the day's actual low did not
    # transact on the open market → it does NOT feed conviction (it STAYS in fact_insider_txn + the display
    # tape; only the CALL skips it). Recall-safe by construction (#9): no price bar for the day → KEEP
    # (never silently drop); a genuine open-market print is within the day's [low, high], so this cannot
    # exclude one — EXCEPT a name that did a REVERSE split between the buy and asof (the bar is restated UP;
    # a documented limitation tied to the deferred price-restatement slice). Directional on purpose: the
    # ABOVE-high side is NOT guarded here because a FORWARD split (common) restates the bar DOWN and would
    # false-reject a real pre-split buy — physically-impossible HIGH prices are caught by the ceiling below.
    insider_offmarket_below_low_frac: float = 0.10
    # A single insider "purchase" above this $ is not a personal open-market conviction buy: it is bad
    # source data (CNBX: a $100,000/share price → a $2 TRILLION row) or a strategic/primary block — either
    # way a physically-implausible number must never reach the call (#3). Set FAR above any real single
    # insider buy (the largest plausible in-universe is ~$300M) and above any split-inflated as-filed price,
    # so it catches only the absurd. Belt-and-suspenders to the below-low check (which misses garbage-HIGH
    # prices and any name with no price bar). Excluded rows are KEPT in history; only conviction skips them.
    insider_max_plausible_txn_usd: float = 2_000_000_000.0  # $2B
    # Conviction (insider) alpha-liveness window is GRADED by grade — the window over which the edge stays
    # live (a hard liveness window here, NOT an exponential 50%-decay point, so it is the FULL
    # edge-persistence horizon). The insider open-market-purchase literature (Lakonishok-Lee 2001;
    # Cohen-Malloy-Pomorski 2012, "Decoding Inside Information") measures abnormal returns over a
    # ~6-month horizon, with multi-insider "cluster" buys the most persistent (evidence to ~12mo). So a
    # CORE cluster ~= 180d (the conservative low end of 6-12mo; it doubles as the cap so a conviction
    # can't arm on a breakout >6mo later). A FLIP buy is fast / sentiment-driven / mean-reverting and
    # stays short. STARTING calibration — set on the alpha horizon, not to fit any one name.
    insider_core_alpha_liveness_days: int = 180
    insider_flip_alpha_liveness_days: int = 18
    # --- 10b5-1 planned-BUY weight (Band 03 S2c) — a DORMANT dial; 1.0 IS today ---
    # Scales a KEPT planned buy's (``aff_10b5_1 IS TRUE``) $ contribution to the cluster total; the buy
    # stays PRESENT for the distinct-insider / senior / anchor logic iff the weight is > 0 (0.0 = a
    # full screen — the buy drops from the survivor set entirely, so the anchor moves). There is NO
    # buy-side 10b5-1 screen today, so the 1.0 default reproduces today byte-for-byte (the golden
    # suite pins it — `x * 1.0` is IEEE-exact and the fold order is unchanged). Tri-state honored:
    # only an explicit True weighs; ``None`` (the pre-Dec-2022 norm) / ``False`` weigh 1.0 — unknown
    # is never asserted "planned" (#9). Flipping this is a later MEASURED, operator-signed config
    # decision (the insider_sell precedent), never a code change. Bounded [0, 1].
    insider_10b5_1_buy_weight: float = Field(default=1.0, ge=0.0, le=1.0)

    # --- catalyst_conviction (Key 1 for theme/catalyst theses, #10) — STARTING calibration ---
    # Catalyst liveness is the catalyst's relevance HORIZON, NOT grade-coupled (unlike insider, where a
    # weak buy is genuinely both low-conviction and fast-decaying). For a catalyst, grade sets categorical
    # call strength while liveness = how long the edge persists = the agreement's own term (period of
    # performance), taken from the structured record where present (e.g. an OKLO DOE OTA -> 2029-07-01),
    # else this default. A flat number misfit both ends (the OKLO OTA decayed too fast at flip-30; LEU's
    # multi-year HALEU missed co-location by 48d at core-365). A long standing horizon slightly overstates
    # conviction freshness — fine for now: arming still needs a co-located FRESH breakout, and the
    # decay-with-age refinement (CALL_LOGIC §7 roadmap) tempers it later.
    catalyst_default_horizon_days: int = 365  # fallback when no agreement term is published

    # --- theme_conviction (Key 1 FALLBACK for theme theses, M5b) — STARTING calibration ---
    # An operator-ratified, thesis-level theme conviction expires on its operator-set horizon (the
    # belief must be re-ratified to stay live — no zombie narratives). When the ratification carries no
    # explicit horizon_end, liveness runs to this default. ~12 months: a structural sector narrative is
    # slower than a single catalyst but should be re-ratified yearly. RECALIBRATION dial (upper bound /
    # default / re-ratification cadence). Liveness is decoupled from grade, exactly like a catalyst.
    theme_conviction_default_horizon_days: int = 365

    # --- revenue_acceleration (§2.2, Key 1) — the structural conviction behind the 5–10x breakout (R6/R8) ---
    # The detector fires when YoY revenue growth's quarter-over-quarter ACCELERATION flips strictly positive
    # after being <= 0 (a_q = g_q − g_{q-1} crosses up), with a FLOOR on g_q so a flip off a collapse into a
    # still-tiny base doesn't fire. 10% = a clearly-material growth rate (well above noise), the R6-ratified
    # floor. Grade is CORE, fixed in the detector (a fundamental inflection earns core — R6, not a config dial).
    revenue_accel_min_yoy: float = 0.10
    # A MAGNITUDE floor on the ACCELERATION itself (a_q = g_q − g_{q-1}, in YoY-growth points): below this a
    # positive flip is noise, not a re-acceleration. Distinct from revenue_accel_min_yoy, which floors the
    # LEVEL (g_q) — this floors the CHANGE (a_q). UNH's real 2025/2026 prints re-accelerated by +0.4pp then
    # +0.07pp (YoY 12.2% -> 12.3%) and fired a CORE conviction reading "+12% up from +12%" (measured 2026-08).
    # 2pp (0.02) is the STARTING floor: it cuts the sub-2pp noise tail (5/86 live sig-lab fires — UNH, LQDT,
    # BAH, RSI, LFST, all <= 1.5pp) while keeping every real inflection (HIMS +34pp, COIN +59pp, ...). A fixed
    # POINTS floor, deliberately scale-blind: a relative floor mishandles the g_prev <= 0 turns, which are the
    # strongest signals. Set 0.0 to restore the pre-floor "any positive flip fires" behavior. Calibration dial.
    revenue_accel_min_accel: float = 0.02
    # Liveness = how long the inflection edge stays relevant, anchored at the inflection quarter's FILED date,
    # DECOUPLED from grade (R8 — like a catalyst, unlike insider). 180d ≈ two quarters: the edge persists into
    # the next print, and a co-located breakout within ~6 months still arms on it. STARTING calibration.
    revenue_accel_alpha_liveness_days: int = 180

    # --- DOE/USASpending automated feed grade rule (#10 feed) — [PROPOSED], confirm at review ---
    # A binding DOE CONTRACT obligating at least this much = a `core` catalyst (contracted revenue is real
    # → build); a smaller contract, or any assistance / OTA / grant (not a contract), = `flip`
    # (provisional → small). Reproduces the operator's precedent (LEU's $317M HALEU contract = core; OKLO's
    # $0 pilot OTA = flip). Calibration dial — see signals/doe feed._derive_grade for the flagged
    # large-assistance edge case.
    doe_core_min_obligation_usd: float = 10_000_000.0

    # --- verdict hold-dimension (§4) — keyed on HORIZON, not kind ---
    # A conviction whose alpha-liveness horizon is >= this is "hold-and-build" (a small entry is a
    # STARTER); below it, the conviction is sentiment-grade -> "do not hold" (a small entry is FLIP-only).
    # This decouples the third job grade used to do (hold-or-not) onto the horizon, so a provisional but
    # long-horizon catalyst holds, a fast insider flip does not, and the next kind inherits correct
    # behavior from its own horizon. The data gap is clean (insider flip ~18d vs core/catalyst >=180d),
    # so a threshold in the gap is safe; calibration dial — tune at recalibration.
    conviction_hold_threshold_days: int = 90

    # --- M5 per-member ranking (the theme menu) — STARTING calibration ---
    # A theme ranks its armed members on a freshness BAND (runway) primary, grade within the band — kept on
    # separate axes, never fused into one score (the through-line). "Runway" here = the call's LIVENESS
    # horizon (exit_by - asof = the conviction hold clock, _clock over alpha_liveness_days), not company
    # cash runway or dilution pressure. An armed member with fewer than this many days of liveness runway
    # left is "lapsing-soon" and ranks below any "fresh" member regardless of grade (so a core arm about to
    # lapse doesn't headline over a long-runway starter). Calibration dial — Phase-1 recalibration tunes
    # how aggressively runway demotes grade; the default isn't load-bearing.
    headline_lapsing_soon_days: int = 45

    # --- volume_breakout / Key 2 (deliberately minimal placeholder) — STARTING calibration ---
    # A price breakout (new short-term closing high + a multi-day return thrust) is the entry; VOLUME
    # grades the confirmation: volume-backed (vol >= breakout_volume_mult x base avg) = full CORE-quality
    # confirmation; a momentum thrust on weak volume still ARMS but as a lower-grade (flip) confirmation
    # — reduced confidence + a volume-gap counter-case (see the assembler). Volume stays central.
    breakout_lookback_days: int = 120
    breakout_base_window: int = 8  # prior closes for the new-closing-high check
    breakout_min_base_bars: int = 5
    breakout_return_days: int = 10  # the momentum-thrust window
    breakout_min_return: float = 0.08  # close-to-close return over breakout_return_days
    breakout_volume_mult: float = 1.5  # vol >= mult x base avg => volume-backed (CORE) confirmation
    breakout_alpha_liveness_days: int = 10
    # --- §3.1 follow-through / hold quality (R13) — SHARPENS the volume_breakout SCORE (grade unchanged) ---
    # The breakout is real (it still FIRES) and its GRADE stays volume-based; the follow-through is a SCORE
    # input only (R13's rail). A weak close or a failed next-day hold is the false-breakout tell: it scores
    # LOWER (lower confidence — the rejected false breakout). A FRESH breakout with no next bar yet is NOT a
    # failed hold (unknown, not failed) so it isn't penalized; ABSENT high/low data never penalizes (#9 — a
    # missing field is not a weak close). The two penalties are score MULTIPLIER cuts (stack
    # multiplicatively). GRADING a weak-CLOSE breakout DOWN to flip is a GATED opt-in — see
    # breakout_weak_close_grade_down below (default OFF). STARTING calibration, not precision.
    breakout_close_strength_min: float = (
        0.70  # (close-low)/(high-low) >= this = a strong (top-of-range) close
    )
    breakout_weak_close_penalty: float = (
        0.30  # score multiplier cut when the breakout bar closes weak
    )
    breakout_failed_hold_penalty: float = (
        0.35  # score multiplier cut when the NEXT bar loses the breakout level
    )
    # §3.1 GRADE-DOWN — v1 DEFAULT ON (operator "go honest", 2026-08-15): a volume-backed breakout that
    # CLOSES WEAK (close_strength < breakout_close_strength_min) grades DOWN core -> flip (a weak-close
    # breakout is a quick-trade, not a structural hold). Measured on the lab: re-verdicts the UNH arm
    # CORE_ENTRY -> starter (still armed); demotes weak-close breakouts lab-wide (member ranking / counter-
    # case) with NO other thesis-level verdict change. Set False to disable.
    breakout_weak_close_grade_down: bool = True
    # Confidence ceiling for a STARTER — a call whose entry grade is flip because EITHER key is weak
    # (an unconfirmed/momentum-only breakout, OR a provisional conviction). An "enter small" call must
    # never read loud: it would invert inverse-loudness and out-rank steadier calls in the Decision
    # Queue. Capped here regardless of how strong the OTHER key is (the noisy-OR of the strong key alone
    # would otherwise float it high). Calibration dial.
    starter_confidence_cap: float = 0.55

    # --- breakout_52w / §2.3 (the STRUCTURAL 52-week breakout confirmation, R9) — SEPARATE detector ---
    # A fresh 52-WEEK closing high on real volume is the structural confirmation behind the 5–10x, distinct
    # from the 8-day momentum tool above (which stays intact). Grade is fixed CORE in the detector
    # (structural — R9, not a config dial, like revenue_acceleration's core). Reads EOD bars ONLY (no new
    # fact table). The RVOL denominator is the ~50-day average volume — the conventional breakout-volume
    # reference, NOT the 8-bar base the momentum tool uses. The min-base-bars gate REFUSES to assert a
    # "52-week high" on less than ~a year of tape (#9 — honest, never a fabricated year-high off 3 months);
    # a name with no volume on the bar declines the volume gate rather than faking a volume-backed breakout.
    # Liveness is 45d (vs the 8-day tool's 10d): a structural core breakout stays an entry window far
    # longer, so arm_until is long for a core hold. STARTING calibration, not precision.
    breakout_52w_lookback_days: int = (
        430  # calendar pull: the 52-week base (~252 bars) + the 45d scan window
    )
    breakout_52w_base_bars: int = (
        252  # trailing trading-bar window whose max close is the prior 52-week high
    )
    breakout_52w_min_base_bars: int = (
        245  # below this many PRIOR bars we can't honestly assert a 52w high -> decline
    )
    breakout_52w_vol_base_bars: int = (
        50  # RVOL denominator: the ~50-day average (breakout-vol reference)
    )
    breakout_52w_volume_mult: float = 1.5  # RVOL >= this x the base average = the volume gate (R9)
    breakout_52w_alpha_liveness_days: int = (
        45  # the CORE structural-breakout entry window (R9; vs the 8-day tool's 10d)
    )

    # --- laggard / §1.2 ROTATION (Key-2 sympathy confirmation) — STARTING calibration ---
    # A basket LEADER's live volume_breakout is the cue; a co-basket name LAGGING the basket's move but
    # still in a structural uptrend is the sympathy catch-up candidate. Because Kind.LAGGARD is a
    # confirmation kind (above), the assembler's existing co-location ARMS a laggard that carries an own
    # conviction but hasn't broken out itself (R3/R4) — the detector only ever supplies the confirmation
    # key, never the arm. Dials: the trailing return window whose basket MEDIAN sets the bar, the lag gap
    # below that median, the uptrend SMA gate, the flip sympathy arm window, and the price-pull lookback.
    laggard_return_days: int = 30  # trailing return (TRADING bars) compared across the basket
    laggard_lag_pts: float = (
        0.15  # B lags the basket median return by >= this (15 pts, as a fraction)
    )
    laggard_trend_sma_window: int = 200  # B's close must sit >= its 200d SMA (uptrend intact)
    laggard_alpha_liveness_days: int = (
        15  # the flip sympathy arm window, anchored to the leader breakout
    )
    laggard_lookback_days: int = (
        420  # calendar-day price pull: >= 200 trading bars for the SMA + return
    )

    # --- breakdown / §2.5 (core) + §3.3 (flip): the grade-aware structural DE-ARM (R10/R11/R12) ---
    # The exit half CALL_LOGIC §2 spec'd but never had a detector: a genuine breakdown de-arms an armed
    # entry — price logic in the DETECTOR, the grade-aware veto in the assembler (never price logic in the
    # assembler). Two detectors, one per grade of arm, each a RISK signal carrying which grade it de-arms
    # (SignalEvent.dearm_grade), so a flip-style fast breakdown can NEVER shake a core hold and a core
    # structural break de-arms the core hold even inside its arm_until window. STARTING calibration.
    #
    # CORE (R10): a close below the 200-day SMA (the long base) is the STRUCTURAL break — NEVER the first
    # pullback (a ~20% dip that HOLDS the 200d does not de-arm; the 200d level is exactly what filters a
    # shallow pullback from a structural break). Gated to a genuine break of an established uptrend: it
    # fires only when the most recent price-vs-200d cross was DOWNWARD (a name that ran up then broke), so
    # a chronic downtrend never below its 200d does not fire (honest loudness / #7).
    breakdown_core_sma_window: int = (
        200  # the long base (200d SMA) whose close-below is the core break
    )
    breakdown_core_min_bars: int = (
        200  # below this we cannot honestly compute a 200d SMA -> decline (#9)
    )
    breakdown_core_lookback_days: int = (
        430  # calendar pull: >= 200 trading bars for the SMA + the regime scan
    )
    # FLIP (R11): a close back below the 8-day breakout base the flip entry cleared — the fast de-arm. The
    # base + the freshness window REUSE the 8-day volume_breakout dials (breakout_base_window /
    # breakout_return_days / breakout_alpha_liveness_days / breakout_min_return / breakout_lookback_days):
    # the flip breakdown is that breakout's mirror, so it reads the SAME base the arm cleared.
    #
    # A fired breakdown's score — floored ABOVE the default risk_block_severity (0.7) so a genuine base
    # break clears the veto gate and de-arms; raising risk_block_severity above this tunes the de-arm OFF.
    # A close below the base de-arms (R10/R11), so the score is a fixed severity, not a depth gauge here.
    breakdown_severity: float = 0.8
    # v1 DEFAULT ON (operator "go honest", 2026-08-15): the structural de-arm — a close back below the base a
    # name broke out from de-arms it (the platform's missing EXIT half). The fire-date-at-downcross detectors +
    # the grade-aware, post-dating assembler veto POST-DATE the arm (a name never de-arms concurrent with its
    # own arm). Measured on the lab: prunes given-back breakouts with NO thesis-level verdict change at "today";
    # the one deeper re-verdict is a thesis armed ONLY on given-back flip breakouts (starter -> not_yet). Set
    # False to disable; replay.run's --breakdown-dearm / ALPHADECK_BREAKDOWN_DEARM still force it on for the backtest.
    breakdown_dearm_enabled: bool = True

    # --- insider_sell (RISK — Band 03 S1): clustered discretionary open-market selling ---
    # The risk-side mirror of insider_conviction: Form 4 code-S sales, screened (10b5-1 planned out,
    # unknown kept; issuer-self out; below-day-low set aside; implausible out — the identity/price
    # screens REUSE the buy side's dials below, one truth each), clustered on the buy side's
    # anchor-and-cohesion mechanics, scored bounded-and-capped. Counter-case + setup-strength ONLY:
    # never a veto, never a de-arm (operator decision 1, 2026-08-16). Every numeric default in this
    # block is a [PROPOSED] STARTING PRIOR — a shape argument, NOT a measurement: the sig-lab
    # distribution pass (Band 03 spec §6, a separate later step) finalizes the real values before the
    # operator flips the master switch. See docs/RECALIBRATION.md for the dial rows.
    #
    # MASTER SWITCH — DEFAULT ON (operator flip, 2026-08-19, the sig-lab pass; the corporate_risk
    # precedent): MEASURED safe on real prod data before the flip. The sig-lab off-vs-on pass (asof
    # 2026-08-19, prod, via the production read path call_for_thesis(record=False)) counted 58 fires
    # across 5 theses with ZERO arm-withholdings and ZERO de-arms — a pure counter-case + (situational)
    # setup-strength haircut, never a timing veto (density ~37% on the semi-heavy AI-Memory basket,
    # 5-13% elsewhere). That "cannot withhold" is guaranteed BY CONSTRUCTION, not just measured: the
    # score is capped at insider_sell_max_score (0.60) strictly BELOW risk_block_severity (0.70), so a
    # sell cluster can never cross the veto gate (the ceiling test pins the relation). Set False to
    # disable; replay.run's --insider-sell / ALPHADECK_INSIDER_SELL still force it on for the backtest.
    insider_sell_enabled: bool = True
    # Cohesion window: kept (discretionary open-market) sales within this many days of the most
    # recent one count as ONE episode of selling — the buy side's cluster logic. A SEPARATE dial
    # (not reusing insider_cluster_window_days) so risk calibration never couples to conviction
    # calibration; the two sides' dynamics may diverge. [PROPOSED] prior, lab-finalized.
    insider_sell_cluster_window_days: int = 30
    # Below this cluster $ total, no signal. Selling is ROUTINE (comp-driven): the buy side's $10k
    # floor would fire on nearly every name and violate honest loudness (a chip on every row carries
    # no information). Set high enough that firing marks the exception; THE LAB DISTRIBUTION PICKS
    # THE REAL VALUE — this is a prior on the shape, not a measured threshold.
    insider_sell_min_usd: float = 250_000.0
    # "Clustered" is the load-bearing word: the (weak) sell-side literature's signal is MULTIPLE
    # insiders selling together, not one. A single-seller path (the mirror of
    # insider_strong_single_usd) is deliberately OMITTED from v1 — one big sale is exactly the
    # many-reasons case (diversification/taxes/a house). [PROPOSED] prior.
    insider_sell_min_distinct: int = 2
    # Require >= 1 senior officer/director in the cluster: officer/director sales are what the
    # literature (weakly) measures. Reuses insider_senior_role_keywords — the DEFINITION of senior
    # is one truth, not a side-specific calibration. [PROPOSED] prior.
    insider_sell_require_senior: bool = True
    # Freshness window on the cluster ANCHOR (the most recent kept sale): risk freshness is
    # DETECTOR-enforced — the assembler never ages risk signals — and a sell cluster is the first
    # event-shaped risk that needs an explicit window (dilution self-limits via maturity_date;
    # breakdown is a recomputed price state). Sales' predictive horizon is weaker/shorter than
    # buys' 180d core window; ~one quarter is the conservative middle. [PROPOSED] prior.
    insider_sell_liveness_days: int = 90
    # THE CEILING — the central policy decision (operator decision 1, RATIFIED 2026-08-16): strictly
    # BELOW risk_block_severity (0.70), so a sell cluster can NEVER withhold an Armed call in v1 —
    # its maximum live effect is a ~0.06 setup-strength haircut (risk_penalty_per_signal x score) +
    # the counter-case + a card chip; a verdict cannot move. The ceiling test pins this relation so
    # lifting it later is a VISIBLE diff, made only with the lab's crossing-count measured first.
    insider_sell_max_score: float = 0.60
    # (reused, not new dials: insider_offmarket_below_low_frac + insider_max_plausible_txn_usd are
    # data-sanity/price truths and insider_senior_role_keywords is an identity truth — one place.)

    # --- corporate events (Band 03 S3): the 8-K item-code tape's POLICY MAP + two master switches ---
    # The SEC's own item-code taxonomy IS the classification (#3 — no NLP, no LLM on the fire path);
    # this map is where each code becomes a signal. Config-in-code, applied on READ (the
    # evidence/policy seam): the fact_corporate_event tape stores EVERY 8-K + items (#9), the two
    # detectors (signals/corporate_catalyst.py + signals/corporate_risk.py) filter to this cut, so
    # adding/retuning an item is a config edit that re-derives every call — zero re-ingest, zero
    # data repair. Every value below is a [PROPOSED] STARTING PRIOR — a shape argument, NOT a
    # measurement: the sig-lab distribution pass finalizes them before either switch flips. See
    # docs/CORPORATE_EVENTS.md + docs/RECALIBRATION.md for the dial rows.
    #
    # MASTER SWITCHES — ONE PER SIDE because the blast radii differ: the trigger side extends the
    # LIVE catalyst family (it can warm/arm), the risk side can withhold an arm on timing. Each
    # detector is registered but detect() no-ops until its switch is on; the pure score() functions
    # stay testable ungated. replay.run's --corporate-catalyst / --corporate-risk
    # (ALPHADECK_CORPORATE_CATALYST / _RISK) set each explicitly for the sig-lab backtest.
    # CATALYST — default OFF (the insider_sell precedent): parked until its own measured operator
    # decision; nothing reaches live cards unmeasured.
    # RISK — DEFAULT ON (operator flip, 2026-08-17; the breakdown_dearm precedent): validated safe
    # on real prod data BEFORE the flip — zero spurious arm-withholdings, zero recorded calls
    # change, so every existing golden stays byte-for-byte unchanged. Set False to disable.
    corporate_catalyst_enabled: bool = False
    corporate_risk_enabled: bool = True
    # The v1 item cut (operator-confirmed 2026-08-17; the gold-doc §10-3a proposal):
    # TRIGGERS (kind=CATALYST — Key-1 conviction, co-location arming + own-conviction ranking ride
    # the existing conviction_kinds membership):
    #   1.01 material definitive agreement -> type=contract, CORE [PROPOSED] (a material contract is
    #        the narrative landing in the business — the "core = capital-committed-or-structural"
    #        line), score 0.9 (= catalyst_conviction's _CORE_SCORE parity), liveness 365d (= the
    #        catalyst default horizon: the item code carries no agreement term, so the family
    #        fallback applies).
    #   5.02 officer/director change -> type=personnel, FLIP [PROPOSED] (direction is ambiguous — a
    #        departure and a marquee hire file identically; the evidence link does the work), score
    #        0.5 (= _FLIP_SCORE parity), liveness 90d (personnel attention decays fast).
    # RISKS (kind=CORPORATE_RISK — grade-blind, no dearm_grade; freshness DETECTOR-enforced since
    # the assembler never ages risks):
    #   3.01 listing-deficiency notice -> 0.50 moderate (counter-case + confidence haircut,
    #        sub-veto), liveness 180d (~ a real exchange cure period).
    #   4.01 auditor change -> 0.50 moderate, liveness 180d (a governance flag with a long tail).
    #   4.02 non-reliance / restatement -> 0.80 SEVERE (>= risk_block_severity 0.70 — withholds the
    #        NAME on timing; = breakdown_severity parity: a "don't trust the financials" event is at
    #        least as severe as a structural base break), liveness 365d (trust stays broken until
    #        restated financials land — roughly an annual cycle).
    #   1.03 bankruptcy -> 0.90 SEVERE (the tape's loudest deterministic red flag; below dilution's
    #        0.95 emission ceiling), liveness 365d.
    # Items OUTSIDE this cut (2.02/7.01/8.01 cadence, 3.03/5.03 reverse-split, ...) are STORED on
    # the tape but fire nothing — their deferred detectors are config additions here, no re-ingest.
    corporate_event_items: dict[str, CorporateEventItemPolicy] = {
        "1.01": CorporateEventItemPolicy(
            role=Role.ENTRY_TRIGGER,
            grade=Grade.CORE,
            catalyst_type=CatalystType.CONTRACT,
            score=0.9,
            liveness_days=365,
        ),
        "5.02": CorporateEventItemPolicy(
            role=Role.ENTRY_TRIGGER,
            grade=Grade.FLIP,
            catalyst_type=CatalystType.PERSONNEL,
            score=0.5,
            liveness_days=90,
        ),
        "3.01": CorporateEventItemPolicy(role=Role.RISK_SIGNAL, score=0.50, liveness_days=180),
        "4.01": CorporateEventItemPolicy(role=Role.RISK_SIGNAL, score=0.50, liveness_days=180),
        "4.02": CorporateEventItemPolicy(role=Role.RISK_SIGNAL, score=0.80, liveness_days=365),
        "1.03": CorporateEventItemPolicy(role=Role.RISK_SIGNAL, score=0.90, liveness_days=365),
    }

    # --- share_creep (RISK — Band 03 S4): quarterly share-count creep / ATM detection ---
    # The slow-motion dilution signal: an at-the-market program quietly draining into the tape shows up
    # as shares outstanding rising quarter-over-quarter with no loud raise event — the realized-dilution
    # counterpart to dilution_clock's POTENTIAL convert overhang (both emit kind=DILUTION_RISK — two
    # lenses on one phenomenon; operator-confirmed reuse, 2026-08-17). Reads the fact_fundamentals S4
    # shares series (three XBRL concepts stored; the detector walks a fixed availability ladder and
    # never mixes concepts inside one computation). Grade-blind like dilution (grade=None, no
    # dearm_grade — not a de-arm); freshness DETECTOR-enforced (the assembler never ages risks). Every
    # numeric default below is a [PROPOSED] STARTING PRIOR — a shape argument, NOT a measurement: the
    # sig-lab distribution pass finalizes the real values before the operator flips the master switch.
    # See docs/RECALIBRATION.md for the dial rows.
    #
    # MASTER SWITCH — DEFAULT ON (operator flip, 2026-08-19, the sig-lab pass; the corporate_risk /
    # insider_sell precedent): MEASURED safe on real prod data. The sig-lab off-vs-on pass (asof
    # 2026-08-19, prod, via the production read path call_for_thesis(record=False)) counted 61 fires
    # across 5 theses with ZERO arm-withholdings — real dilution (e.g. UEC +13.4% QoQ shares), a
    # counter-case + at most a small (~0.05) setup-strength haircut, never a timing veto. Guaranteed
    # BY CONSTRUCTION: share_creep_score (0.50) sits strictly BELOW risk_block_severity (0.70), so
    # creep can never withhold an arm (the ceiling test pins the relation). Set False to disable;
    # replay.run's --share-creep / ALPHADECK_SHARE_CREEP still force it on for the sig-lab backtest.
    share_creep_enabled: bool = True
    # The trailing window, in consecutive QoQ pairs (window+1 quarterly points, each adjacent gap a real
    # quarter): the SUSTAINED-drip prior — a persistent drip is the ATM tell; a one-off jump is usually
    # an explained discrete raise (spec fork 1, operator-confirmed). 4 pairs ≈ a trailing year of
    # quarterly prints. [PROPOSED] prior, lab-finalized.
    share_creep_window_quarters: int = 4
    # The cumulative % rise over the window that fires. Anchored on what the phenomena MEAN in absolute
    # terms (the recalibration discipline, never fit to a name): routine SBC dilution runs ~2-5%/yr; an
    # ACTIVE ATM materially drains ~10-30%/yr. 10% over the trailing window = clearly above routine comp
    # creep. (UEC, the measured real instance, sits at +13.4% trailing-4; MRAM's ~5%/yr SBC-scale drip
    # correctly does NOT fire — honest loudness.) [PROPOSED] prior, lab-finalized.
    share_creep_cum_min_pct: float = 10.0
    # The single-pair absurdity ceiling: a quarter-over-quarter rise at/above this is NOT creep — it is
    # a forward split, a recap/merger, or an XBRL scale artifact (measured rampant on the real basket:
    # a literal 1-share row, thousands-vs-units errors reading +100,000%). The detector DECLINES the
    # window rather than mislabeling a structural event as ATM drainage. [PROPOSED] prior.
    share_creep_pair_ceiling_pct: float = 100.0
    # Freshness window on the series ANCHOR (the newest point's ``filed`` date) — detector-enforced,
    # the insider_sell precedent. ~One filing cycle + the 10-K lag tolerance: a quarterly series whose
    # newest print is older than this has gone dark (delinquent/stopped filer) and asserts nothing
    # about TODAY's issuance. [PROPOSED] prior.
    share_creep_liveness_days: int = 150
    # Flat moderate score (= the S3 moderate items' 0.50), strictly BELOW risk_block_severity (0.70):
    # slow dilution is a persistent drag on the thesis — counter-case + a setup-strength haircut,
    # sub-veto, NEVER a hard timing veto like a bankruptcy. The ceiling test pins this relation so
    # lifting it later is a VISIBLE diff, made only with the lab's crossing-count measured first.
    # [PROPOSED] prior, lab-finalized.
    share_creep_score: float = 0.50

    # --- activist_stake (Key 1 — Band 03 S5): the SC 13D activist-stake conviction ---
    # A new SC 13D filed ABOUT a basket member = an outside party crossed 5% WITH INTENT to
    # influence — a rare, deliberate, capital-committed act by an informed party (the Brav/Jiang
    # activist event-study literature measures persistent post-FILING abnormal returns). The FORM
    # TYPE is the entire fire decision (#3 — no NLP, no cover parse to fire): 13D-family ORIGINALS
    # fire a CORE Key-1 conviction (grade fixed in the detector, the R6/R9 structural-grade
    # precedent — "core = a rare, deliberate capital commitment", the line 1.01 failed); 13G
    # (passive — mostly index funds crossing 5%, measured ~2 originals/yr/name on real subjects) is
    # STORED on the fact_activist_stake tape but fires NOTHING in v1 (the S3 1.01-flood lesson);
    # amendments (/A) ride the tape + provenance but NEVER re-anchor a fire (direction-blind:
    # increase, sell-down, and exit all file as /A — the measured CMPS 13D/A reporting 4.96%, a
    # sell-down BELOW 5%, must not fire a fresh CORE). Like every conviction it WARMS; arming still
    # needs a co-located confirmation (the two-key gate), and 13D originals are rare per name
    # (measured: 1 per 6 years on the richest real subject), so it cannot flood. Every numeric
    # default below is a [PROPOSED] STARTING PRIOR — a shape argument, NOT a measurement: the
    # sig-lab distribution pass finalizes the real values before the operator flips the master
    # switch. See docs/ACTIVIST_STAKE.md + docs/RECALIBRATION.md for the dial rows.
    #
    # MASTER SWITCH — default OFF (the insider_sell / corporate-pair / share_creep precedent): the
    # detector is registered but detect() no-ops until enabled, so nothing reaches live cards
    # unmeasured and every existing golden is byte-for-byte unchanged; the pure score() stays
    # testable ungated. replay.run's --activist-stake / ALPHADECK_ACTIVIST_STAKE force it on for
    # the sig-lab backtest. The operator flips this only after seeing the lab table.
    activist_stake_enabled: bool = False
    # Score parity with catalyst_conviction's _CORE_SCORE (0.9): a filed >5%-with-intent stake is a
    # binding, verifiable commitment — the theme analog the catalyst family already prices at 0.9.
    # [PROPOSED] prior, lab-finalized.
    activist_13d_score: float = 0.9
    # Liveness anchored on the ORIGINAL 13D's filed date. The activist literature measures the
    # abnormal-return window in months from the filing; 180d = the conservative low end, and parity
    # with the insider CORE window (insider_core_alpha_liveness_days) — the same "informed party
    # committed capital" economics. >= conviction_hold_threshold_days (90), so an armed 13D entry
    # correctly reads hold-and-build, not flip-only. [PROPOSED] prior, lab-finalized.
    activist_13d_liveness_days: int = 180

    # --- Workbench scoring — pip-bucketing cutoffs (Slice 3) — PRE-REGISTERED, not fit to the seed ---
    # The 0-4 "pip" meters score each basket name from the point-in-time facts (re-derived on read). Every
    # cutoff is grounded in what the metric MEANS in absolute terms (the discipline the recalibration pass
    # holds) — NEVER reverse-engineered from the (n=4, clustered) seed names. See docs/RECALIBRATION.md.
    # Each tuple is the inclusive lower bound for the 1/2/3/4-pip bars.
    #
    # PURITY = % of revenue/business that IS the theme (exposure CONCENTRATION). 80 = the conventional
    # "pure-play" bar; 50 = majority; 25 = a clearly-material segment (well above the ~10% SEC reportable-
    # segment-materiality floor); 10 = the floor of relevance. NOT discounted for pre-revenue (runway +
    # dilution carry funding risk). Seed lands SEPARATELY as a check: LEU 77% -> 3 (a 23% non-nuclear
    # segment, honestly not a pure-play), the three 100% names -> 4.
    purity_pip_pct: tuple[float, float, float, float] = (10.0, 25.0, 50.0, 80.0)
    #
    # RUNWAY = months of funding at the current burn (cash / (quarterly_burn / 3)); a FUNDING-RISK gauge.
    # Grounded in the financing cycle: raises typically land ~12-18mo out; < 6mo signals distress; >= 24mo
    # is ~two raise cycles of cushion. A cash-generative name (burn <= 0) reads max. For a revenue name a
    # single quarter's operating cash use is working-capital noise; the >=24mo -> 4-pip cap keeps the pip
    # honest now (LEU's 160mo -> 4), and the structural revenue-vs-burn refinement is filed (RECALIBRATION).
    runway_pip_months: tuple[float, float, float, float] = (6.0, 12.0, 18.0, 24.0)
    #
    # CATALYSTS = density of LIVE catalysts (live = valid_from within the catalyst's liveness horizon — the
    # SAME window the back half uses). Grounded in count + the existing core-vs-flip grade: a single binding
    # (core) catalyst = 2 pips, denser than a single provisional (flip) one = 1 pip; >= multi -> 3 pips;
    # >= dense -> 4 pips (a rich surface). Deliberately strict — a lone DOE award is not a dense surface.
    catalyst_pip_multi_count: int = 2  # >= this many live catalysts -> 3 pips
    catalyst_pip_dense_count: int = 3  # >= this many live catalysts -> 4 pips (a rich surface)
    #
    # DILUTION = convert-overhang PRESSURE (more pips = more RISK — opposite polarity to the others; the
    # visual distinction is a Slice-4 display concern). Bars are the RAW overhang % (the shared
    # dilution_clock.overhang_pct, NOT backed out of the clamped risk severity); the 4-pip "severe" bar
    # REUSES dilution_overhang_severe_pct (25.0) above — one place. A name with converts below the 1-pip bar
    # reads 0 pips (a clean low reading); NO convert fact reads "-" (not 0 — no fake zeros).
    dilution_pip_pct: tuple[float, float, float] = (
        2.0,
        8.0,
        15.0,
    )  # 1/2/3-pip; 4-pip = the severe dial
    #
    # NOTE the archetype-recommendation cutoffs are GONE (Business-Type M1): the size-derived
    # leader/high_beta/lotto tier was never adopted (486/494 members unset, measured 2026-08) — the
    # market-cap FIGURE stays first-class on every scored row, and "what a name IS" now derives from
    # the SIC maps in securities/business_type/ (operator-editable, master-level).
    #
    # The uniform CASH-RUNWAY basis is a RATIFY-TIME convention (applied when cash_usd is ratified), NOT
    # enforced in the scorer (which trusts cash_usd as ratified). Documented here as the single canonical
    # statement the ratify CLIs + docs reference.
    cash_runway_basis: str = (
        "cash + equivalents + all marketable securities (current and noncurrent)"
    )

    # The LLM-seam operational dials (llm_model / llm_max_tokens / llm_timeout_s + the decompose_* siblings)
    # moved to `domain/settings.py` (the env-overridable Settings), with their rationale comments verbatim:
    # they are OPERATIONAL knobs an operator should change with an env edit, not trust-validated call-engine
    # tuning. Everything above stays here, deliberately NOT env-overridable — the file boundary is the line.

    @property
    def own_conviction_kinds(self) -> frozenset[Kind]:
        """Name-sourced ("own") convictions — the theme conviction is a basket-level FALLBACK, not "own".
        Used at the three M5b decision sites (broadcast eligibility / the `is_own` ranking axis / the
        `theme_armed` flag) so a future conviction kind added to `conviction_kinds` inherits "own"
        automatically — the through-line (factor on the property, never a hardcoded kind literal).
        """
        return self.conviction_kinds - {Kind.THEME_CONVICTION}


DEFAULT_CONFIG = CallConfig()


class ExtractorConfig(DomainModel):
    """The scoring-fact extractor's detector dials (Slice hybrid-1) — kept here, never hardcoded in the
    extractor, under the same no-magic-number discipline as ``CallConfig`` (a behavioral guard proves a
    changed dial changes a flag). Starting calibrations against the seed oracle, not claims of precision.
    """

    # YTD detector: a cash-flow period whose span exceeds this is a year-to-date column, not a quarter, so
    # the quarter must be DERIVED (YTD - prior period). A clean fiscal quarter is ~89-92 days. (NNE.)
    quarterly_span_max_days: int = 100
    # one-time detector: a NON-ROUTINE operating line (accrued/settlement — NOT routine working capital like
    # inventory/AR/trade-AP, and NOT a non-cash add-back) whose magnitude is at least this fraction of
    # |operating cash use| is anomalous -> flag for ratification (the operator decides whether to back it
    # out). SMR's ENTRA1 settlement (the AP-and-accrued line) is ~84%; routine working-capital swings (LEU's
    # inventory is 139%!) are excluded by category, not by size, so they don't trip it.
    one_time_line_fraction: float = 0.70
    # corroborating keyword-class for the located passage (NEVER names a specific item like ENTRA1) — a
    # one-time line whose label matches strengthens the flag and anchors the passage.
    one_time_keywords: tuple[str, ...] = (
        "settlement",
        "milestone",
        "litigation",
        "impairment",
        "nonrecurring",
        "one-time",
        "contribution",
    )

    # --- annual-cover shares (the dark names, Retrieval Slice 1 — ingest/edgar/annual_shares.py) ---
    # The window AFTER the located cover instruction searched for the count. The instruction sentence
    # ends and the count follows within a sentence or a short per-class table; measured sufficient
    # across every readable 20-F/40-F cover in a real basket (measured; PR #221).
    annual_cover_segment_chars: int = 900
    # A chosen count older than this (days, aged against the request's `today`) wears `stale-cover` —
    # mirroring the FE's ">~6mo old" `.wb-stale-shares` badge (`staleSharesMonths`), so the backend
    # flag and the UI badge tell ONE staleness story rather than two.
    annual_stale_cover_days: int = 183
    # The implausibility floor: a WINNING value below this is emitted WITH `implausible-count`, never
    # suppressed (recall #9 — a suppressed value is worse than a flagged one). QNTM's companyfacts
    # `dei` claims 12 shares against a cover of 3,887,729. PROPOSED dial (spec §9), not a measured fact.
    annual_implausible_floor_shares: float = 1_000.0
    # The ADS-ratio absurdity ceiling (spec §10.3): a parsed ratio above this is treated as UNREAD
    # (suppress), never applied. Real ratios in the measured universe run 1..120; deep-discount ADRs
    # can genuinely reach several hundred (a 400:1 was measured mid-conflict), so the ceiling sits an
    # order above the observed range rather than hugging it.
    annual_ads_ratio_max: int = 1000

    # --- annual-statements runway (Retrieval Slice A — ingest/edgar/annual_runway.py) ---
    # Stale-runway threshold: an annual filer's burn reading is inherently up to ~a year old between
    # filings, so the shares-style ~6-month bar would cry wolf on EVERY name (honest loudness — a flag
    # true of every row carries no information). 550 days ≈ one annual cycle + a filing-lag allowance:
    # only a name whose newest operating-cash reading is older than even the annual rhythm allows wears
    # `stale-runway` (a delinquent or stopped filer). PROPOSED dial (spec §Open dials), not measured.
    annual_stale_runway_days: int = 550
    # --- exhibit statement source (Retrieval Slice A-2 — ingest/edgar/statement_sources.py) ---
    # The cost-thread cap on how many EX-99 exhibit documents one filing may FETCH while hunting the
    # financial statements (a 40-F carries 90-110 documents; the EX-99 family alone ran 7-8 on every
    # measured name, most of them tiny certifications). Candidates are ordered unknown-size-first then
    # LARGEST-first before capping (a statements exhibit is a big document; certifications are tiny),
    # so the cap sheds the boilerplate tail, never the statements — and every skipped candidate is
    # LOGGED (a bounded scan is never a silent one). Each fetch is cache-first under the immutable
    # forms/ prefix, so the cost is paid once per accession, ever.
    exhibit_scan_max: int = 6


DEFAULT_EXTRACTOR_CONFIG = ExtractorConfig()
