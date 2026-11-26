"""The split list's auto-confirm rule (split plan S1) — two independent sources must agree (operator
decision 2026-10-20). Each verdict below pairs a REAL Massive.com row with what the REAL SEC filing states
(``tests/fixtures/splits``); the rule's edges with no real instance are stated on direct inputs."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from fractions import Fraction

from domain.splits import (
    PAST_RATIO_ONLY,
    SESSION_TOLERANCE,
    STATUS_ANNOUNCED,
    STATUS_CONFIRMED,
    STATUS_NEEDS_REVIEW,
    STATUS_REJECTED,
    VIA_AUTO,
    PolygonEvidence,
    SecEvidence,
    date_agreement,
    decide,
    distribution_reason,
    price_factor,
    ratio_label,
    session_distance,
)
from ingest.edgar.split_filing import extract_split, extract_split_screened
from tests.split_fixtures import documents, polygon_row, v1_row

ASOF = date(2026, 10, 20)


def sec(name: str, *, form: str, filed: str, accession: str | None = None) -> SecEvidence:
    """The SEC evidence for a real fixture filing, as the backfill builds it."""
    x = extract_split(documents(name))
    return SecEvidence(
        accession=accession or name.split(".")[0],
        form=form,
        filed=date.fromisoformat(filed),
        url="https://www.sec.gov/Archives/edgar/data/x/y/z-index.htm",
        ratio_from=x.ratio_from,
        ratio_to=x.ratio_to,
        effective_date=x.effective_date,
        date_kind=x.date_kind,
        passage=x.passage,
        document=x.document,
        abstain_reason=x.abstain_reason,
        date_note=x.date_note,
        about_a_split=x.about_a_split,
    )


def test_btoc_two_sources_agree_so_it_is_confirmed():
    d = decide(
        polygon_row("BTOC", "2026-10-15"),
        [sec("0001213900-26-108468.txt", form="8-K", filed="2026-10-09")],
        asof=ASOF,
    )
    assert (d.status, d.confirmed_via, d.value_source) == (STATUS_CONFIRMED, VIA_AUTO, "both")
    assert (d.ratio_from, d.ratio_to, d.effective_date) == (
        Decimal(16),
        Decimal(1),
        date(2026, 10, 15),
    )
    assert d.valid_from == date(2026, 10, 9)  # public on the filing date, not the split date
    assert "Beginning with the opening of trading on October 15, 2026" in d.sec.passage


def test_the_same_agreement_before_its_day_is_announced_not_confirmed():
    d = decide(
        polygon_row("BTOC", "2026-10-15"),
        [sec("0001213900-26-108468.txt", form="8-K", filed="2026-10-09")],
        asof=date(2026, 10, 14),
    )
    assert d.status == STATUS_ANNOUNCED and d.confirmed_via == VIA_AUTO
    assert "Takes effect 2026-10-15" in d.note


def test_pbm_the_moved_date_agrees_with_polygon():
    """PBM moved its consolidation from 01-20 to 02-02; the 6-K states 02-02 and Massive.com executes 02-02."""
    d = decide(
        polygon_row("PBM", "2026-02-02"),
        [sec("0001213900-26-009093.txt", form="6-K", filed="2026-01-29")],
        asof=ASOF,
    )
    assert d.status == STATUS_CONFIRMED
    assert (d.ratio_from, d.ratio_to) == (Decimal("6.25"), Decimal(1))


def test_mi_a_legal_effective_date_on_the_session_confirms():
    d = decide(
        polygon_row("MI", "2026-05-18"),
        [
            sec(
                "MI_ex991_0930.htm",
                form="6-K",
                filed="2026-09-30",
                accession="0001213900-26-105247",
            )
        ],
        asof=ASOF,
    )
    assert d.status == STATUS_CONFIRMED and d.effective_date == date(2026, 5, 18)
    assert d.valid_from == date(2026, 5, 18)  # the market saw it before this late filing


def test_wnrs_one_source_for_the_date_needs_review():
    """WNRS's 1-A/A states 1:300 but no market date; Massive.com alone dates it 05-13 — one source, so the
    operator decides (both sides kept on the row)."""
    d = decide(
        polygon_row("WNRS", "2026-05-13"),
        [sec("WNRS_1aa.htm", form="1-A/A", filed="2026-04-14", accession="0001493152-26-016607")],
        asof=ASOF,
    )
    assert d.status == STATUS_NEEDS_REVIEW and d.confirmed_via is None
    assert d.sec is not None and d.sec.ratio_from == Decimal(300)
    assert (d.ratio_from, d.effective_date) == (
        Decimal(300),
        date(2026, 5, 13),
    )  # Massive.com's values
    assert "no first-session or effective date stated" in d.note


def test_mi_window_holds_only_the_proxy_range_so_it_needs_review():
    """MI's window holds the proxy that authorizes a RANGE (the extractor abstains) — the abstain is kept as
    evidence and the split goes to review."""
    d = decide(
        polygon_row("MI", "2026-05-18"),
        [sec("0001213900-26-046153.txt", form="6-K", filed="2026-04-21")],
        asof=ASOF,
        unread=3,
    )
    assert d.status == STATUS_NEEDS_REVIEW
    assert d.sec.abstain_reason.startswith("only an authorized range")
    assert "3 SEC filing(s) in the window could not be read" in d.note


def test_smx_a_polygon_row_no_filing_states_stays_polygon_only():
    """Massive.com lists SMX 2.285 -> 1 on 2026-06-01; the only split filing nearby is SMX's 1-for-20 of 05-11 — a
    different split, named in the note but never attached as this one's evidence."""
    d = decide(
        polygon_row("SMX", "2026-06-01"),
        [sec("0001493152-26-021807.txt", form="6-K", filed="2026-05-08")],
        asof=ASOF,
    )
    assert d.status == STATUS_NEEDS_REVIEW and d.sec is None
    assert d.note.startswith("Massive.com only")
    assert "a different split?" in d.note
    assert d.valid_from == date(2026, 6, 1)


