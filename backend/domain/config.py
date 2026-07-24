from __future__ import annotations

from domain.base import DomainModel
from domain.enums import Kind


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
    conviction_kinds: frozenset[Kind] = frozenset(
        {Kind.INSIDER, Kind.CATALYST, Kind.THEME_CONVICTION}
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
    # Confidence ceiling for a STARTER — a call whose entry grade is flip because EITHER key is weak
    # (an unconfirmed/momentum-only breakout, OR a provisional conviction). An "enter small" call must
    # never read loud: it would invert inverse-loudness and out-rank steadier calls in the Decision
    # Queue. Capped here regardless of how strong the OTHER key is (the noisy-OR of the strong key alone
    # would otherwise float it high). Calibration dial.
    starter_confidence_cap: float = 0.55

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
    # ARCHETYPE recommendation (Slice 4, #10) — a DERIVED DEFAULT the operator overrides, never auto-applied.
    # The role a name plays is largely a SIZE + PURITY read: a large-cap bellwether LEADS; a small/mid name
    # AMPLIFIES (high beta); a micro/nano name is the binary LOTTO; an off-thesis (low-purity) name is ADJACENT
    # regardless of size. `shovel` (a picks-and-shovels value-chain role) and `fund` (an ETF) are RELATIONAL /
    # structural — NOT derivable from these figures — so the rule ABSTAINS on them (the operator's call), and
    # it abstains entirely when there's no market cap yet (no facts → the operator's default stands). Cutoffs
    # are conventional cap tiers, grounded in what they MEAN, NOT fit to the seed. NOTE the limitation: "leader"
    # is ABSOLUTE here (a large-cap), not relative-to-the-basket — a small-cap theme gets no derived leader, the
    # operator sets it (consistent with abstention-as-a-feature; a relative-leader refinement is the operator's
    # to call). The operator's edge — tune here.
    archetype_adjacent_max_purity_pip: int = (
        1  # purity pips <= this (off-thesis / peripheral) -> adjacent
    )
    archetype_leader_min_cap_usd: float = 10e9  # >= large-cap -> leader (the bellwether)
    archetype_high_beta_min_cap_usd: float = (
        5e8  # >= this -> high_beta; below -> lotto (micro / nano)
    )
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


DEFAULT_EXTRACTOR_CONFIG = ExtractorConfig()
