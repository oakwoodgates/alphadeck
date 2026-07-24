"""Annual-statements cash + runway (Retrieval Slice A) — one test per hazard, each tied to a REAL name.

THIS MODULE IS THE ORACLE. Expected values were verified against the filings themselves (and
cross-checked against companyfacts where the two share a date) on 2026-07-23; the rules they encode
are canon in ``docs/WORKBENCH_EXTRACTION.md`` ("The annual-statements runway path"). The fixtures
under ``fixtures/sec_extractor/annual/`` are REAL filing text, trimmed with every trim VERIFIED to
reproduce the full-document extraction result (values, flags, note, passages) before commit; the
``cf-runway-*.json`` fixtures are the real companyfacts ifrs-full rows as served on the measurement
date. Both sides are pinned — frozen input, frozen expectation. OFFLINE — no network, no DB.

WHERE THIS SUITE DEVIATES FROM THE SLICE'S ANSWER KEY (each deviation is the doc-first rule applied,
verified against the filings + agreeing companyfacts cross-checks — not a redesign):

- SHMD is CASH-GENERATIVE, not burning: its FY2025 statement shows operating cash flow +€1,250
  thousand; the key classified it burning off companyfacts' lagged FY2024 (−€2,578K).
- EVO is BURNING, not generative: its FY2025 statement shows −€9,179K; the key read companyfacts'
  lagged FY2024 (+€18,220K).
- MMTIF's financial statements ARE in its main 20-F document (balance sheet "Cash 23(a) $250,148",
  cash-flow total "(1,323,007)" — both matching companyfacts exactly), so it emits a finite runway
  rather than deferring; the key's "financials in an exhibit" claim came from a probe whose OCF label
  pattern required a "Net " prefix MMTIF's statement does not use.
- PBM's headline "~1.8yr" is reproduced from the FY2025 20-F + the still-current companyfacts 6-K
  interim (the state the key measured); PBM's FY2026 20-F (filed 2026-06-22) has since superseded it
  live (~11 months at the annual FY2026 burn) — the newer-annual-wins direction is pinned separately.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from domain.extraction import Tier
from ingest.edgar.annual_runway import annual_facts_for_security, extract_annual_runway

_FX = Path(__file__).resolve().parent.parent / "fixtures" / "sec_extractor" / "annual"
_TODAY = date(2026, 7, 23)  # the measurement date — staleness in the notes is deterministic


def _text(fname: str) -> str:
    return (_FX / fname).read_text(encoding="utf-8")


def _cf(name: str) -> dict:
    return json.loads((_FX / f"cf-runway-{name}.json").read_text(encoding="utf-8"))


def _one(
    fname: str,
    *,
    cf: dict | None = None,
    report_date: date,
    form: str = "20-F",
    today: date = _TODAY,
):
    facts, reason = extract_annual_runway(
        cf,
        _text(fname),
        annual_ref=f"https://sec.gov/{fname}",
        annual_form=form,
        report_date=report_date,
        today=today,
    )
    assert len(facts) <= 1
    return (facts[0] if facts else None), reason


def _months(f) -> float:
    """The runway meter's read: months = cash / (quarterly_burn / 3)."""
    assert f.cash_usd is not None and f.quarterly_burn_usd and f.quarterly_burn_usd > 0
    return f.cash_usd / (f.quarterly_burn_usd / 3.0)


# ---------------------------------------------------------------------------------------------------------
# the acceptance samples — filing-derived, exact inputs, meter-months asserted as ranges
# ---------------------------------------------------------------------------------------------------------


def test_ghrs_runway_full_year():
    """GHRS (the spec's loop-proof sample): cash $246,251K / OCF −$43,552K over the full year →
    ~68 months (~5.6-5.7y). FLAG, both statement passages present, and the `$'000` scale applied
    (the F4 exemplar: the raw statement figures are thousands)."""
    f, reason = _one("GHRS-20f-fin.txt", cf=_cf("GHRS"), report_date=date(2025, 12, 31))
    assert reason is None and f is not None
    assert f.tier is Tier.FLAG and f.source == "annual-statements"
    assert f.cash_usd == 246_251_000.0  # 246,251 × 1,000 — the scale header applied
    assert 66.0 < _months(f) < 69.0  # ≈ 67.6 months ≈ 5.6y
    kinds = {p.kind for p in f.located_passages}
    assert kinds == {"cash-flow", "balance-sheet"}  # BOTH rows — the passage contract
    assert "$’000" in f.note  # the scale marker rides the note
    assert f.event_date == date(2025, 12, 31)
    # fixture integrity (the trim trap): the multi-column rows + the scale header must survive trims
    text = _text("GHRS-20f-fin.txt")
    assert "$’000" in text
    assert "( 43,552 ) ( 42,285 ) ( 33,336 )" in text  # the OCF total, three fiscal years
    assert "Cash and cash equivalents 8 246,251 100,791" in text  # note-ref + two years