def test_a_ratio_disagreement_keeps_both_sides():
    """BTOC's real filing against a Massive.com row carrying a different ratio on the same day."""
    other = PolygonEvidence("Ex", "BTOC", date(2026, 10, 15), Decimal(15), Decimal(1))
    d = decide(other, [sec("0001213900-26-108468.txt", form="8-K", filed="2026-10-09")], asof=ASOF)
    assert d.status == STATUS_NEEDS_REVIEW and d.sec.ratio_from == Decimal(16)
    assert "the ratios differ" in d.note and d.ratio_from == Decimal(15)


# --- found by the dev backfill (2026-10-20): real filings, real Massive.com rows (fixtures/splits/polygon) ----------


def test_the_dev_run_fixes_confirm_against_polygon():
    """Each was "needs review" before the extractor learned the phrasing; each now agrees with Massive.com."""
    cases = [
        ("ELBM", "2025-01-02", "0001171843-24-007098.txt", "6-K", "2024-12-30"),
        ("CMND", "2026-10-05", "0001213900-26-105269.txt", "6-K", "2026-09-30"),
        ("ERNA", "2025-06-12", "0001641172-25-014877.txt", "8-K", "2025-06-12"),
        ("SMX", "2025-01-15", "0001493152-25-001874.txt", "6-K", "2025-01-13"),
        ("NIPG", "2026-07-06", "0001493152-26-031419.txt", "6-K", "2026-07-01"),
        ("ASNS", "2025-11-18", "0001213900-25-110404.txt", "8-K", "2025-11-14"),
    ]
    for ticker, day, name, form, filed in cases:
        d = decide(polygon_row(ticker, day), [sec(name, form=form, filed=filed)], asof=ASOF)
        assert (d.status, d.confirmed_via) == (STATUS_CONFIRMED, VIA_AUTO), (ticker, d.note)


def test_a_restated_old_ads_change_never_confirms_a_later_split():
    """LGHL: Massive.com lists a 1-for-50 on 2025-03-26; the only filing nearby restates the 2023 ADS change at the
    same ratio — the dates are years apart, so it is review, never auto-confirmed."""
    d = decide(
        polygon_row("LGHL", "2025-03-26"),
        [sec("0001213900-24-111258.txt", form="6-K", filed="2024-12-20")],
        asof=ASOF,
    )
    assert d.status == STATUS_NEEDS_REVIEW
    assert "2023-07-13" in d.note and "tolerance" in d.note


