"""Band 03 S4 — the share-count-creep (ATM detection) RISK detector: pure score on the REAL measured
UEC instance, the decline paths (gap / stale / artifact-ceiling / non-drip / sub-floor / one-off
jump), the concept ladder (availability-first, no verdict shopping), the master switch, the score
ceiling pin, and the assembler composition (moderate rides the Armed call with a haircut — zero
assembler edits, the existing role+score path)."""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

from calls.assembler import assemble_call
from domain.config import DEFAULT_CONFIG
from domain.enums import Kind, Role, State
from signals import share_creep
from tests.calls.factories import ASOF, SID, breakout_event, insider_event, make_thesis


def _row(metric: str, period_end: date, value: float, filed: date, accn: str) -> dict:
    """One as-of fact_fundamentals row as the PIT view returns it (latest version per period_end)."""
    return {
        "metric_key": metric,
        "period_end": period_end,
        "value": value,
        "valid_from": filed,
        "accession": accn,
    }


# --- the REAL golden instance (test-honesty: measured, not fitted) ------------------------------------
# Uranium Energy Corp (CIK 1334933) — us-gaap:CommonStockSharesOutstanding, the latest version per
# period_end as measured from the prod cron's own companyfacts cache on 2026-08-17. UEC's ATM is
# on-file: S-3ASR shelves + 424B5 prospectus supplements (accession 0001437749-24-038144, 2024-12-20,
# is titled "At-the-Market Offering"), ZERO Item-3.02 8-Ks since 2024 — the quiet drip. 23 consecutive
# rising quarters overall; the trailing five points below rise +13.4% with no step above +6.5%.
_UEC_ASOF = date(2026, 8, 17)
_UEC_POINTS = [
    (date(2025, 4, 30), 435_027_962.0, date(2025, 6, 2), "0001437749-25-019033"),
    (date(2025, 7, 31), 454_015_855.0, date(2026, 6, 9), "0001437749-26-019889"),
    (date(2025, 10, 31), 483_209_225.0, date(2025, 12, 10), "0001437749-25-037296"),
    (date(2026, 1, 31), 489_270_002.0, date(2026, 3, 10), "0001437749-26-007413"),
    (date(2026, 4, 30), 493_317_899.0, date(2026, 6, 9), "0001437749-26-019889"),
]


def _uec_rows() -> list[dict]:
    return [_row("shares_out_xbrl", e, v, f, a) for e, v, f, a in _UEC_POINTS]


def _drip_rows(
    metric: str = "shares_out_xbrl",
    values: tuple[float, ...] = (1_000_000, 1_040_000, 1_080_000, 1_120_000, 1_160_000),
    last_end: date = date(2026, 3, 31),
    last_filed: date = date(2026, 5, 20),
) -> list[dict]:
    """A synthetic 5-point quarterly drip ENDING at last_end/last_filed (fresh vs the factories' ASOF
    2026-06-02): default +4%/quarter -> cum +16%, a clean fire. Ends are ~92d apart; each point's
    filed trails its end by the same lag so knowability stays ordered."""
    rows = []
    n = len(values)
    for i, v in enumerate(values):
        end = last_end - timedelta(days=92 * (n - 1 - i))
        filed = last_filed - timedelta(days=92 * (n - 1 - i))
        rows.append(_row(metric, end, v, filed, f"acc-{i}"))
    return rows


def test_fires_on_the_uec_sustained_drip():
    e = share_creep.score(_uec_rows(), SID, _UEC_ASOF)
    assert e is not None and e.fired
    assert e.role is Role.RISK_SIGNAL and e.kind is Kind.DILUTION_RISK  # the REUSED dilution family
    assert e.grade is None and e.dearm_grade is None  # grade-blind like dilution; NOT a de-arm
    assert e.alpha_liveness_days is None  # freshness is detector-enforced, not assembler-aged
    assert e.score == DEFAULT_CONFIG.share_creep_score
    assert e.asof == date(2026, 6, 9)  # anchored at the NEWEST point's filed date (knowability, #1)
    assert "+13.4% over 4 quarters" in e.label and "balance-sheet series" in e.label
    assert "largest single-quarter step +6.4%" in e.label
    assert "counter-case input, below the timing-veto threshold" in e.label
    # provenance: every window point rides, the anchor carries the computation summary (#6)
    assert [p.ref for p in e.provenance] == [a for _, _, _, a in _UEC_POINTS]
    anchor = e.provenance[-1].detail
    assert anchor["cum_pct"] == round((493_317_899 / 435_027_962 - 1) * 100, 4)
    assert anchor["max_pair_pct"] == round((483_209_225 / 454_015_855 - 1) * 100, 4)
    assert anchor["window_quarters"] == 4
    assert e.provenance[0].detail["shares"] == 435_027_962.0