def test_ghrs_net_total_beats_the_pre_finance_subtotal():
    """GHRS's statement carries BOTH "Cash flows used in operating activities (54,973)" (a subtotal
    before finance items) and "Net cash used in operating activities (43,552)" (the true total). The
    Net-prefixed row must win — the subtotal is a confidently-wrong number one row up."""
    text = _text("GHRS-20f-fin.txt")
    assert "( 54,973 )" in text  # the trap is really in the fixture
    f, _ = _one("GHRS-20f-fin.txt", cf=_cf("GHRS"), report_date=date(2025, 12, 31))
    assert f.quarterly_burn_usd == pytest.approx(43_552_000 / 364 * (365.25 / 4))
    assert "54,973" not in f.note


def test_thousands_unit_scale_applied():
    """F4 — a `$'000` statement value is ×1,000: GHRS's printed 246,251 emerges as $246,251,000, and
    the burn side scales identically (a mixed-scale ratio would corrupt the runway silently)."""
    f, _ = _one("GHRS-20f-fin.txt", cf=_cf("GHRS"), report_date=date(2025, 12, 31))
    assert f.cash_usd == 246_251_000.0
    assert (
        f.quarterly_burn_usd is not None and f.quarterly_burn_usd > 10_000_000
    )  # K-scaled, not raw
    assert "unit-scale-unread" not in f.flags


def test_pbm_interim_span_is_normalized():
    """F1 — THE core computation, in the exact state the answer key measured: PBM's FY2025 20-F (the
    then-latest annual, still immutable on EDGAR) + companyfacts serving a FRESHER six-month 6-K
    interim (cash $7,149,985 / OCF −$2,029,147 for 2025-04-01 → 2025-09-30). The interim wins on
    later-as-of and MUST be normalized by its actual ~182-day span → ~21 months (~1.8y). Treating the
    6-month figure as ANNUAL gives ~3.5y (the trap the key named); treating it as a QUARTER gives
    ~10.5 months. Both wrong readings are pinned impossible."""
    f, reason = _one("PBM-20f-fy2025-fin.txt", cf=_cf("PBM"), report_date=date(2025, 3, 31))
    assert reason is None and f is not None
    assert f.cash_usd == 7_149_985.0  # the interim balance — fresher than the FY2025 statement's
    assert f.event_date == date(2025, 9, 30)  # the burn period's own end (valid-time)
    months = _months(f)
    assert months == pytest.approx(21.1, rel=0.02)  # ≈ 1.8y — the span-normalized read
    wrong_as_annual = 7_149_985 / (2_029_147 / 12)  # ≈ 42.3 months (~3.5y) — the F1 trap
    wrong_as_quarter = 7_149_985 / (2_029_147 / 3)  # ≈ 10.6 months
    assert abs(months - wrong_as_annual) > 15
    assert abs(months - wrong_as_quarter) > 5
    assert "2025-04-01 → 2025-09-30" in f.note  # the span is stated where ratified


def test_pbm_newer_annual_supersedes_the_interim_live():
    """The other direction of later-as-of, measured on the CURRENT corpus: PBM's FY2026 20-F (filed
    2026-06-22, after the key's inputs) carries an annual statement (−$8,143,007 over the year; cash
    $7,444,763) whose period end BEATS the same 6-K interim — the live runway is ~11 months, and the
    key's 1.8y is a superseded snapshot. Pinned so the suite documents WHY the live number moved.
    (PBM also pins the no-"Net"-prefix label: its total row reads "Cash used in operating activities",
    and full-dollar statements whose scale marker sits outside the statement region wear
    `unit-scale-unread` with figures offered AS PRINTED — stated, never guessed.)"""
    f, reason = _one("PBM-20f-fy2026-fin.txt", cf=_cf("PBM"), report_date=date(2026, 3, 31))
    assert reason is None and f is not None
    assert f.cash_usd == 7_444_763.0  # the FY2026 statement beats the older interim
    assert f.event_date == date(2026, 3, 31)
    assert 10.5 < _months(f) < 11.5
    assert "unit-scale-unread" in f.flags  # full-dollar statements; no scale marker in the region