def test_a_filing_that_only_restates_past_splits_is_never_a_splits_evidence():
    """The same LGHL filing as the backfill now reads a 6-K (the restatement screen, 2026-10-24): every ratio it
    states is a past split's, so it abstains — and an abstain of that kind is never shown as THIS split's
    evidence (filed at most 10 days after a split, a filing can only restate OLDER ones). The card says Massive.com
    only, with no 2023 reading attached."""
    x = extract_split_screened(documents("0001213900-24-111258.txt"), filed=date(2024, 12, 20))
    assert x.abstain_reason == PAST_RATIO_ONLY
    s = SecEvidence(
        accession="0001213900-24-111258",
        form="6-K",
        filed=date(2024, 12, 20),
        url="u",
        abstain_reason=x.abstain_reason,
        date_note=x.date_note,
        about_a_split=x.about_a_split,
    )
    d = decide(polygon_row("LGHL", "2025-03-26"), [s], asof=ASOF)
    assert (d.status, d.sec) == (STATUS_NEEDS_REVIEW, None)
    assert d.note.startswith(
        "Massive.com only — no SEC filing read in the window states this split"
    )


# --- the date rule's edges, on direct inputs -------------------------------------------------------------------


def _s(eff: date, kind: str) -> SecEvidence:
    return SecEvidence(
        "a",
        "8-K",
        date(2026, 1, 1),
        "u",
        Decimal(10),
        Decimal(1),
        effective_date=eff,
        date_kind=kind,
    )


def _p(d: date) -> PolygonEvidence:
    return PolygonEvidence("p", "T", d, Decimal(10), Decimal(1))


def test_a_stated_first_session_must_match_exactly():
    assert date_agreement(_s(date(2026, 3, 2), "trading"), _p(date(2026, 3, 2)))[0]
    agree, why = date_agreement(_s(date(2026, 3, 2), "trading"), _p(date(2026, 3, 3)))
    assert not agree and "+1 session(s)" in why


def test_a_legal_effective_date_agrees_within_the_tolerance():
    # effective Friday 5 p.m., first session Monday (1 session); and the tolerance edge
    assert SESSION_TOLERANCE == 2
    assert date_agreement(_s(date(2026, 2, 27), "legal"), _p(date(2026, 3, 2)))[0]
    assert date_agreement(_s(date(2026, 2, 26), "legal"), _p(date(2026, 3, 2)))[0]
    assert not date_agreement(_s(date(2026, 2, 25), "legal"), _p(date(2026, 3, 2)))[0]


def test_sessions_and_factors():
    assert session_distance(date(2026, 2, 27), date(2026, 3, 2)) == 1  # Fri -> Mon
    assert session_distance(date(2026, 3, 2), date(2026, 2, 27)) == -1
    assert session_distance(date(2026, 3, 2), date(2026, 3, 2)) == 0
    assert price_factor(Decimal(16), Decimal(1)) == 16
    assert price_factor(Decimal(1), Decimal(2)) == Fraction(1, 2)
    assert ratio_label(Decimal(16), Decimal(1)) == "1-for-16 reverse split"
    assert ratio_label(Decimal(1), Decimal(2)) == "2-for-1 forward split"
    assert ratio_label(Decimal("6.25"), Decimal(1)) == "1-for-6.25 reverse split"


# --- D2 (platform fixes W2-3, 2026-11-25): a forward 'split' under x1.5 that only Massive.com lists, with no SEC
# filing in its window stating or mentioning a split, is set aside automatically as a distribution -------------
#
# The 25 MEASURED rows (prod, read-only, 2026-11-25): the 17 needs-review rows the steward review found were not
# splits (SCCO 9, GREH 3, PHG 2, GPUS 2, ENVX 1) and the 8 the steward rejected on 10-23/24 (MOVE, DVLT, IQST, CYCU,
# OPEN, HON, ENVX, METC) — each Massive.com row as listed, and the SEC side its stored note records: the filings read
# that state nothing about a split, and the "nearest split filing" a note names (a different split). 23 sit in
# ``decide``'s Massive.com-only branch and are set aside; GPUS 2024-12-13 and CYCU 2025-12-15 sit in the abstained
# branch (an SEC filing in the window mentions a split) and stay for review.

CAP = Decimal("1.5")  # ``split_distribution_max_factor``'s default


def _quiet(n: int, ticker: str) -> list[SecEvidence]:
    """``n`` filings read in the window that state nothing about a split (a note's "N filing(s) read")."""
    return [SecEvidence(f"{ticker}-quiet-{i}", "8-K", date(2025, 1, 2), "u") for i in range(n)]


def _other(
    acc: str, form: str, filed: str, k: int, eff: str | None, kind: str | None
) -> SecEvidence:
    """A filing a note names as the "nearest split filing": a 1-for-``k`` reverse split, a different split."""
    return SecEvidence(
        acc,
        form,
        date.fromisoformat(filed),
        "u",
        Decimal(k),
        Decimal(1),
        effective_date=date.fromisoformat(eff) if eff else None,
        date_kind=kind,
        about_a_split=True,
    )