def test_declines_on_a_gap_in_the_series():
    """A missing quarter breaks the consecutive chain — the detector declines across the hole rather
    than comparing non-adjacent periods (the _prior_quarter honesty, #9)."""
    rows = [r for r in _uec_rows() if r["period_end"] != date(2025, 10, 31)]
    assert share_creep.score(rows, SID, _UEC_ASOF) is None


def test_declines_when_the_series_goes_dark_inclusive_at_the_horizon():
    """Freshness is DETECTOR-enforced on the newest point's filed date: live inclusively AT the
    liveness horizon, gone one day past — a stopped filer's stale series asserts nothing about today.
    """
    horizon = date(2026, 6, 9) + timedelta(days=DEFAULT_CONFIG.share_creep_liveness_days)
    assert share_creep.score(_uec_rows(), SID, horizon) is not None
    assert share_creep.score(_uec_rows(), SID, horizon + timedelta(days=1)) is None


def test_declines_on_a_split_or_scale_artifact_pair():
    """A single-quarter step at/above the ceiling is a forward split / recap / XBRL scale artifact
    (measured on the real basket: a literal 1-share row; thousands-vs-units errors) — a structural
    event, NOT ATM creep; the window declines rather than mislabeling it."""
    rows = _drip_rows(values=(1_000_000, 1_010_000, 1_020_000_000, 1_030_000_000, 1_040_000_000))
    assert share_creep.score(rows, SID, ASOF) is None


def test_declines_on_a_shrinking_quarter():
    """A negative pair (a buyback / reverse split) breaks the drip — not persistent issuance."""
    rows = _drip_rows(values=(1_000_000, 1_050_000, 990_000, 1_100_000, 1_200_000))
    assert share_creep.score(rows, SID, ASOF) is None


def test_declines_on_a_one_off_jump_with_flat_neighbors():
    """The discrete explained-raise shape (fork 1, sustained prior): one +30% quarter amid flat ones
    clears the cumulative floor but is NOT a drip — strict positivity declines it."""
    rows = _drip_rows(values=(1_000_000, 1_000_000, 1_300_000, 1_300_000, 1_300_000))
    assert share_creep.score(rows, SID, ASOF) is None


def test_declines_below_the_cumulative_floor():
    """Routine SBC-scale creep (~5%/yr — the measured MRAM shape) stays below the 10% floor: firing on
    every comp-diluting name would be a chip on every row (honest loudness)."""
    rows = _drip_rows(values=(1_000_000, 1_012_000, 1_024_000, 1_036_000, 1_049_000))
    assert share_creep.score(rows, SID, ASOF) is None


def test_declines_with_no_share_rows_and_ignores_other_metrics():
    assert share_creep.score([], SID, ASOF) is None
    revenue_only = [_row("revenue", date(2026, 3, 31), 5e8, date(2026, 5, 20), "acc-r")]
    assert share_creep.score(revenue_only, SID, ASOF) is None


