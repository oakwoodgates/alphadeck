from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from db.bitemporal import as_of
from db.clock import db_now
from db.session import DEFAULT_TENANT_ID
from domain.config import DEFAULT_CONFIG
from domain.enums import Grade, State
from domain.market_time import known_at_for_asof
from domain.scopes import DATA_TENANT_ID, tenants_for
from ingest import CacheMiss
from ingest.doe import entities
from ingest.doe.client import UsaSpendingClient
from ingest.doe.feed import _derive_grade, discover, parse_award
from pipeline.call_for_thesis import call_for_thesis
from pipeline.seed import LEU_ID, NUCLEAR_THESIS_ID, OKLO_ID, seed_doe_catalysts, seed_nuclear
from signals import catalyst_conviction
from signals.base import PointInTimeData

_FIXTURES = Path(__file__).resolve().parents[2] / "seed_data" / "doe"
_KNOWN = datetime(2100, 1, 1, tzinfo=timezone.utc)
_ASOF = date(
    2026, 6, 2
)  # the real moment the breakouts are fresh AND holding (by 06-03 they give back)

# the polluted recipient the spike caught: "OKLO TECHNOLOGIES, INC." carries $48B of national-lab
# contracts and must NOT resolve to OKLO — the real awardee is OKLO INC. (a different recipient_id)
_OKLO_TECHNOLOGIES_ID = "9de4aa64-a655-236e-42dd-ebce56d66284-R"
_ACO_ID = "d527144c-7fea-82ff-aff0-e95e5fd6e488-C"
_OKLO_INC_ID = "0bf298ad-ffe8-996a-d34e-70e1621fe8ee-R"


def _offline_client() -> UsaSpendingClient:
    return UsaSpendingClient(cache_dir=_FIXTURES, allow_live=False)


def test_resolver_is_exact_not_fuzzy():
    """Resolution is exact-by-recipient_id — the operator's no-fuzzy-matching rule. The polluted
    OKLO TECHNOLOGIES recipient and any unknown id resolve to None (dropped), never guessed."""
    assert entities.resolve(_ACO_ID).ticker == "LEU"
    assert entities.resolve(_OKLO_INC_ID).ticker == "OKLO"
    assert entities.resolve(_OKLO_TECHNOLOGIES_ID) is None  # polluted entity -> dropped
    assert entities.resolve("not-a-real-id") is None
    assert entities.resolve(None) is None


def test_grade_rule_is_customer_vs_sponsor():
    """The grade principle (the operator's edge): DOE-as-customer (a contract DOE buys, or committed
    financing) = core; DOE-as-sponsor (assistance/OTA/grant) = flip — by NATURE, never by size."""
    cfg = DEFAULT_CONFIG
    big = cfg.doe_core_min_obligation_usd + 1
    assert _derive_grade("contract", big, cfg) is Grade.CORE  # DOE buys product = revenue
    assert _derive_grade("loan", 0.0, cfg) is Grade.CORE  # loan guarantee = committed financing
    assert _derive_grade("contract", 1.0, cfg) is Grade.FLIP  # sub-threshold contract = provisional
    assert _derive_grade("grant", 200_000_000.0, cfg) is Grade.FLIP  # big assistance is STILL flip
    assert _derive_grade("other", 0.0, cfg) is Grade.FLIP  # an OTA = sponsor, not customer


def test_client_raises_on_cache_miss_with_live_disabled(tmp_path):
    """The etiquette guard: the test transport never hits the network — a miss raises CacheMiss."""
    client = UsaSpendingClient(cache_dir=tmp_path, allow_live=False)
    with pytest.raises(CacheMiss):
        client.award_detail("CONT_AWD_NOPE")