def _abstained(acc: str, filed: str, why: str) -> SecEvidence:
    return SecEvidence(
        acc, "8-K", date.fromisoformat(filed), "u", abstain_reason=why, about_a_split=True
    )


def _row(ticker: str, day: str, old: str, new: str) -> PolygonEvidence:
    return PolygonEvidence(
        f"E-{ticker}-{day}", ticker, date.fromisoformat(day), Decimal(old), Decimal(new)
    )


MEASURED_DISTRIBUTIONS = [
    # the 16 needs-review non-splits in the Massive.com-only branch
    (_row("SCCO", "2026-11-03", "1", "1.0106"), []),
    (_row("SCCO", "2026-08-11", "1", "1.012"), []),
    (_row("SCCO", "2026-05-13", "1", "1.01"), []),
    (_row("SCCO", "2026-02-10", "1", "1.0085"), []),
    (_row("SCCO", "2025-11-12", "1", "1.0085"), []),
    (_row("SCCO", "2025-08-15", "1", "1.0101"), []),
    (_row("SCCO", "2025-05-02", "1", "1.0099"), []),
    (_row("SCCO", "2025-02-11", "1", "1.0073"), []),
    (_row("SCCO", "2024-11-06", "1", "1.0062"), []),
    (_row("PHG", "2026-05-13", "1", "1.037127656"), _quiet(7, "PHG")),
    (_row("PHG", "2025-05-13", "1", "1.0423095962"), _quiet(8, "PHG")),
    (
        _row("GREH", "2026-09-14", "1", "1.1"),
        [
            *_quiet(1, "GREH"),
            _other("0001683168-26-005632", "1-A POS", "2026-07-20", 500, None, None),
        ],
    ),
    (
        _row("GREH", "2026-07-15", "1", "1.1"),
        [
            *_quiet(1, "GREH"),
            _other("0001683168-26-005503", "1-A POS", "2026-07-14", 500, None, None),
        ],
    ),
    (
        _row("GREH", "2025-11-14", "1", "1.01"),
        [
            *_quiet(12, "GREH"),
            _other("0001683168-25-007448", "1-A POS", "2025-10-08", 500, None, None),
        ],
    ),
    (
        _row("GPUS", "2026-09-15", "1", "1.04066734"),
        [
            *_quiet(13, "GPUS"),
            _other("0001214659-26-010099", "8-K", "2026-08-13", 5, "2026-08-25", "trading"),
        ],
    ),
    (_row("ENVX", "2026-11-13", "4", "5"), []),
    # the 7 of the steward's 10-23/24 rejections in the Massive.com-only branch
    (
        # MOVE's stored note is cut at "effective 2025-10-1": any day of October 2025 is months from the row
        _row("MOVE", "2026-04-07", "1", "1.358"),
        [
            *_quiet(2, "MOVE"),
            _other("0001213900-25-124005", "8-K", "2025-12-22", 10, "2025-10-10", "legal"),
        ],
    ),
    (_row("DVLT", "2026-01-07", "60", "61"), _quiet(2, "DVLT")),
    (_row("IQST", "2025-12-15", "1", "1.01646"), _quiet(2, "IQST")),
    (_row("OPEN", "2025-11-18", "30", "31"), []),
    (_row("HON", "2025-10-30", "1000", "1061"), _quiet(1, "HON")),
    (_row("ENVX", "2025-07-17", "7", "8"), _quiet(6, "ENVX")),
    (_row("METC", "2025-05-30", "1", "1.009228"), _quiet(7, "METC")),
]

# the 2 measured rows an SEC filing in the window mentions a split for (the extractor abstained): kept for review
MEASURED_ABSTAINED = [
    (
        _row("GPUS", "2024-12-13", "1", "1.139"),
        [
            _abstained(
                "0001214659-24-019590",
                "2024-11-26",
                "a ratio is stated but not whether the split is reverse or forward",
            )
        ],
    ),
    (
        _row("CYCU", "2025-12-15", "1", "1.008"),
        [
            _abstained(
                "0001493152-25-016033",
                "2025-09-29",
                "only an authorized range of ratios (3:1) — no ratio set",
            )
        ],
    ),
]


