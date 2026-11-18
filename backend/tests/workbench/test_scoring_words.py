"""V2 W5d: the scored read's words about the two shared values (``workbench/scoring.py``).

- No market-cap note tells anyone to ratify a share count: the notes are ``platform_values.words``'s (what is
  missing; "The steward sets the ratio"), and the agents copy them.
- Each provenance says WHO set its fact as a kind (``set_by``), never the stored ``ratified_by`` (an email): a
  shared value ``machine`` / ``steward`` / ``carried``; the reader's own purity ``you``.
- ``ScoredMember`` has no ``unconfirmed_estimates`` (the plan's Q8): a blank runway or cap is a shared value's
  state, read from ``GET /platform-values/...``, never a count of things the user confirms.

DB-backed (the shared ``db`` and ``security_id`` fixtures).
"""

from __future__ import annotations

from datetime import date

from db.session import DEFAULT_TENANT_ID
from domain.config import DEFAULT_CONFIG
from domain.workbench import ScoredMember
from ingest.cash_burn import ingest_cash_burn
from ingest.revenue_mix import ingest_revenue_mix
from ingest.shares import ingest_shares_outstanding
from platform_values import words
from signals.base import PointInTimeData
from tests.workbench.test_scoring import _member, _price
from workbench.scoring import score_member

STEWARD = "steward@example.test"
USER = "jane@example.test"


def _notes(fig) -> str:
    return " ".join(str(p.detail.get("note", "")) for p in fig.provenance)


def _shares(db, sid, **over) -> None:
    ingest_shares_outstanding(
        db,
        sid,
        **{
            "shares": 50_000_000,
            "source": "10-q-cover",
            "source_ref": "10-Q",
            "event_date": date(2026, 6, 17),
            "ratified_by": STEWARD,
            **over,
        },
    )
    db.commit()


def _score(db, sid):
    pit = PointInTimeData(db, asof=date(2026, 7, 1), workspace=DEFAULT_TENANT_ID)
    return score_member(pit, _member(sid), DEFAULT_CONFIG)


def test_the_scored_member_has_no_unconfirmed_count():
    assert "unconfirmed_estimates" not in ScoredMember.model_fields


def test_no_market_cap_note_says_ratify_and_each_reads_the_one_words_module(db, security_id):
    _shares(db, security_id)  # a count, no price
    no_price = _notes(_score(db, security_id).market_cap)
    assert words.CAP_NO_PRICE in no_price
    for note in (no_price, words.CAP_NO_SHARES, words.CAP_ADS_UNREAD):
        assert "ratif" not in note.lower()


def test_an_unread_ads_ratio_withholds_the_cap_and_says_the_steward_sets_it(db, security_id):
    _price(db, security_id, date(2026, 6, 30), 10.0)
    _shares(db, security_id, ads_ratio_status="unread")
    cap = _score(db, security_id).market_cap
    assert cap.value is None
    assert words.CAP_ADS_UNREAD in _notes(cap)


def test_a_price_with_no_shared_count_names_the_missing_count(db, security_id):
    _price(db, security_id, date(2026, 6, 30), 10.0)
    db.commit()
    assert _notes(_score(db, security_id).market_cap) == words.CAP_NO_SHARES


def test_provenance_says_who_set_each_value_as_a_kind_never_an_email(db, security_id):
    _price(db, security_id, date(2026, 6, 30), 10.0)
    _shares(db, security_id)
    ingest_cash_burn(
        db,
        security_id,
        cash_usd=30_000_000,
        quarterly_burn_usd=3_000_000,
        source="10-Q",
        source_ref="10-Q",
        event_date=date(2026, 6, 17),
        ratified_by="operator",
    )
    ingest_revenue_mix(
        db,
        security_id,
        segment_label="core",
        mix_pct=80,
        source="10-k-segment",
        source_ref="10-K",
        event_date=date(2025, 12, 31),
        ratified_by=USER,
    )
    db.commit()
    m = _score(db, security_id)
    shares_detail = next(p.detail for p in m.market_cap.provenance if p.source == "10-q-cover")
    assert shares_detail["set_by"] == "steward"
    assert m.runway.provenance[0].detail["set_by"] == "carried"
    assert m.purity.provenance[0].detail["set_by"] == "you"
    for fig in (m.market_cap, m.runway, m.purity):
        for p in fig.provenance:
            assert "ratified_by" not in p.detail
            assert "@" not in str(p.detail)