def test_feed_derives_grade_and_horizon_from_real_fixtures():
    """Pure (no DB): on committed USASpending fixtures the feed re-derives the hand-seeded catalysts and
    grades every discovered award deterministically. Discovery (a fuzzy net) resolves ONLY to curated
    names — NAC International / OKLO TECHNOLOGIES never appear."""
    client = _offline_client()
    found = discover(client)
    assert found  # gid -> ticker
    assert set(found.values()) <= {"LEU", "OKLO"}  # nothing else leaks through the exact resolver

    parsed = {gid: parse_award(client, gid, ticker) for gid, ticker in found.items()}
    by_piid = {c.piid: c for c in parsed.values() if c is not None}

    # LEU's $317M HALEU production CONTRACT -> core, horizon = base term (option A), event = the date the
    # award was recorded (for this contract, the same day its work period starts)
    leu = by_piid["89243223CNE000030"]
    assert leu.ticker == "LEU" and leu.grade is Grade.CORE and leu.category == "contract"
    assert leu.event_date == date(2022, 11, 30) and leu.horizon_end == date(2026, 6, 30)
    assert leu.obligation > 3e8 and "89243223CNE000030" in leu.source_ref

    # OKLO's reactor-pilot OTA -> flip (assistance/other, $0), horizon -> 2029 (a long, durable horizon).
    # Event = the date the award was RECORDED, 2026-05-18 (the payload's `date_signed`: its first action
    # date in the federal record), not the work period's start 2026-02-09, 98 days earlier (rule
    # 2026-11-catalysts-dated-by-record-date)
    oklo = by_piid["DENE0009589"]
    assert oklo.ticker == "OKLO" and oklo.grade is Grade.FLIP and oklo.category == "other"
    assert oklo.event_date == date(2026, 5, 18) and oklo.horizon_end == date(2029, 7, 1)
    assert oklo.obligation == 0.0

    # the binding-ness rule: a large ASSISTANCE award (the $148M grant) still reads flip, not core
    grant = by_piid["DENE0000530"]
    assert grant.grade is Grade.FLIP and grant.category != "contract" and grant.obligation > 1e8


# --- the event date: the date the award was RECORDED (rule 2026-11-catalysts-dated-by-record-date) ------
#
# The six committed payloads, one row each: award id -> (work period start, date recorded, the label the
# feed built BEFORE the rule). The dates are the payloads' own ``period_of_performance.start_date`` and
# ``date_signed`` (the source's name for the award's first action date in the federal record: the day it
# was recorded, NOT the day the agreement was signed); the labels were MEASURED by running the pre-rule
# ``feed.py`` on these payloads (2026-11-11), so "byte-identical" below is checked against what the feed
# really wrote, not against a string this file derives from the new code.
_SIX_AWARDS: dict[str, tuple[date, date, str]] = {
    "ASST_NON_DENE0009589_089": (
        date(2026, 2, 9),
        date(2026, 5, 18),
        "DOE other (DENE0009589): OKLO OTHER TRANSACTION AGREEMENT (OTA) FOR DOE REACTOR PILOT PROGRAM. "
        "— $0 obligated, term to 2029-07-01",
    ),
    "ASST_NON_DENE0000530_089": (
        date(2012, 6, 1),
        date(2012, 11, 30),
        "DOE grant (DENE0000530): AMERICAN CENTRIFUGE CASCADE DEMONSTRATION TEST PROGRAM "
        "— $147,951,761 obligated, term to 2014-04-15",
    ),
    "ASST_NON_DESC0003997_089": (
        date(2010, 3, 23),
        date(2010, 12, 21),
        "DOE grant (DESC0003997): AMERICAN CENTRIFUGE DEMONSTRATION PROJECT "
        "— $0 obligated, term to 2011-03-31",
    ),
    "CONT_AWD_89243223CNE000030_8900_-NONE-_-NONE-": (
        date(2022, 11, 30),
        date(2022, 11, 30),
        "DOE contract (89243223CNE000030): HIGH ASSAY LOW ENRICHED URANIUM (HALEU) CASCADE DEMO "
        "COMPLETION AND EXTENDED PRO — $317,041,412 obligated, term to 2026-06-30",
    ),
    "CONT_AWD_89243225FNE400178_8900_89243225DNE000026_8900": (
        date(2025, 4, 11),
        date(2025, 4, 11),
        "DOE contract (89243225FNE400178): NEW TASK ORDER AWARD UNDER THE LOW-ENRICHED URANIUM (LEU) - "
        "ENRICHMENT ACQUISITI — $500,000 obligated, term to 2026-04-10",
    ),
    "CONT_AWD_89303519CNE000005_8900_-NONE-_-NONE-": (
        date(2019, 5, 31),
        date(2019, 5, 31),
        "DOE contract (89303519CNE000005): LETTER CONTRACT TO FACILITATE THE DEMONSTRATION OF HIGH ASSAY "
        "LOW ENRICHED URANI — $172,974,296 obligated, term to 2023-04-30",
    ),
}
_OKLO_AWARD = "ASST_NON_DENE0009589_089"
_OKLO_RECORDED = date(2026, 5, 18)