def _why(p: PolygonEvidence, cap: str = "1.5") -> str:
    ratio = ratio_label(p.split_from, p.split_to).split(" ")[0]  # "1.0106-for-1"
    return (
        f"Set aside automatically: a {ratio} forward 'split' listed by Massive.com only, under x{cap} — a stock "
        "dividend or distribution, not a split (the 2026-10-23/24 rule). Restore returns it to review."
    )


def test_d2_the_25_measured_rows_are_counted_as_the_plan_measured_them():
    assert len(MEASURED_DISTRIBUTIONS) + len(MEASURED_ABSTAINED) == 25
    assert {p.ticker for p, _ in MEASURED_DISTRIBUTIONS + MEASURED_ABSTAINED} == {
        "SCCO",
        "PHG",
        "GREH",
        "GPUS",
        "ENVX",
        "MOVE",
        "DVLT",
        "IQST",
        "CYCU",
        "OPEN",
        "HON",
        "METC",
    }


def test_d2_the_measured_distributions_are_set_aside_with_massive_coms_values():
    for p, filings in MEASURED_DISTRIBUTIONS:
        d = decide(p, filings, asof=ASOF, distribution_max_factor=CAP)
        assert (d.status, d.confirmed_via, d.value_source, d.sec) == (
            STATUS_REJECTED,
            None,
            "polygon",
            None,
        ), (p.ticker, p.execution_date, d.note)
        assert (d.key_date, d.effective_date, d.ratio_from, d.ratio_to) == (
            p.execution_date,
            p.execution_date,
            p.split_from,
            p.split_to,
        )
        assert d.valid_from == p.execution_date and d.polygon == p
        # the reason first, then the evidence the review card carried
        assert d.note.startswith(
            _why(p) + " Massive.com only — no SEC filing read in the window states this split"
        )
        assert f"({len(filings)} filing(s) read)" in d.note
        assert distribution_reason(p, filings, max_factor=CAP) == _why(p)
    scco = decide(*MEASURED_DISTRIBUTIONS[0], asof=ASOF, distribution_max_factor=CAP)
    assert scco.note.startswith(
        "Set aside automatically: a 1.0106-for-1 forward 'split' listed by Massive.com only, under x1.5 — a stock "
        "dividend or distribution, not a split (the 2026-10-23/24 rule). Restore returns it to review."
    )
    gpus = decide(*MEASURED_DISTRIBUTIONS[14], asof=ASOF, distribution_max_factor=CAP)
    assert (
        "Nearest split filing: SEC 8-K 0001214659-26-010099" in gpus.note
    )  # a different split, named only


def test_d2_with_the_setting_off_every_measured_row_waits_for_review_as_today():
    for p, filings in MEASURED_DISTRIBUTIONS:
        for d in (
            decide(p, filings, asof=ASOF),
            decide(p, filings, asof=ASOF, distribution_max_factor=None),
        ):
            assert (d.status, d.value_source) == (STATUS_NEEDS_REVIEW, "polygon"), p.ticker
            assert d.note.startswith(
                "Massive.com only — no SEC filing read in the window states this split"
            )


def test_d2_an_sec_filing_that_mentions_a_split_keeps_it_for_review():
    """GPUS 2024-12-13 (its EX-99.1 restates the 1-for-35) and CYCU 2025-12-15 (a proxy's authorized range):
    the extractor abstained, so a filing mentions a split — the steward decides, never the rule."""
    for p, filings in MEASURED_ABSTAINED:
        d = decide(p, filings, asof=ASOF, distribution_max_factor=CAP)
        assert (d.status, d.sec.accession) == (STATUS_NEEDS_REVIEW, filings[0].accession), p.ticker
        assert d.note.startswith("Massive.com only to confirm:")
        assert distribution_reason(p, filings, max_factor=CAP) is None


def test_d2_three_for_two_two_for_one_and_reverse_splits_are_never_distributions():
    for p in (
        _row("T32", "2026-06-01", "2", "3"),  # x1.5 exactly: not under the bound
        _row("T21", "2026-06-01", "1", "2"),
        _row("QGEN", "2025-01-29", "36", "35"),  # a reverse 35-for-36 (QGEN's synthetic buyback)
        _row("T56", "2026-06-01", "6", "5"),
    ):
        d = decide(p, [], asof=ASOF, distribution_max_factor=CAP)
        assert d.status == STATUS_NEEDS_REVIEW and d.note.startswith("Massive.com only"), p.ticker
        assert distribution_reason(p, [], max_factor=CAP) is None


