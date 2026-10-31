"""The readers of the internal-transfer tag, on direct inputs — the two insider detectors and the Cockpit flow member
leave a screened leg out of their totals and NAME it (#6), and a read without one is byte-identical to before (no
record churn). The tag itself is set in the shared seam (``domain/insider_internal_transfers.py``); the real filings
are pinned in ``tests/ingest/test_form4_internal_transfers.py``."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

from domain.config import DEFAULT_CONFIG
from domain.insider_internal_transfers import INTERNAL_TRANSFER
from signals import insider_conviction, insider_sell
from signals.display import insider_flow

ASOF = date(2025, 3, 3)
D0 = date(2025, 2, 24)
SID = uuid4()
_TAG = {"with": ("SUJODA Investments, LP",), "evidence": ("named_holding",), "co_report": False}


def _row(name, role, usd, code, d=D0, tagged=False, **extra):
    t = {
        "txn_code": code,
        "usd": usd,
        "shares": usd / 21.85,
        "price": 21.85,
        "insider_name": name,
        "insider_role": role,
        "valid_from": d,
        "accession": f"acc-{name}-{code}",
        "aff_10b5_1": False,
        **extra,
    }
    if tagged:
        t[INTERNAL_TRANSFER] = dict(_TAG)
    return t


# --- the buy side ------------------------------------------------------------------------------------------------


def test_a_transfer_buy_is_no_conviction_and_is_named():
    real = _row("Jane Doe", "Director", 200_000.0, "P", d=date(2025, 2, 26))
    phantom = _row("SUJODA Investments, LP", "10% owner", 18_999_995.25, "P", tagged=True)
    fired = insider_conviction.score([real, phantom], SID, ASOF, DEFAULT_CONFIG)
    assert fired is not None
    assert fired.label == (
        "1 insider incl. senior officer bought $200,000 open-market (code P) across 1 txns"
        " (traded 2025-02-26); 1 internal-transfer txn ($18,999,995) screened"
    )
    assert [p.ref for p in fired.provenance] == [
        "acc-Jane Doe-P"
    ]  # the transfer's filing is not cited as a buy
    # untagged, the same buy is counted — the whole difference is the tag
    counted = insider_conviction.score(
        [real, {**phantom, INTERNAL_TRANSFER: None}], SID, ASOF, DEFAULT_CONFIG
    )
    assert counted.label.startswith("2 insiders incl. senior officer bought $19,199,995")
    # a transfer alone is no buy at all
    assert insider_conviction.score([phantom], SID, ASOF, DEFAULT_CONFIG) is None


def test_a_read_without_a_transfer_keeps_its_exact_label():
    real = _row("Jane Doe", "Director", 200_000.0, "P", d=date(2025, 2, 26))
    fired = insider_conviction.score([real], SID, ASOF, DEFAULT_CONFIG)
    assert fired.label == (
        "1 insider incl. senior officer bought $200,000 open-market (code P) across 1 txns"
        " (traded 2025-02-26)"
    )
    # a transfer OUTSIDE the chosen cluster's window is not named on it
    old = _row(
        "SUJODA Investments, LP", "10% owner", 9_000_000.0, "P", d=date(2024, 12, 1), tagged=True
    )
    assert insider_conviction.score([real, old], SID, ASOF, DEFAULT_CONFIG).label == fired.label


# --- the sell side -----------------------------------------------------------------------------------------------


def test_transfer_sales_are_screened_and_named_beside_a_real_cluster():
    kept = [
        _row("Jane Doe", "Chief Executive Officer", 400_000.0, "S", d=date(2025, 2, 25)),
        _row("John Roe", "Chief Financial Officer", 350_000.0, "S", d=date(2025, 2, 26)),
    ]
    james = _row("KIM JAMES J", "10% owner", 18_999_995.25, "S", tagged=True)
    agnes = _row("KIM AGNES C", "10% owner", 18_999_995.25, "S", tagged=True)
    fired = insider_sell.score([*kept, james, agnes], SID, ASOF, DEFAULT_CONFIG)
    assert fired is not None
    assert fired.label == (
        "2 insiders incl. senior officer sold $750,000 open-market (code S) across 2 txns; "
        "2 internal-transfer txns ($37,999,990) screened"
    )
    detail = fired.provenance[0].detail
    assert detail["internal_transfer_screened"] == 2
    assert detail["internal_transfer_screened_usd"] == 37_999_990.5
    # no transfer in the window -> the detail carries no transfer keys at all (no record churn)
    plain = insider_sell.score(kept, SID, ASOF, DEFAULT_CONFIG)
    assert "internal_transfer_screened" not in plain.provenance[0].detail
    assert (
        plain.label
        == "2 insiders incl. senior officer sold $750,000 open-market (code S) across 2 txns"
    )


def test_the_transfer_bucket_is_structural_before_the_plan_flag():
    t = _row("KIM JAMES J", "10% owner", 1_000.0, "S", tagged=True, aff_10b5_1=True)
    assert insider_sell._screen(t, {}, None, DEFAULT_CONFIG) == "internal_transfer"
    assert (
        insider_sell._screen({**t, INTERNAL_TRANSFER: None}, {}, None, DEFAULT_CONFIG) == "planned"
    )


# --- the Cockpit flow ------------------------------------------------------------------------------------------


def test_the_flow_sets_both_legs_aside_and_says_so():
    rows = [
        _row("SUJODA Investments, LP", "10% owner", 18_999_995.25, "P", tagged=True),
        _row("KIM JAMES J", "10% owner", 18_999_995.25, "S", tagged=True),
        _row("Jane Doe", "Director", 200_000.0, "P", d=date(2025, 2, 26)),
    ]
    sig = insider_flow.compute(rows, ASOF)
    m = {x.key: x.value for x in sig.metrics}
    assert (m["buy_count"], m["sell_count"], m["buy_usd"]) == (1, 0, 200_000.0)
    assert (
        "set aside 2 internal-transfer txns (~$38M) — a buy and a sale between linked filers"
        in sig.basis.note
    )
    assert insider_flow._screen(rows[0], {}, None) == insider_flow.INTERNAL_TRANSFER
    # without a transfer the note is exactly as before
    plain = insider_flow.compute(rows[2:], ASOF)
    assert (
        plain.basis.note
        == "from ingested Form 4 only — zero is zero ingested, not proven-zero filings"
    )