def test_prtc_reads_current_column_not_prior():
    """F2+F3 — PRTC's statements show 2-3 fiscal years side by side (`252,470 280,641`;
    `(85,131) (134,369) (105,917)`) while companyfacts still serves FY2024 (280,641K / −134,369K — a
    full-year lag). The CURRENT column wins BOTH quantities → ~2.9-3.0y, not the ~2.1y the lagged
    inputs give. The prior-column values must appear nowhere in the chosen figures."""
    f, reason = _one("PRTC-20f-fin.txt", cf=_cf("PRTC"), report_date=date(2025, 12, 31))
    assert reason is None and f is not None
    assert f.cash_usd == 252_470_000.0  # NEVER 280,641,000 (the prior column / lagged companyfacts)
    assert f.quarterly_burn_usd == pytest.approx(85_131_000 / 365 * (365.25 / 4))
    assert 34.5 < _months(f) < 36.5  # ≈ 35.6 months ≈ 2.96y
    assert (
        "-134,369,000" in f.note and "behind the filing" in f.note
    )  # the lag is named, not silent
    # fixture integrity: the multi-column regions the test exists to guard must survive any re-trim
    text = _text("PRTC-20f-fin.txt")
    assert "Cash and cash equivalents 24 252,470 280,641" in text
    assert "( 85,131 ) ( 134,369 ) ( 105,917 )" in text
    assert "$000s" in text  # PRTC's thousands marker variant


def test_cmnd_fresher_interim_wins_and_is_span_normalized():
    """THE test that the rule is later-as-of-wins per quantity, not prefer-the-document: CMND's
    companyfacts carries a THREE-month 6-K interim (−$2,084,949, 2025-11-01 → 2026-01-31; cash
    $9,257,766) that is FRESHER than its 2025-10-31 20-F statement (−$4,734,498 FY). The interim wins
    and is normalized by its 91-day span → ~13.3 months; the statement's own figure is stated as not
    used. (Reading the 3-month figure as ANNUAL would give ~4.4y — 4× wrong.)"""
    f, reason = _one("CMND-20f-fin.txt", cf=_cf("CMND"), report_date=date(2025, 10, 31))
    assert reason is None and f is not None
    assert f.cash_usd == 9_257_766.0 and f.event_date == date(2026, 1, 31)
    assert 12.7 < _months(f) < 13.9  # ≈ 13.3 months
    wrong_as_annual = 9_257_766 / (2_084_949 / 12)  # ≈ 53 months (~4.4y)
    assert abs(_months(f) - wrong_as_annual) > 30
    assert "-4,734,498" in f.note and "OLDER and was not used" in f.note


def test_qntm_cf_span_match_pins_the_report_period():
    """A real filer titled its ANNUAL cash-flow statement "For the nine months ended September 30,
    2025" (a copy-paste error) — the period phrase lies. The span comes instead from the companyfacts
    row that reproduces the statement's own value AND ends at the period of report (2025-01-01 →
    2025-12-31): reproducible arithmetic beats a mislabeled phrase. The end-date pin also defeats a
    COINCIDENTALLY-equal old interim row in the same companyfacts (a live collision this suite
    preserves in the fixture). QNTM: cash $1,905,357 / −$8,237,012 annual → ~2.8 months."""
    text = _text("QNTM-20f-fin.txt")
    assert "nine months ended" in text.lower()  # the lying phrase stays in the fixture
    f, reason = _one("QNTM-20f-fin.txt", cf=_cf("QNTM"), report_date=date(2025, 12, 31))
    assert reason is None and f is not None
    assert f.cash_usd == 1_905_357.0
    assert "2025-01-01 → 2025-12-31" in f.note  # the matched span, not the phrase's 274 days
    assert 2.6 < _months(f) < 3.0  # ≈ 2.77 — nine-months normalization would read ~2.1