def test_d2_the_bound_is_strict_and_is_the_settings():
    just_under = _row("T", "2026-06-01", "10000", "14999")
    assert decide(just_under, [], asof=ASOF, distribution_max_factor=CAP).status == STATUS_REJECTED
    envx = _row("ENVX", "2026-11-13", "4", "5")  # x1.25
    assert (
        decide(envx, [], asof=ASOF, distribution_max_factor=Decimal("1.25")).status
        == STATUS_NEEDS_REVIEW
    )
    lower = decide(envx, [], asof=ASOF, distribution_max_factor=Decimal("1.3"))
    assert lower.status == STATUS_REJECTED and lower.note.startswith(_why(envx, "1.3"))


def _stock_split(eff: date, kind: str = "trading") -> SecEvidence:
    """An 8-K stating a 6-for-5 stock split paid as a stock distribution (direct input)."""
    return SecEvidence(
        "0000000000-26-000065",
        "8-K",
        date(2026, 5, 20),
        "u",
        Decimal(5),
        Decimal(6),
        effective_date=eff,
        date_kind=kind,
        about_a_split=True,
    )


def test_d2_an_agreeing_sec_filing_confirms_a_small_forward_split_as_today():
    p = _row("T", "2026-06-01", "5", "6")
    d = decide(p, [_stock_split(date(2026, 6, 1))], asof=ASOF, distribution_max_factor=CAP)
    assert (d.status, d.confirmed_via, d.value_source) == (STATUS_CONFIRMED, VIA_AUTO, "both")
    assert d == decide(
        p, [_stock_split(date(2026, 6, 1))], asof=ASOF
    )  # the setting changes nothing here


def test_d2_a_disagreeing_sec_filing_keeps_it_for_review():
    """The same 6-for-5 stated for a first session three sessions off: the sources disagree — the steward's."""
    p = _row("T", "2026-06-01", "5", "6")
    filings = [_stock_split(date(2026, 6, 4))]
    d = decide(p, filings, asof=ASOF, distribution_max_factor=CAP)
    assert d.status == STATUS_NEEDS_REVIEW and d.sec is not None
    assert distribution_reason(p, filings, max_factor=CAP) is None


def test_d2_a_past_splits_restatement_is_not_a_mention_and_unread_filings_stay_on_the_note():
    """A filing whose every ratio is a PAST split's is never this split's evidence (``PAST_RATIO_ONLY``), so it
    does not keep a distribution for review; filings that could not be read are named on the note, and the next
    night re-decides the row (a system set-aside is re-decided like an open one)."""
    p = _row("SCCO", "2026-11-03", "1", "1.0106")
    past = SecEvidence(
        "a", "6-K", date(2026, 10, 1), "u", abstain_reason=PAST_RATIO_ONLY, about_a_split=True
    )
    d = decide(p, [past], asof=ASOF, unread=2, distribution_max_factor=CAP)
    assert d.status == STATUS_REJECTED
    assert d.note.endswith("2 SEC filing(s) in the window could not be read.")


# --- W6-2 (decision W6-a, 2026-11-25): Massive.com's ``stock_dividend`` label in D2's rule ------------------------
#
# GPUS 2024-12-13 (1 -> 1.139) sits in ``decide``'s ABSTAINED branch: its 8-K mentions a split the extractor could not
# read, so W2-3 keeps it for review. The v1 reader's row carries Massive.com's label ``stock_dividend``
# (``tests/fixtures/splits/polygon/v1_2026-11-25.json``). With ``label_overrides_abstain`` (the setting
# ``split_distribution_label_overrides_abstain``, default on) that label stands in for the reading the extractor could
# not make: the row is set aside, the abstaining filing kept as its SEC evidence. A filing that STATES this split (its
# ratio, or a date that agrees) still keeps it for review; the label never narrows W2-3's rule (a ``forward_split``
# label or none at all, the v3 reader's and every row stored before W6-1, decides exactly as W2-3 does).

_GPUS_FILINGS = MEASURED_ABSTAINED[0][1]  # its 8-K of 2024-11-26: a ratio stated, the direction not
_LABELS = (None, "forward_split", "reverse_split", "stock_dividend")


def _why_label(p: PolygonEvidence, cap: str = "1.5") -> str:
    ratio = ratio_label(p.split_from, p.split_to).split(" ")[0]
    return (
        f"Set aside automatically: a {ratio} forward 'split' listed by Massive.com only, under x{cap}, that "
        "Massive.com labels a stock dividend (an SEC filing in its window mentions a split but states no ratio) "
        "— a stock dividend or distribution, not a split (the 2026-10-23/24 rule). Restore returns it to review."
    )


