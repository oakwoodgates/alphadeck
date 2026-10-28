"""The context role on a basket membership (Context roles, Phase 1) — pure domain; no DB.

A role says what part a company plays on THIS card. It is per company per card (stored on every row of a company),
operator-authored, and display metadata only. These pin the enum's closed set, the model field (NULL = unset, an
unknown value refused), and the two pure helpers the promote route applies after the identity passes: the conflict
finder (two different non-null roles on one company) and the inheritance fill (a NULL row beside a set role).
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from domain.enums import ContextRole
from domain.thesis import (
    BasketMember,
    context_role_conflicts,
    with_inherited_context_roles,
)

A, B = uuid.UUID(int=0xA), uuid.UUID(int=0xB)


def _m(
    ticker: str,
    sid: uuid.UUID | None,
    role: ContextRole | None = None,
    segment: str | None = "Chain",
) -> BasketMember:
    return BasketMember(
        ticker=ticker, role="r", security_id=sid, segment=segment, context_role=role
    )


def test_the_six_roles_are_a_closed_set_with_these_wire_values():
    assert {r.value for r in ContextRole} == {
        "payer",
        "counter_case",
        "direct_read",
        "participant",
        "benchmark",
        "financier",
    }


def test_a_member_defaults_to_unset_and_never_to_a_role():
    assert BasketMember(ticker="X", role="r").context_role is None


def test_a_member_accepts_a_role_by_value_and_by_member():
    assert (
        BasketMember(ticker="X", role="r", context_role="payer").context_role is ContextRole.PAYER
    )
    assert (
        BasketMember(ticker="X", role="r", context_role=ContextRole.FINANCIER).context_role
        is ContextRole.FINANCIER
    )


@pytest.mark.parametrize(
    "bad", ["", "Payer", "counter-case", "watch", "entry_trigger", "risk_signal"]
)
def test_a_member_rejects_an_invalid_role(bad):
    """The free-text prose ``role`` and ``SignalEvent.role`` values are NOT context roles — the three never
    cross (the naming guard)."""
    with pytest.raises(ValidationError):
        BasketMember(ticker="X", role="r", context_role=bad)


def test_the_prose_role_field_is_untouched_by_the_context_role():
    m = BasketMember(ticker="X", role="the name", context_role="benchmark")
    assert m.role == "the name" and m.context_role is ContextRole.BENCHMARK


# --- context_role_conflicts ---------------------------------------------------------------------------


def test_one_company_with_two_different_roles_is_named_with_the_roles_it_was_sent():
    out = context_role_conflicts(
        [_m("ACME", A, ContextRole.PAYER, "L1"), _m("ACME", A, ContextRole.FINANCIER, "L2")]
    )
    assert len(out) == 1
    assert "ACME" in out[0] and "payer" in out[0] and "financier" in out[0]


def test_the_same_role_on_every_row_is_no_conflict():
    rows = [_m("ACME", A, ContextRole.PAYER, "L1"), _m("ACME", A, ContextRole.PAYER, "L2")]
    assert context_role_conflicts(rows) == []


def test_a_null_row_beside_a_set_role_is_no_conflict():
    """A NULL row inherits (``with_inherited_context_roles``); it is never a second role."""
    rows = [_m("ACME", A, ContextRole.PAYER, "L1"), _m("ACME", A, None, "L2")]
    assert context_role_conflicts(rows) == []


def test_all_null_is_no_conflict():
    assert context_role_conflicts([_m("ACME", A), _m("ACME", A, None, "L2")]) == []
    assert context_role_conflicts([]) == []


def test_every_conflicting_company_is_named_in_first_seen_order_and_others_are_left_out():
    out = context_role_conflicts(
        [
            _m("ZED", B, ContextRole.PAYER, "L1"),
            _m("ACME", A, ContextRole.PAYER, "L1"),
            _m("OKAY", uuid.UUID(int=0xC), ContextRole.BENCHMARK, "L1"),
            _m("ZED", B, ContextRole.DIRECT_READ, "L2"),
            _m("ACME", A, ContextRole.COUNTER_CASE, "L2"),
        ]
    )
    assert [s.split()[0] for s in out] == ["ZED", "ACME"]
    assert not any("OKAY" in s for s in out)


def test_a_third_distinct_role_is_counted_once_per_company():
    out = context_role_conflicts(
        [
            _m("ACME", A, ContextRole.PAYER, "L1"),
            _m("ACME", A, ContextRole.PAYER, "L2"),
            _m("ACME", A, ContextRole.FINANCIER, "L3"),
            _m("ACME", A, ContextRole.BENCHMARK, "L4"),
        ]
    )
    assert len(out) == 1 and "3 different" in out[0]


def test_rows_with_no_security_id_are_never_grouped():
    """Two id-less rows of the same ticker with different roles are NOT one company to this rule (unlike the
    silent-or-voting check, which falls back to the ticker): there is no identity to group on."""
    rows = [
        _m("ACME", None, ContextRole.PAYER, "L1"),
        _m("ACME", None, ContextRole.FINANCIER, "L2"),
    ]
    assert context_role_conflicts(rows) == []
    # …and an id-less row never conflicts with an identified one that shares its ticker
    mixed = [_m("ACME", A, ContextRole.PAYER, "L1"), _m("ACME", None, ContextRole.FINANCIER, "L2")]
    assert context_role_conflicts(mixed) == []


def test_two_listings_that_share_one_security_id_are_one_company():
    """After the promote's canonicalize pass, sibling listings of a company carry ONE security_id — the check
    keys on it, so a different role on each listing's row is a conflict."""
    rows = [_m("ASML", A, ContextRole.PAYER, "L1"), _m("ASML", A, ContextRole.BENCHMARK, "L2")]
    assert len(context_role_conflicts(rows)) == 1