def test_generative_name_marks_cash_generative():
    """F7 — the sign IS the state: SHMD's FY2025 statement shows operating cash flow +€1,250K →
    cash-generative. No runway months exist anywhere on the fact; the meter reads burn <= 0 as the
    cash-generative top pip. ALSO the doc-first sign-flip: companyfacts still serves FY2024's
    NEGATIVE −€2,578K (the reading that mis-filed SHMD as burning in the answer key) — the fresher
    statement wins the sign. And the zero-width-character hazard: SHMD's real filing text is littered
    with U+200B inside labels and numbers; the fixture preserves them."""
    assert "​" in _text("SHMD-20f-fin.txt")  # the invisible hazard stays pinned
    f, reason = _one("SHMD-20f-fin.txt", cf=_cf("SHMD"), report_date=date(2025, 12, 31))
    assert reason is None and f is not None
    assert f.quarterly_burn_usd is not None and f.quarterly_burn_usd < 0  # generating, not burning
    assert f.cash_usd == 1_574_000.0  # "in € thousand" applied
    assert "CASH-GENERATIVE" in f.note and "no runway applies" in f.note
    assert "-2,578,000" in f.note  # the lagged negative is named, not silently dropped


def test_tsm_ascending_years_and_convenience_column():
    """F3 at full strength: TSM's statements run ASCENDING (2023 2024 2025) with a FOURTH
    convenience-US$ column on the cash-flow statement and a third on the balance sheet, in NT$
    MILLIONS with decimal values. The current column is the THIRD of four (OCF +NT$2,274,975.6M) and
    the SECOND of three (cash NT$2,767,856.4M) — never the prior year, never the US$ column."""
    f, reason = _one("TSM-20f-fin.txt", cf=_cf("TSM"), report_date=date(2025, 12, 31))
    assert reason is None and f is not None
    assert f.cash_usd == pytest.approx(2_767_856.4e6)  # column 2 of 3 — NOT 2,127,627.0 (2024)
    assert f.quarterly_burn_usd is not None and f.quarterly_burn_usd < 0  # generative
    assert abs(f.quarterly_burn_usd) == pytest.approx(2_274_975.6e6 / 365 * (365.25 / 4), rel=1e-6)
    assert "88,232.6" not in f.note and "72,520.7" not in f.note  # the US$ columns never leak
    text = _text("TSM-20f-fin.txt")
    assert "2023 2024 2025" in text  # ascending header retained
    assert "72,520.7" in text  # the convenience column retained — the trap stays live


def test_hyft_bare_cash_label_and_first_net_total():
    """Two real shapes on one filer: HYFT's balance sheet labels the row bare "Cash" ("Current assets
    Cash 16 11,348 10,665" — the full 'Cash and cash equivalents' label appears only in prose), and
    its cash-flow statement carries a SECOND "Net cash used in operating activities" total under
    "Cash from discontinued operations" — the FIRST (continuing-operations) total must win. CAD
    thousands via "(Expressed in Canadian dollars) (in thousands)" — the thousands marker outranks
    the bare currency declaration."""
    f, reason = _one("HYFT-20f-fin.txt", report_date=date(2026, 4, 30))
    assert reason is None and f is not None
    assert f.cash_usd == 11_348_000.0  # the bare-Cash row, current column, ×1,000
    assert f.quarterly_burn_usd == pytest.approx(12_463_000 / 365 * (365.25 / 4))
    assert 10.4 < _months(f) < 11.4  # ≈ 10.9 months — 777 (discontinued) never the basis
    text = _text("HYFT-20f-fin.txt")
    assert "Cash 16 11,348 10,665" in text
    assert "Net cash used in operating activities 777" in text  # the second-total trap retained


