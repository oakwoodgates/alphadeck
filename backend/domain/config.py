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
    dilution_block_runway_months: float = 6.0
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
    # weak buy is genuinely both low-conviction and fast-decaying). For a catalyst, grade sets entry SIZE
    # (provisional -> flip -> starter) while liveness = how long the edge persists = the agreement's own
    # term (period of performance), taken from the structured record where present (e.g. an OKLO DOE OTA
    # -> 2029-07-01), else this default. A flat number misfit both ends (the OKLO OTA decayed too fast at
    # flip-30; LEU's multi-year HALEU missed co-location by 48d at core-365). A long standing horizon
    # slightly overstates conviction freshness — fine for now: arming still needs a co-located FRESH
    # breakout, and the decay-with-age refinement (CALL_LOGIC §7 roadmap) tempers it later.
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
    # horizon (exit_by - asof = the conviction hold clock, _clock over alpha_liveness_days) — NOT the
    # dilution cash-runway risk dial (dilution_block_runway_months). An armed member with fewer than this
    # many days of liveness runway left is "lapsing-soon" and ranks below any "fresh" member regardless of
    # grade (so a core arm about to lapse doesn't headline over a long-runway starter). Calibration dial —
    # Phase-1 recalibration tunes how aggressively runway demotes grade; the default isn't load-bearing.
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

    @property
    def own_conviction_kinds(self) -> frozenset[Kind]:
        """Name-sourced ("own") convictions — the theme conviction is a basket-level FALLBACK, not "own".
        Used at the three M5b decision sites (broadcast eligibility / the `is_own` ranking axis / the
        `theme_armed` flag) so a future conviction kind added to `conviction_kinds` inherits "own"
        automatically — the through-line (factor on the property, never a hardcoded kind literal).
        """
        return self.conviction_kinds - {Kind.THEME_CONVICTION}


DEFAULT_CONFIG = CallConfig()