# --- with_inherited_context_roles ---------------------------------------------------------------------


def test_a_null_row_beside_a_set_role_inherits_it():
    rows = [_m("ACME", A, ContextRole.PAYER, "L1"), _m("ACME", A, None, "L2")]
    out = with_inherited_context_roles(rows)
    assert [m.context_role for m in out] == [ContextRole.PAYER, ContextRole.PAYER]
    assert [m.segment for m in out] == ["L1", "L2"]  # nothing else about the row moves


def test_inheritance_works_whichever_row_comes_first():
    rows = [_m("ACME", A, None, "L1"), _m("ACME", A, ContextRole.BENCHMARK, "L2")]
    assert [m.context_role for m in with_inherited_context_roles(rows)] == [
        ContextRole.BENCHMARK,
        ContextRole.BENCHMARK,
    ]


def test_an_all_null_company_stays_null_never_defaulted():
    rows = [_m("ACME", A, None, "L1"), _m("ACME", A, None, "L2")]
    assert [m.context_role for m in with_inherited_context_roles(rows)] == [None, None]


def test_inheritance_never_crosses_companies():
    rows = [_m("ACME", A, ContextRole.PAYER), _m("OTHER", B, None)]
    out = with_inherited_context_roles(rows)
    assert [m.context_role for m in out] == [ContextRole.PAYER, None]


def test_rows_with_no_security_id_never_inherit_and_never_donate():
    rows = [
        _m("ACME", None, ContextRole.PAYER, "L1"),
        _m("ACME", None, None, "L2"),
        _m("ACME", A, None, "L3"),
    ]
    out = with_inherited_context_roles(rows)
    assert [m.context_role for m in out] == [ContextRole.PAYER, None, None]


def test_a_conflicted_company_is_left_exactly_as_sent():
    """Two different roles is the case the caller already refused; the helper guesses nothing — the NULL row
    stays NULL rather than taking either."""
    rows = [
        _m("ACME", A, ContextRole.PAYER, "L1"),
        _m("ACME", A, ContextRole.FINANCIER, "L2"),
        _m("ACME", A, None, "L3"),
    ]
    assert [m.context_role for m in with_inherited_context_roles(rows)] == [
        ContextRole.PAYER,
        ContextRole.FINANCIER,
        None,
    ]


def test_an_unchanged_row_is_the_same_object_and_the_argument_is_untouched():
    set_row, null_row, other = (
        _m("ACME", A, ContextRole.PAYER, "L1"),
        _m("ACME", A, None, "L2"),
        _m("Z", B),
    )
    rows = [set_row, null_row, other]
    out = with_inherited_context_roles(rows)
    assert out[0] is set_row and out[2] is other
    assert out[1] is not null_row and out[1].context_role is ContextRole.PAYER
    assert null_row.context_role is None and rows == [set_row, null_row, other]


def test_clearing_is_null_on_every_row_and_stays_cleared():
    """The operator clears a company's role by sending NULL on EVERY row; inheritance has nothing to give."""
    cleared = [_m("ACME", A, None, "L1"), _m("ACME", A, None, "L2")]
    assert context_role_conflicts(cleared) == []
    assert [m.context_role for m in with_inherited_context_roles(cleared)] == [None, None]
