from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from domain.config import DEFAULT_CONFIG
from domain.enums import Grade, State
from ingest import CacheMiss
from ingest.doe import entities
from ingest.doe.client import UsaSpendingClient
from ingest.doe.feed import _derive_grade, discover, parse_award
from pipeline.call_for_thesis import call_for_thesis
from pipeline.seed import LEU_ID, NUCLEAR_THESIS_ID, OKLO_ID, seed_doe_catalysts, seed_nuclear

_FIXTURES = Path(__file__).resolve().parents[2] / "seed_data" / "doe"
_KNOWN = datetime(2027, 1, 1, tzinfo=timezone.utc)
_ASOF = date(2026, 6, 5)  # all four nuclear names have a live breakout here

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

    # LEU's $317M HALEU production CONTRACT -> core, horizon = base term (option A), event = PoP start
    leu = by_piid["89243223CNE000030"]
    assert leu.ticker == "LEU" and leu.grade is Grade.CORE and leu.category == "contract"
    assert leu.event_date == date(2022, 11, 30) and leu.horizon_end == date(2026, 6, 30)
    assert leu.obligation > 3e8 and "89243223CNE000030" in leu.source_ref

    # OKLO's reactor-pilot OTA -> flip (assistance/other, $0), horizon -> 2029 (a long, durable horizon)
    oklo = by_piid["DENE0009589"]
    assert oklo.ticker == "OKLO" and oklo.grade is Grade.FLIP and oklo.category == "other"
    assert oklo.event_date == date(2026, 2, 9) and oklo.horizon_end == date(2029, 7, 1)
    assert oklo.obligation == 0.0

    # the binding-ness rule: a large ASSISTANCE award (the $148M grant) still reads flip, not core
    grant = by_piid["DENE0000530"]
    assert grant.grade is Grade.FLIP and grant.category != "contract" and grant.obligation > 1e8


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
