"""The split list's auto-confirm rule (split plan S1) — two independent sources must agree (operator
decision 2026-10-20). Each verdict below pairs a REAL Massive.com row with what the REAL SEC filing states
(``tests/fixtures/splits``); the rule's edges with no real instance are stated on direct inputs."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from fractions import Fraction

from domain.splits import (
    PAST_RATIO_ONLY,
    SESSION_TOLERANCE,
    STATUS_ANNOUNCED,
    STATUS_CONFIRMED,
    STATUS_NEEDS_REVIEW,
    VIA_AUTO,
    PolygonEvidence,
    SecEvidence,
    date_agreement,
    decide,
    price_factor,
    ratio_label,
    session_distance,
)
from ingest.edgar.split_filing import extract_split, extract_split_screened
from tests.split_fixtures import documents, polygon_row

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