def test_all_six_committed_awards_count_from_the_date_they_were_recorded():
    """Pure (no DB), on the six REAL payloads: every award's event date is its ``date_signed`` — the date
    it was recorded. Three were recorded after their work period started (so their date MOVES, and the
    label now says both dates); the three contracts were recorded the day the work starts (nothing moves,
    and the label is byte-for-byte what the feed wrote before the rule)."""
    client = _offline_client()
    found = discover(client)
    assert set(found) == set(_SIX_AWARDS)  # the six, no more: the table below covers every payload

    moved, unmoved = [], []
    for gid, (start, recorded, label_before) in _SIX_AWARDS.items():
        c = parse_award(client, gid, found[gid])
        assert c is not None, gid
        assert c.event_date == recorded, gid  # THE RULE: the date the award was recorded
        # both dates carried, as the payload states them
        assert (c.start_date, c.recorded_date) == (start, recorded), gid
        if recorded != start:
            moved.append(gid)
            # recorded AFTER the work period began: the old date was the earlier one
            assert c.event_date > start, gid
            assert c.label == f"{label_before} · recorded {recorded}, work period from {start}", gid
        else:
            unmoved.append(gid)
            assert c.label == label_before, gid  # byte-identical to the label before the rule

    assert sorted(moved) == [
        "ASST_NON_DENE0000530_089",
        "ASST_NON_DENE0009589_089",
        "ASST_NON_DESC0003997_089",
    ]
    assert len(unmoved) == 3 and all(g.startswith("CONT_AWD_") for g in unmoved)


class _OneAward:
    """INPUT-CONTRACT stub (not a fixture, not a real award): hands ``parse_award`` ONE minimal detail dict,
    so each test below states the rule's branch directly on its inputs. The six real payloads all carry
    both dates, recorded on or after the start, so they cannot exercise these branches. The stub's dicts
    use the payload's own key, ``date_signed`` (the source's name for the date the award was recorded).
    """

    def __init__(self, detail: dict) -> None:
        self._detail = detail

    def award_detail(self, generated_internal_id: str) -> dict:
        return self._detail


def _contract_award(**overrides) -> dict:
    detail = {
        "category": "grant",
        "fain": "DEXX0000001",
        "total_obligation": 1000.0,
        "description": "A TEST AWARD",
        "date_signed": "2025-06-10",
        "period_of_performance": {"start_date": "2025-03-01", "end_date": "2027-03-01"},
    }
    detail.update(overrides)
    return detail


_LABEL_NO_SUFFIX = "DOE grant (DEXX0000001): A TEST AWARD — $1,000 obligated, term to 2027-03-01"


def test_input_contract_no_record_date_falls_back_to_the_work_period_start():
    """INPUT-CONTRACT: a payload with no ``date_signed`` (no record date) is dated as it was before the rule
    (the work period's start), and its label is the pre-rule label (no second date to show)."""
    for missing in (None, ""):
        c = parse_award(_OneAward(_contract_award(date_signed=missing)), "GID", "TKR")
        assert c is not None and c.event_date == date(2025, 3, 1)
        assert (c.start_date, c.recorded_date) == (date(2025, 3, 1), None)
        assert c.label == _LABEL_NO_SUFFIX
    no_key = _contract_award()
    del no_key["date_signed"]
    assert parse_award(_OneAward(no_key), "GID", "TKR").event_date == date(2025, 3, 1)


