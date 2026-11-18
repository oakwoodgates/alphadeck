"""V2 W5d: the words the shared filing reader writes about WHO set a stored value (``dashboards/filings.py``).

A share count or cash value is a PLATFORM value: the steward's row reads "Verified by the steward on <day>", a
value carried over from V1 "Verified (carried over from V1)", a machine row "From the filing (applied
automatically)", and none of them ever carries the stored ``ratified_by`` (an email). The reader's OWN purity stays
theirs: "Ratified by you on <day>", a "ratified fact", never relabeled as the steward's. A company catalyst a
person ratified is shared data too: worded like the steward's value. The words come from ``platform_values.words``.
Pure: no database.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

from dashboards.evidence_ledger import fact_authority, fact_rows
from dashboards.filings import Who, rows_from_catalysts, rows_from_ratified
from platform_values import words

W = Who(uuid4(), "STW", "0000876543", "Steward Test Corp")
STEWARD = "steward@example.test"
USER = "jane@example.test"
STAMP = datetime(2026, 11, 18, 15, 0, tzinfo=timezone.utc)  # 10:00 ET on Nov 18


def _shares(by):
    return {
        "shares": Decimal("52113000"),
        "source": "10-q-cover",
        "source_ref": "0000876543-26-000044",
        "ratified_by": by,
        "valid_from": date(2026, 9, 30),
        "recorded_at": STAMP,
    }


def _cash(by):
    return {
        "cash_usd": Decimal("890000000"),
        "quarterly_burn_usd": Decimal("31400000"),
        "source": "10-Q",
        "source_ref": "0000876543-26-000044",
        "ratified_by": by,
        "valid_from": date(2026, 9, 30),
        "recorded_at": STAMP,
    }


def _mix(by):
    return {
        "segment_label": "nuclear",
        "mix_pct": Decimal("61"),
        "source": "10-k-segment",
        "source_ref": "0000876543-26-000010",
        "ratified_by": by,
        "valid_from": date(2025, 12, 31),
        "recorded_at": STAMP,
    }


def _all_text(row) -> str:
    return " ".join((row.form, row.headline, *row.detail, str(row.set_by)))


def test_a_stewards_share_count_and_cash_read_verified_by_the_steward_and_never_carry_the_email():
    for row in rows_from_ratified([_cash(STEWARD)], [_shares(STEWARD)], [], W):
        assert row.form == "verified value"
        assert row.detail[-1] == "Verified by the steward on Nov 18, 2026"
        assert (row.set_by, row.ratified_on) == ("steward", date(2026, 11, 18))
        assert "@" not in _all_text(row)
        assert not any("ratified by" in line.lower() for line in row.detail)


def test_a_carried_value_reads_verified_carried_over_from_v1():
    (row,) = rows_from_ratified([], [_shares("operator")], [], W)
    assert (row.form, row.set_by) == ("verified value", "carried")
    assert row.detail[-1] == words.CARRIED_LINE == "Verified (carried over from V1)"


def test_a_machine_value_reads_from_the_filing_applied_automatically():
    for by in ("auto", None, " AUTO "):
        (row,) = rows_from_ratified([], [_shares(by)], [], W)
        assert (row.form, row.set_by) == ("stored fact", None)
        assert row.detail[-1] == words.FROM_FILING_LINE == "From the filing (applied automatically)"
        assert "operator" not in _all_text(row)


def test_the_users_own_purity_stays_hers_and_names_no_email():
    (row,) = rows_from_ratified([], [], [_mix(USER)], W)
    assert (row.form, row.set_by) == ("ratified fact", "you")
    assert row.detail[-1] == "Ratified by you on Nov 18, 2026"
    assert "@" not in _all_text(row)
    # a purity row nobody ratified keeps its words (purity is unchanged by W5d)
    (stored,) = rows_from_ratified([], [], [_mix("auto")], W)
    assert stored.detail[-1] == "Applied automatically, not operator-ratified"


def test_a_company_catalyst_a_person_ratified_is_worded_as_the_stewards():
    f = {
        "catalyst_type": "regulatory",
        "label": "a decision date",
        "grade": "core",
        "source": "ratified",
        "source_ref": "0000876543-26-000070",
        "valid_from": date(2026, 11, 1),
        "recorded_at": STAMP,
    }
    (steward,) = rows_from_catalysts([{**f, "ratified_by": STEWARD}], W)
    (carried,) = rows_from_catalysts([{**f, "ratified_by": "operator"}], W)
    assert steward.detail[-2:] == ("Source: ratified", "Verified by the steward on Nov 18, 2026")
    assert carried.detail[-1] == "Verified (carried over from V1)"
    assert (steward.set_by, carried.set_by) == ("steward", "carried")
    assert "@" not in _all_text(steward)


def test_the_ledger_classes_each_row_by_its_setter_kind_and_names_no_email():
    rows = rows_from_ratified(
        [_cash(STEWARD)], [_shares("operator"), _shares("auto")], [_mix(USER)], W
    )
    got = sorted((fact_authority(r), r.set_by or "-") for r in rows)
    assert got == [
        ("automatic", "-"),
        ("operator", "you"),
        ("steward", "carried"),
        ("steward", "steward"),
    ]
    ledger = fact_rows({W.security_id: rows}, {})
    assert sorted(r.by or "-" for r in ledger) == ["-", "carried", "steward", "you"]
    assert not any("@" in (r.by or "") for r in ledger)