def test_xtlb_small_ungrouped_values_read_correctly():
    """XTLB's summary statement prints TWO SMALL ADJACENT VALUES with no thousands grouping ("Cash
    and cash equivalents 76 371" — $76K current, $371K prior, in thousands): a space-grouping read
    would fuse them into 76,371 and a naive parse would take 371 (the PRIOR year). The
    column-count-vs-headers rule reads [76, 371] and picks 76 → ~0.9 months of runway (a real
    deep-distress reading, agreeing with the filing)."""
    f, reason = _one("XTLB-20f-fin.txt", report_date=date(2025, 12, 31))
    assert reason is None and f is not None
    assert f.cash_usd == 76_000.0  # NOT 371,000 (prior column), NOT 76,371,000 (fused)
    assert 0.7 < _months(f) < 1.1
    assert "Cash and cash equivalents 76 371" in _text("XTLB-20f-fin.txt")


def test_mmtif_statements_are_in_doc_and_emit():
    """DEVIATION FROM THE ANSWER KEY, verified: MMTIF's main 20-F document DOES carry its financial
    statements — the balance sheet ("Cash 23(a) $ 250,148") and the cash-flow total ("Cash flows used
    in operating activities ( 1,323,007 )"), BOTH reproducing companyfacts exactly. The key deferred
    MMTIF as exhibit-only because its probe's label pattern demanded a "Net " prefix. Recall is
    sacred (#9): a readable name must emit, and it does — ~2.3 months, deep distress, honestly."""
    f, reason = _one("MMTIF-20f-fin.txt", cf=_cf("MMTIF"), report_date=date(2025, 10, 31))
    assert reason is None and f is not None
    assert f.cash_usd == 250_148.0
    assert 2.0 < _months(f) < 2.6
    assert "companyfacts agrees" in f.note  # both quantities cross-checked, same date, same value


def test_evo_burning_despite_lagged_generative_companyfacts():
    """The sign-flip in the OTHER direction (the mirror of SHMD): EVO's FY2025 statement shows
    −€9,179K (k€ scale — the "in k€" marker variant) while companyfacts still serves FY2024's
    POSITIVE +€18,220K. Doc-first: EVO is BURNING with a finite (huge) runway, not cash-generative
    as the key filed it."""
    f, reason = _one("EVO-20f-fin.txt", cf=_cf("EVO"), report_date=date(2025, 12, 31))
    assert reason is None and f is not None
    assert f.quarterly_burn_usd is not None and f.quarterly_burn_usd > 0  # burning
    assert f.cash_usd == 418_517_000.0
    assert _months(f) > 400  # a barely-burning balance-sheet giant — honest, if enormous
    assert "18,220,000" in f.note  # the lagged positive is named


def test_statement_currency_rides_the_fact_for_the_native_label():
    """DISPLAY-ONLY currency LABEL (Slice A follow-up): the statement's already-detected ISO currency is
    carried onto the emitted fact so the FE can label cash/burn in the filer's OWN currency (``cash NT$
    …``) instead of a misread ``$`` (TSM's cash is NT$2,767,856,400,000 — ~US$88B, not $2.77T). It is
    NEVER converted and NEVER a scoring input — the value is untouched. A USD-stated annual filer carries
    ``"USD"`` (the FE then renders the plain ``$``, same as every domestic 10-Q/10-K name → None).
    """
    tsm, _ = _one("TSM-20f-fin.txt", cf=_cf("TSM"), report_date=date(2025, 12, 31))
    assert tsm is not None and tsm.statement_currency == "TWD"
    assert tsm.cash_usd == pytest.approx(
        2_767_856.4e6
    )  # the value is UNTOUCHED — only the label moves
    hyft, _ = _one("HYFT-20f-fin.txt", report_date=date(2026, 4, 30))
    assert hyft is not None and hyft.statement_currency == "CAD"
    ghrs, _ = _one("GHRS-20f-fin.txt", cf=_cf("GHRS"), report_date=date(2025, 12, 31))
    assert ghrs is not None and ghrs.statement_currency == "USD"


# ---------------------------------------------------------------------------------------------------------
# the empty states — a state is named, never silently blank and never a wrong number
# ---------------------------------------------------------------------------------------------------------


def test_exhibit_40f_name_defers_honestly():
    """CRDL and DRUG (40-F/MJDS): the main document is a wrapper — no statement rows exist in it —
    and companyfacts says they BURN. No passage → no fact; the runway leg defers with the distinct
    honest reason (`financials-in-exhibit`), never a companyfacts-only number."""
    for fname, cfname, rd in (
        ("CRDL-40f.txt", "CRDL", date(2025, 12, 31)),
        ("DRUG-40f.txt", "DRUG", date(2025, 9, 30)),
    ):
        f, reason = _one(fname, cf=_cf(cfname), report_date=rd, form="40-F")
        assert f is None, fname
        assert reason == "financials-in-exhibit", fname