def test_input_contract_an_unreadable_record_date_falls_back_to_the_work_period_start():
    """INPUT-CONTRACT: "readable" means it parses as a date. A record date that does not is treated as
    absent, so the new field can never be the reason an award fails to parse."""
    c = parse_award(_OneAward(_contract_award(date_signed="not a date")), "GID", "TKR")
    assert c is not None and c.event_date == date(2025, 3, 1) and c.recorded_date is None
    assert c.label == _LABEL_NO_SUFFIX


def test_input_contract_a_record_date_before_the_start_still_counts_from_the_record_date():
    """INPUT-CONTRACT: a record date EARLIER than the work period's start is the event date too (the award
    is in the federal record from then, whenever its work begins). The term's end is unchanged, so liveness
    simply runs longer."""
    c = parse_award(_OneAward(_contract_award(date_signed="2025-01-15")), "GID", "TKR")
    assert c is not None and c.event_date == date(2025, 1, 15)
    assert c.horizon_end == date(2027, 3, 1)
    assert c.label == f"{_LABEL_NO_SUFFIX} · recorded 2025-01-15, work period from 2025-03-01"


def test_input_contract_a_record_date_equal_to_the_start_leaves_the_label_alone():
    """INPUT-CONTRACT: equal dates -> one date, nothing to explain, the pre-rule label."""
    c = parse_award(_OneAward(_contract_award(date_signed="2025-03-01")), "GID", "TKR")
    assert c is not None and c.event_date == date(2025, 3, 1) and c.label == _LABEL_NO_SUFFIX


def test_input_contract_a_record_date_with_no_start_date_dates_the_award():
    """INPUT-CONTRACT: the record date alone is enough to anchor the award (before the rule a payload with
    no start date was skipped). There is no second date, so the label carries no suffix."""
    c = parse_award(
        _OneAward(_contract_award(period_of_performance={"end_date": "2027-03-01"})), "GID", "TKR"
    )
    assert c is not None and c.event_date == date(2025, 6, 10) and c.start_date is None
    assert c.label == _LABEL_NO_SUFFIX


def test_input_contract_neither_date_skips_the_award():
    """INPUT-CONTRACT: no record date and no start date -> ``None`` (nothing honest to anchor it on)."""
    for detail in (
        _contract_award(date_signed=None, period_of_performance={}),
        _contract_award(date_signed=None, period_of_performance=None),
        _contract_award(date_signed="", period_of_performance={"end_date": "2027-03-01"}),
    ):
        assert parse_award(_OneAward(detail), "GID", "TKR") is None


def _oklo_award_rows(db, asof: date, *, clock: str, known_at: datetime) -> list[dict]:
    rows = as_of(
        db,
        "fact_catalyst",
        security_id=OKLO_ID,
        asof=asof,
        known_at=known_at,
        tenant_id=tenants_for("fact_catalyst", data=DATA_TENANT_ID, workspace=DEFAULT_TENANT_ID),
        clock=clock,
    )
    return [r for r in rows if r["source_ref"].endswith(_OKLO_AWARD)]