def test_w6_2_the_real_gpus_row_is_the_measured_one_labelled_a_stock_dividend():
    real = v1_row("GPUS", "2024-12-13")
    measured, _ = MEASURED_ABSTAINED[0]
    assert real.adjustment_type == "stock_dividend"
    assert (real.execution_date, real.split_from, real.split_to) == (
        measured.execution_date,
        measured.split_from,
        measured.split_to,
    )


def test_w6_2_the_stock_dividend_label_sets_gpus_aside_and_keeps_its_abstaining_filing():
    real = v1_row("GPUS", "2024-12-13")
    d = decide(
        real, _GPUS_FILINGS, asof=ASOF, distribution_max_factor=CAP, label_overrides_abstain=True
    )
    assert (d.status, d.confirmed_via, d.value_source) == (STATUS_REJECTED, None, "polygon")
    assert d.sec == _GPUS_FILINGS[0]  # the steward still sees the filing that mentions a split
    assert (d.key_date, d.effective_date, d.ratio_from, d.ratio_to, d.valid_from) == (
        real.execution_date,
        real.execution_date,
        real.split_from,
        real.split_to,
        real.execution_date,
    )
    why = distribution_reason(real, _GPUS_FILINGS, max_factor=CAP, label_overrides_abstain=True)
    assert why == _why_label(real)
    # the reason first, then the evidence the review card carried (W2-3's abstained note, unchanged)
    assert d.note == (
        f"{why} Massive.com only to confirm: SEC 8-K 0001214659-24-019590 (filed 2024-11-26) mentions a split but "
        "the extractor abstained — a ratio is stated but not whether the split is reverse or forward."
    )


def test_w6_2_without_the_stock_dividend_label_gpus_waits_for_review_as_w2_3_decides():
    """A ``forward_split`` label, or none (the v3 reader, a row stored before W6-1): W2-3's rule exactly."""
    measured, _ = MEASURED_ABSTAINED[0]
    w2_3 = decide(measured, _GPUS_FILINGS, asof=ASOF, distribution_max_factor=CAP)
    for label in (None, "forward_split", "reverse_split"):
        p = replace(measured, adjustment_type=label)
        d = decide(
            p, _GPUS_FILINGS, asof=ASOF, distribution_max_factor=CAP, label_overrides_abstain=True
        )
        assert (d.status, d.sec) == (STATUS_NEEDS_REVIEW, _GPUS_FILINGS[0]), label
        assert d.note == w2_3.note and d.note.startswith("Massive.com only to confirm:")
        assert d == replace(w2_3, polygon=p)
        assert (
            distribution_reason(p, _GPUS_FILINGS, max_factor=CAP, label_overrides_abstain=True)
            is None
        )


def test_w6_2_the_label_needs_the_setting_and_d2_on():
    real = v1_row("GPUS", "2024-12-13")
    for d in (
        decide(
            real, _GPUS_FILINGS, asof=ASOF, distribution_max_factor=CAP
        ),  # the rule's default: off
        decide(
            real,
            _GPUS_FILINGS,
            asof=ASOF,
            distribution_max_factor=CAP,
            label_overrides_abstain=False,
        ),
        decide(
            real,
            _GPUS_FILINGS,
            asof=ASOF,
            distribution_max_factor=None,  # D2 off: nothing is set aside, labelled or not
            label_overrides_abstain=True,
        ),
    ):
        assert d.status == STATUS_NEEDS_REVIEW and d.note.startswith("Massive.com only to confirm:")
    assert distribution_reason(real, _GPUS_FILINGS, max_factor=CAP) is None


def test_w6_2_a_stock_dividend_at_or_over_the_cap_is_never_set_aside():
    """MTH 2025-01-03: Massive.com labels its 1 -> 2 a stock dividend; at x2 it is over the bound, so it waits for
    review whatever a filing says (the label is not a factor band)."""
    mth = v1_row("MTH", "2025-01-03")
    assert (mth.adjustment_type, mth.split_from, mth.split_to) == (
        "stock_dividend",
        Decimal(1),
        Decimal(2),
    )
    for filings in ([], _GPUS_FILINGS):
        d = decide(
            mth, filings, asof=ASOF, distribution_max_factor=CAP, label_overrides_abstain=True
        )
        assert d.status == STATUS_NEEDS_REVIEW, filings
        assert (
            distribution_reason(mth, filings, max_factor=CAP, label_overrides_abstain=True) is None
        )
    t32 = _row(
        "T32", "2026-06-01", "2", "3"
    )  # x1.5 exactly, labelled a stock dividend: not under the bound
    t32 = replace(t32, adjustment_type="stock_dividend")
    d = decide(
        t32, _GPUS_FILINGS, asof=ASOF, distribution_max_factor=CAP, label_overrides_abstain=True
    )
    assert d.status == STATUS_NEEDS_REVIEW