def test_wrapper_generative_name_marks_state_without_a_fact():
    """OGI (40-F wrapper, POSITIVE companyfacts operating cash flow): no statements to locate, so no
    fact can exist (no passage → no fact) — but the STATE is knowable and named: `cash-generative`,
    not a deferral. A generative name must never read as "runway data missing"."""
    f, reason = _one("OGI-40f.txt", cf=_cf("OGI"), report_date=date(2025, 9, 30), form="40-F")
    assert f is None and reason == "cash-generative"


def test_unknowable_sign_is_statements_not_located():
    """A wrapper document with NO companyfacts at all: neither rows nor a sign — the honest state is
    `statements-not-located` (unread, not empty), distinct from both the deferral and the generative
    mark."""
    f, reason = _one("CRDL-40f.txt", cf=None, report_date=date(2025, 12, 31), form="40-F")
    assert f is None and reason == "statements-not-located"


# ---------------------------------------------------------------------------------------------------------
# the structural bound + no-lookahead + the wrapper
# ---------------------------------------------------------------------------------------------------------


def test_runway_path_never_returns_auto():
    """STRUCTURAL: the pre-fill tier is unreachable from this path — the token appears NOWHERE in the
    module source (the annual-shares / display-signals idiom: unable by construction, not by
    discipline), and every fact any fixture emits is FLAG."""
    import ingest.edgar.annual_runway as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "AUTO" not in src  # the token itself — not just `Tier.AUTO`
    for fname, cf, rd in (
        ("GHRS-20f-fin.txt", _cf("GHRS"), date(2025, 12, 31)),
        ("PBM-20f-fy2025-fin.txt", _cf("PBM"), date(2025, 3, 31)),
        ("SHMD-20f-fin.txt", _cf("SHMD"), date(2025, 12, 31)),
        ("TSM-20f-fin.txt", _cf("TSM"), date(2025, 12, 31)),
        ("HYFT-20f-fin.txt", None, date(2026, 4, 30)),
    ):
        f, _ = _one(fname, cf=cf, report_date=rd)
        assert f is not None and f.tier is Tier.FLAG, fname


def test_every_fact_carries_both_statement_passages():
    """The passage contract (no passage → no fact, per row): every emitted fact carries BOTH the
    balance-sheet cash row and the cash-flow OCF row as located passages, each with its offset
    recorded (audit, never filtered on)."""
    for fname, cf, rd in (
        ("GHRS-20f-fin.txt", _cf("GHRS"), date(2025, 12, 31)),
        ("PRTC-20f-fin.txt", _cf("PRTC"), date(2025, 12, 31)),
        ("CMND-20f-fin.txt", _cf("CMND"), date(2025, 10, 31)),
        ("MMTIF-20f-fin.txt", _cf("MMTIF"), date(2025, 10, 31)),
        ("XTLB-20f-fin.txt", None, date(2025, 12, 31)),
    ):
        f, _ = _one(fname, cf=cf, report_date=rd)
        assert f is not None, fname
        kinds = [p.kind for p in f.located_passages]
        assert sorted(kinds) == ["balance-sheet", "cash-flow"], fname
        assert all(p.offset is not None for p in f.located_passages), fname


def test_no_lookahead_time_is_a_parameter():
    """The same inputs at two different `today`s yield IDENTICAL values — time only ages the
    staleness flag. GHRS read on the measurement date is fresh; the same filing read years later
    wears `stale-runway` (and nothing else changes)."""
    fresh, _ = _one("GHRS-20f-fin.txt", cf=_cf("GHRS"), report_date=date(2025, 12, 31))
    late, _ = _one(
        "GHRS-20f-fin.txt", cf=_cf("GHRS"), report_date=date(2025, 12, 31), today=date(2030, 1, 1)
    )
    assert fresh is not None and late is not None
    assert (fresh.cash_usd, fresh.quarterly_burn_usd) == (late.cash_usd, late.quarterly_burn_usd)
    assert "stale-runway" not in fresh.flags
    assert "stale-runway" in late.flags