def test_ladder_prefers_balance_sheet_then_falls_back_to_cover():
    """Availability ladder: the balance-sheet concept wins when usable; a sparse/stale gaap series
    (the measured SMR shape — one point from 2022) falls through to the dei cover series. Concepts are
    never mixed inside one computation."""
    both = _drip_rows("shares_out_xbrl") + _drip_rows(
        "shares_out_cover_xbrl", values=(2_000_000, 2_100_000, 2_200_000, 2_300_000, 2_400_000)
    )
    e = share_creep.score(both, SID, ASOF)
    assert e is not None and "balance-sheet series" in e.label
    assert all(p.detail["metric"] == "shares_out_xbrl" for p in e.provenance)

    sparse_gaap = [
        _row("shares_out_xbrl", date(2022, 5, 3), 44_000_000, date(2022, 5, 12), "acc-s")
    ]
    fallback = sparse_gaap + _drip_rows("shares_out_cover_xbrl")
    e2 = share_creep.score(fallback, SID, ASOF)
    assert e2 is not None and "cover-page series" in e2.label
    assert all(p.detail["metric"] == "shares_out_cover_xbrl" for p in e2.provenance)


def test_no_concept_shopping_past_an_honest_no_creep():
    """The FIRST usable series gives the verdict: a computable balance-sheet window below the floor is
    an honest 'no material creep' — the detector must NOT shop the cover series for a bigger number.
    """
    quiet_gaap = _drip_rows(values=(1_000_000, 1_012_000, 1_024_000, 1_036_000, 1_049_000))
    loud_dei = _drip_rows(
        "shares_out_cover_xbrl", values=(1_000_000, 1_050_000, 1_100_000, 1_150_000, 1_200_000)
    )
    assert share_creep.score(quiet_gaap + loud_dei, SID, ASOF) is None


def test_master_switch_now_defaults_on_but_still_gates_detect():
    """The switch is now LIVE by default (share_creep_enabled=True, ratified on the 2026-08-19 sig-lab
    pass: 61 fires / 5 theses / 0 withholds) — detect() FIRES under DEFAULT_CONFIG. Explicitly OFF,
    detect no-ops WITHOUT reading the pit (the throwing accessor proves it never runs). The pure score
    ignores the switch entirely (the insider_sell / corporate-pair precedent)."""
    assert (
        DEFAULT_CONFIG.share_creep_enabled is True
    )  # the ratified LIVE default (sig-lab 2026-08-19)

    off = DEFAULT_CONFIG.model_copy(update={"share_creep_enabled": False})
    pit_off = SimpleNamespace(
        fundamentals_facts=lambda sid: (_ for _ in ()).throw(AssertionError("pit read while OFF"))
    )
    assert share_creep.detect(pit_off, SID, ASOF, off) is None  # OFF -> no-op, never reads the pit

    assert share_creep.score(_drip_rows(), SID, ASOF, DEFAULT_CONFIG) is not None  # ungated

    pit_on = SimpleNamespace(fundamentals_facts=lambda sid: _drip_rows())
    e = share_creep.detect(pit_on, SID, ASOF, DEFAULT_CONFIG)  # LIVE default -> fires
    assert e is not None and e.kind is Kind.DILUTION_RISK


def test_score_ceiling_pins_below_the_timing_veto():
    """THE CEILING (operator-confirmed sub-veto v1): the flat moderate score sits strictly below
    risk_block_severity, so share creep can never withhold an Armed call — lifting it later is a
    VISIBLE diff, made only with the lab's crossing-count measured first."""
    assert DEFAULT_CONFIG.share_creep_score < DEFAULT_CONFIG.risk_block_severity
    e = share_creep.score(_drip_rows(), SID, ASOF)
    assert e is not None and e.score == DEFAULT_CONFIG.share_creep_score


# --- assembler composition: zero kind branches, the existing role+score path --------------------------


def test_moderate_share_creep_rides_the_armed_call_with_a_haircut():
    """A moderate creep never blocks: the call still ARMS, the risk rides the card (counter-case
    surface), and setup strength wears the per-risk haircut."""
    risk = share_creep.score(_drip_rows(), SID, ASOF)
    assert risk is not None
    clean = assemble_call(make_thesis(), [insider_event(), breakout_event()], ASOF, DEFAULT_CONFIG)
    carded = assemble_call(
        make_thesis(), [insider_event(), breakout_event(), risk], ASOF, DEFAULT_CONFIG
    )
    assert carded.state is State.ARMED
    assert any(r.kind is Kind.DILUTION_RISK for r in carded.risk_signals)
    assert carded.confidence is not None and carded.confidence < clean.confidence