def test_the_award_is_not_read_before_it_was_recorded_on_either_clock(db):
    """POINT-IN-TIME (invariant #1), on the real feed rows. The DENE0009589 award's work period starts
    2026-02-09 and it was recorded 2026-05-18. A view on 2026-03-01 — between the two, and before the
    award's public announcement of 2026-03-17 — must not read it: on the RECORD clock (``known_at`` = now,
    the live read's gate) and on the PUBLIC clock (a history view, capped at that night's end). From
    2026-05-18 it is read, dated at its record date. And the detector does not fire on it before
    2026-05-18 (which is late by design: this source does not carry the announcement date).

    Before the rule this row's ``valid_from`` was 2026-02-09, so both reads at 2026-03-01 returned it.
    """
    seed_nuclear(db)
    db.commit()
    seed_doe_catalysts(db)
    db.commit()
    now = db_now(db)

    between = date(2026, 3, 1)
    assert _oklo_award_rows(db, between, clock="record", known_at=now) == []
    assert _oklo_award_rows(db, between, clock="public", known_at=known_at_for_asof(between)) == []
    # the strictest form: a public read told that EVERYTHING public by now is knowable still refuses it,
    # because the fact is not dated yet on 2026-03-01
    assert _oklo_award_rows(db, between, clock="public", known_at=now) == []

    for clock, known_at in (("record", now), ("public", known_at_for_asof(_OKLO_RECORDED))):
        (row,) = _oklo_award_rows(db, _OKLO_RECORDED, clock=clock, known_at=known_at)
        assert row["valid_from"] == _OKLO_RECORDED and row["horizon_end"] == date(2029, 7, 1)
        assert row["source"] == "doe_usaspending" and row["ratified_by"] is None
        assert "recorded 2026-05-18, work period from 2026-02-09" in row["label"]

    # the detector, through the real point-in-time view: nothing before the record date, on either clock
    def fired(asof: date, clock: str):
        pit = PointInTimeData(
            db,
            asof=asof,
            known_at=now if clock == "record" else known_at_for_asof(asof),
            workspace=DEFAULT_TENANT_ID,
            clock=clock,
        )
        return catalyst_conviction.detect(pit, OKLO_ID, asof)

    for clock in ("record", "public"):
        for before in (date(2026, 2, 9), between, date(2026, 5, 15), date(2026, 5, 17)):
            assert fired(before, clock) is None, (clock, before)
        ev = fired(_OKLO_RECORDED, clock)
        assert ev is not None and ev.fired and ev.asof == _OKLO_RECORDED  # fires AT the record date
        # liveness follows the event date: the term's end is unchanged, the window starts 98 days later
        assert ev.alpha_liveness_days == (date(2029, 7, 1) - _OKLO_RECORDED).days == 1140


def test_feed_arms_the_nuclear_theme_with_a_ranked_per_member_menu(db):
    """End-to-end on real data: the AUTOMATED feed (offline fixtures) emits the nuclear catalysts and the
    theme arms with a per-member ranked menu (M5 Part A). The feed still derives LEU core + OKLO flip from
    the structured terms; the menu then ranks OKLO (fresh, → 2029) above LEU (core but lapsing → 06-30).
    """
    seed_nuclear(db)
    db.commit()
    emitted = seed_doe_catalysts(db)
    db.commit()

    # the feed emitted both catalysts with the right grades, from the structured terms (unchanged)
    by_ticker_grade = {(c.ticker, c.grade) for c in emitted}
    assert ("LEU", Grade.CORE) in by_ticker_grade
    assert ("OKLO", Grade.FLIP) in by_ticker_grade

    card = call_for_thesis(db, NUCLEAR_THESIS_ID, _ASOF, known_at=_KNOWN, record=False)
    assert card.state is State.ARMED
    # the ranked menu headlines the FRESH member (OKLO), not the lapsing core (LEU) — runway over grade
    assert card.armed_security_id == OKLO_ID
    assert [m.security_id for m in card.armed_members] == [OKLO_ID, LEU_ID]
    leu = card.armed_members[1]
    assert leu.conviction_grade is Grade.CORE and leu.exit_by == date(
        2026, 6, 30
    )  # core, lapsing, #2

    # provenance traces to the real USASpending awards (show the work), both names present
    refs = [p.ref for t in card.triggers_fired for p in t.sources]
    assert any("89243223CNE000030" in r for r in refs)  # LEU contract
    assert any("DENE0009589" in r for r in refs)  # OKLO OTA
    assert any("usaspending.gov" in r for r in refs)