def test_w6_2_a_stock_dividend_reverse_row_is_never_set_aside():
    p = replace(_row("QGEN", "2025-01-29", "36", "35"), adjustment_type="stock_dividend")
    d = decide(
        p, _GPUS_FILINGS, asof=ASOF, distribution_max_factor=CAP, label_overrides_abstain=True
    )
    assert d.status == STATUS_NEEDS_REVIEW


def test_w6_2_a_stock_dividend_an_sec_filing_states_is_decided_as_today():
    """The label never overrides a filing that STATES the split: one that agrees confirms it, one that states its
    ratio on another date (or another ratio on an agreeing date) keeps it for review — an abstaining filing beside
    them changes nothing."""
    p = replace(_row("T", "2026-06-01", "5", "6"), adjustment_type="stock_dividend")
    other_ratio_same_day = SecEvidence(
        "0000000000-26-000066",
        "8-K",
        date(2026, 5, 20),
        "u",
        Decimal(1),
        Decimal(2),
        effective_date=date(2026, 6, 1),
        date_kind="trading",
        about_a_split=True,
    )
    for filings, status in (
        ([_stock_split(date(2026, 6, 1))], STATUS_CONFIRMED),
        ([_stock_split(date(2026, 6, 1)), *_GPUS_FILINGS], STATUS_CONFIRMED),
        ([_stock_split(date(2026, 6, 4)), *_GPUS_FILINGS], STATUS_NEEDS_REVIEW),
        ([other_ratio_same_day, *_GPUS_FILINGS], STATUS_NEEDS_REVIEW),
    ):
        d = decide(p, filings, asof=ASOF, distribution_max_factor=CAP, label_overrides_abstain=True)
        assert d.status == status, [s.accession for s in filings]
        assert d == decide(p, filings, asof=ASOF, distribution_max_factor=CAP)  # as W2-3 decides
        assert distribution_reason(p, filings, max_factor=CAP, label_overrides_abstain=True) is None


def test_w6_2_the_label_never_narrows_the_massive_com_only_rule():
    """Every measured distribution under every label, the override on: W2-3's verdict and note exactly (HON's
    ``forward_split`` 1000 -> 1061 stays set aside by its factor); the label is named only where it decided.
    """
    for p0, filings in MEASURED_DISTRIBUTIONS:
        w2_3 = decide(p0, filings, asof=ASOF, distribution_max_factor=CAP)
        for label in _LABELS:
            p = replace(p0, adjustment_type=label)
            d = decide(
                p, filings, asof=ASOF, distribution_max_factor=CAP, label_overrides_abstain=True
            )
            assert d == replace(w2_3, polygon=p), (p.ticker, p.execution_date, label)
            assert d.status == STATUS_REJECTED and "labels" not in d.note
    hon = replace(MEASURED_DISTRIBUTIONS[20][0], adjustment_type="forward_split")
    assert hon.ticker == "HON"
    assert (
        decide(hon, [], asof=ASOF, distribution_max_factor=CAP, label_overrides_abstain=True).status
        == STATUS_REJECTED
    )


def test_w6_2_a_past_splits_restatement_and_a_split_the_filing_states_elsewhere_still_do_not_count():
    """Beside the abstaining filing: a filing restating a PAST split is no mention (as W2-3), and one stating a
    DIFFERENT split (GPUS's own 1-for-35, first session 2024-11-25) is not about this one: set aside.
    """
    real = v1_row("GPUS", "2024-12-13")
    past = SecEvidence(
        "a", "6-K", date(2024, 12, 1), "u", abstain_reason=PAST_RATIO_ONLY, about_a_split=True
    )
    one_for_35 = _other("b", "8-K", "2024-11-20", 35, "2024-11-25", "trading")
    filings = [*_GPUS_FILINGS, past, one_for_35]
    d = decide(real, filings, asof=ASOF, distribution_max_factor=CAP, label_overrides_abstain=True)
    assert d.status == STATUS_REJECTED and d.sec == _GPUS_FILINGS[0]