def test_stale_runway_fires_only_past_the_annual_cycle():
    """Honest loudness: annual data is inherently up to ~a year old, so the flag must not cry wolf on
    every name — it fires only past `annual_stale_runway_days` (~1.5 annual cycles). XTXXF (last
    annual filing FY2022 — a stopped filer, ~3.5 years stale) wears it; GHRS (current) does not.
    XTXXF also pins the tie-goes-to-the-document rule: its statement value (+CAD 1,319,111, full
    dollars, "Expressed in Canadian dollars") matches companyfacts at the SAME end date — generative,
    read from the statement, with the cross-check agreeing."""
    ghrs, _ = _one("GHRS-20f-fin.txt", cf=_cf("GHRS"), report_date=date(2025, 12, 31))
    assert "stale-runway" not in ghrs.flags
    xtxxf, reason = _one("XTXXF-20f-fin.txt", cf=_cf("XTXXF"), report_date=date(2022, 12, 31))
    assert reason is None and xtxxf is not None
    assert "stale-runway" in xtxxf.flags
    assert xtxxf.quarterly_burn_usd is not None and xtxxf.quarterly_burn_usd < 0  # generative
    assert xtxxf.event_date == date(2022, 12, 31)  # the value's own valid-time, however old
    assert (
        "2022-01-01 → 2022-12-31" in xtxxf.note
    )  # tie at the period end → the statement + cf span


class _Cf404(Exception):
    """A duck-typed httpx.HTTPStatusError: `.response.status_code == 404` (companyfacts absent)."""

    def __init__(self):
        super().__init__("404 companyfacts")
        self.response = type("R", (), {"status_code": 404})()


class _FakeClient:
    """A cache-shaped fake: submissions + per-document texts keyed by primary-doc basename; the
    companyfacts fetch 404s unless a dict is given (the annual-shares suite's idiom)."""

    def __init__(self, *, subs: dict, texts: dict[str, str] | None = None, cf: dict | None = None):
        self._subs, self._texts, self._cf = subs, texts or {}, cf

    def get_json(self, url: str, cache_key: str) -> dict:
        if cache_key.startswith("submissions/"):
            return self._subs
        if cache_key.startswith("companyfacts/"):
            if self._cf is None:
                raise _Cf404()
            return self._cf
        raise AssertionError(f"unexpected get_json: {cache_key}")

    def get_text(self, url: str, cache_key: str) -> str:
        doc = cache_key.rsplit("/", 1)[-1]
        if doc not in self._texts:
            raise AssertionError(f"unexpected document fetch: {cache_key}")
        return self._texts[doc]


def test_wrapper_fetches_once_and_merges_shares_with_runway_reason():
    """``annual_facts_for_security`` (the route's dark-name entry): ONE submissions read + ONE
    document fetch feed BOTH extractors — CRDL yields its Slice-1 shares candidate PLUS the runway
    leg's honest deferral, in one ``ExtractionResult``. The fake raises on any unexpected fetch, so a
    second document pull fails the test by construction."""
    subs = json.loads((_FX / "subs-CRDL.json").read_text(encoding="utf-8"))
    client = _FakeClient(
        subs=subs,
        texts={"crdl-20251231x40f.htm": _text("CRDL-40f.txt")},
        cf=_cf("CRDL"),
    )
    res = annual_facts_for_security(client, 1702123, today=_TODAY)
    assert [f.fact_type for f in res.facts] == ["shares_outstanding"]
    assert res.facts[0].tier is Tier.FLAG
    assert res.empty_reason is None  # facts are non-empty — the Slice-1 semantics hold
    assert res.runway_empty_reason == "financials-in-exhibit"


def test_wrapper_no_annual_filing_keeps_slice1_shape():
    """A name with no 20-F/40-F at all keeps the Slice-1 empty state — and the runway reason stays
    unset (the top-level reason covers the whole annual path)."""
    subs = json.loads((_FX / "subs-SKHY.json").read_text(encoding="utf-8"))
    res = annual_facts_for_security(_FakeClient(subs=subs), 1234, today=_TODAY)
    assert res.facts == [] and res.empty_reason == "no-annual-filing"
    assert res.runway_empty_reason is None
