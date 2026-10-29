"""The cash reader's LABEL rule (data hygiene, 2026-10-24): each cash input reads its most CURRENT label.

The old rule (``_first``) kept the first candidate label a company ever used, so a filer that moved its cash to
another tag read the old tag's last balance. MEASURED over prod's cached companyfacts for 937 basket companies
with US-GAAP data: 2 picked an older known label over a current one, 80 were stale under a label the reader did
not know (66 the restricted-cash total, 14 plain ``Cash``), 40 had cash ONLY under those labels (read none), and
13 left their current marketable securities out under an older label. Re-run with the new rule: 895 current, 0 of
those four classes left (the 5 of the 40 whose newest cash is itself old now read "stale-cash", honestly).

The fixtures are REAL documents: ``tests/fixtures/edgar/companyfacts_cash/<TICKER>.json`` = data.sec.gov
companyfacts as prod's EDGAR cache held it on 2026-10-24 (copied read-only), cut to ``cik`` / ``entityName`` and
the cash, marketable-securities and operating-cash-flow nodes present, every node verbatim (the source's own
spelling and keys). Nothing is edited.

- ``TNC`` — ``CashAndCashEquivalentsAtCarryingValue`` ends 2018-12-31; the restricted-cash total runs to
  2026-06-30.
- ``GAME`` — no cash-and-equivalents tag at all: plain ``Cash`` and the restricted-cash total, both to
  2026-06-30 (the tie: the balance-sheet line wins).
- ``CLOV`` — the known ``...IncludingDiscontinuedOperations`` label to 2026-06-30, the plain one to 2025-12-31.
- ``AKAM`` — cash-and-equivalents and the restricted-cash total both to 2026-06-30 (the tie); its securities
  moved from ``AvailableForSaleSecurities*`` (to 2019) to ``MarketableSecurities*`` (to 2026-06-30).
- ``STT`` — the restricted-cash total only.

Display-only: the cash reading feeds the Workbench runway meter alone (``workbench/scoring.py:_runway`` reads
``fact_cash_burn``, written only when the operator ratifies), never a call.
"""

from __future__ import annotations

import ast
import json
from datetime import date
from pathlib import Path

import pytest

from domain.config import ExtractorConfig
from domain.extraction import Tier
from ingest.edgar import extract as ex

_FX = Path(__file__).resolve().parents[1] / "fixtures" / "edgar" / "companyfacts_cash"
_PERIOD = date(2026, 6, 30)  # every fixture's latest balance sheet (the 10-Q period it is read for)
_RESTRICTED = "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"


def _facts(ticker: str) -> dict:
    return json.loads((_FX / f"{ticker}.json").read_text(encoding="utf-8"))["facts"]


def _old_rule(facts: dict) -> tuple[float, str] | None:
    """The rule this replaces, replayed: the FIRST present of the two labels the reader knew."""
    rows = ex._first(facts, ex._CASH[:2], "USD")
    if not rows:
        return None
    end = max(r["end"] for r in rows)
    at = [r for r in rows if r["end"] == end]
    return float(max(at, key=lambda r: r.get("filed", ""))["val"]), end


def _cb(ticker: str):
    return ex._cash_burn(_facts(ticker), "", "10-Q", _PERIOD, ExtractorConfig())


def test_tnc_reads_the_restricted_cash_total_and_says_so():
    facts = _facts("TNC")
    assert _old_rule(facts) == (85_609_000.0, "2018-12-31")  # the old reading: eight years stale
    assert ex._pick_instant(facts, ex._CASH) == (76_900_000.0, "2026-06-30", _RESTRICTED)
    cb = _cb("TNC")
    assert cb.cash_usd == 76_900_000.0
    assert "stale-cash" not in cb.flags and cb.tier is Tier.AUTO
    assert "includes restricted cash" in cb.note
    assert f"cash as of 2026-06-30 (tagged {_RESTRICTED})" in cb.note


def test_game_reads_plain_cash_the_balance_sheet_line_winning_the_tie():
    """Plain ``Cash`` and the restricted-cash total end on the same date: the balance-sheet line wins (the
    listed order breaks a tie), so the value excludes restricted cash and the note does not say it includes
    it. The old rule read nothing: a ``no-cash-instant`` FLAG with no cash at all."""
    facts = _facts("GAME")
    assert _old_rule(facts) is None
    assert ex._pick_instant(facts, ex._CASH) == (2_101_751.0, "2026-06-30", "Cash")
    cb = _cb("GAME")
    assert cb.cash_usd == 2_101_751.0 and "no-cash-instant" not in cb.flags
    assert "(tagged Cash)" in cb.note and "includes restricted cash" not in cb.note


def test_clov_reads_the_newer_known_label_over_the_first_listed():
    facts = _facts("CLOV")
    assert _old_rule(facts) == (78_301_000.0, "2025-12-31")
    picked = ex._pick_instant(facts, ex._CASH)
    assert picked == (
        198_837_000.0,
        "2026-06-30",
        "CashAndCashEquivalentsAtCarryingValueIncludingDiscontinuedOperations",
    )
    assert "stale-cash" not in _cb("CLOV").flags


def test_akam_keeps_the_balance_sheet_cash_and_includes_its_current_securities():
    """Two ties and a securities move: cash-and-equivalents beats the same-dated restricted-cash total
    (1,480,257,000, not 1,481,739,000), and the securities read the ``MarketableSecurities*`` labels that sit
    on the CURRENT balance sheet instead of the ``AvailableForSaleSecurities*`` ones that stopped in 2019 — so
    the same-dated rule now includes them (the old reading left $3.1B of securities out)."""
    facts = _facts("AKAM")
    assert ex._pick_instant(facts, ex._CASH) == (
        1_480_257_000.0,
        "2026-06-30",
        "CashAndCashEquivalentsAtCarryingValue",
    )
    assert ex._latest_instant(facts, ex._STI) == (1_875_130_000.0, "2026-06-30")
    assert ex._latest_instant(facts, ex._LTI) == (1_260_918_000.0, "2026-06-30")
    cb = _cb("AKAM")
    assert cb.cash_usd == 1_480_257_000.0 + 1_875_130_000.0 + 1_260_918_000.0
    assert cb.tier is Tier.AUTO and cb.flags == []
    assert "marketable as of 2026-06-30" in cb.note and "(tagged" not in cb.note


def test_stt_reads_cash_only_reported_as_the_restricted_cash_total():
    facts = _facts("STT")
    assert _old_rule(facts) is None
    cb = _cb("STT")
    assert cb.cash_usd == 4_290_000_000.0 and "no-cash-instant" not in cb.flags
    assert "includes restricted cash" in cb.note


def _inst(end: str, val: float) -> dict:
    return {"end": end, "val": val, "filed": end}


def _synthetic(**nodes: list[dict]) -> dict:
    return {"us-gaap": {c: {"units": {"USD": rows}} for c, rows in nodes.items()}}


def test_the_newest_label_wins_and_a_tie_keeps_the_listed_order():
    newer = _synthetic(
        CashAndCashEquivalentsAtCarryingValue=[_inst("2025-12-31", 10.0)],
        Cash=[_inst("2026-03-31", 7.0)],
    )
    assert ex._pick_instant(newer, ex._CASH) == (7.0, "2026-03-31", "Cash")
    tie = _synthetic(
        CashAndCashEquivalentsAtCarryingValue=[_inst("2026-03-31", 10.0)],
        Cash=[_inst("2026-03-31", 7.0)],
        **{_RESTRICTED: [_inst("2026-03-31", 12.0)]},
    )
    assert ex._pick_instant(tie, ex._CASH) == (
        10.0,
        "2026-03-31",
        "CashAndCashEquivalentsAtCarryingValue",
    )
    assert ex._pick_instant(_synthetic(), ex._CASH) is None


def test_no_cash_label_is_still_never_a_fake_zero():
    """The 'never a fake $0' rule holds under the wider label set: a company with none of the labels reads
    None and FLAGs, it never reads $0."""
    cb = ex._cash_burn(
        _synthetic(CashAndDueFromBanks=[_inst("2026-06-30", 5.0)]),
        "",
        "10-Q",
        _PERIOD,
        ExtractorConfig(),
    )
    assert cb.cash_usd is None and cb.tier is Tier.FLAG


_BACKEND = Path(__file__).resolve().parents[2]


def _product_sources() -> list[Path]:
    """Every product module (the backend packages, not the tests or the venv)."""
    out: list[Path] = []
    for d in sorted(_BACKEND.iterdir()):
        if d.is_dir() and not d.name.startswith(".") and d.name not in ("tests", "__pycache__"):
            out += sorted(d.rglob("*.py"))
    return out


@pytest.mark.parametrize("pkg", ["calls", "signals"])
def test_nothing_on_the_call_path_imports_the_extractor(pkg):
    for src in sorted((_BACKEND / pkg).rglob("*.py")):
        for node in ast.walk(ast.parse(src.read_text(encoding="utf-8"))):
            mods = (
                [node.module]
                if isinstance(node, ast.ImportFrom) and node.module
                else [a.name for a in node.names] if isinstance(node, ast.Import) else []
            )
            for mod in mods:
                assert not mod.startswith(("ingest.edgar.extract", "workbench")), (src, mod)


def test_the_one_reader_of_the_stored_cash_fact_is_the_runway_meter():
    """VERIFIED 2026-10-24 and pinned: ``fact_cash_burn`` (what a ratified cash reading becomes) is read
    through ``cash_burn_facts`` only by the Workbench runway meter, never by a detector or the call.
    Since 2026-10-29 the display-only dashboards (``dashboards/``) may read it too, to LIST the stored
    fact as a row (the Filing Stream's shared reader, the Peer Table's runway column): a dashboard is a
    leaf the call path can never import (``docs/DASHBOARDS.md`` rule 1, enforced by
    ``tests/dashboards/test_isolation.py``), so the pin holds: no detector, no call.
    """
    readers = [p for p in _product_sources() if "cash_burn_facts(" in p.read_text(encoding="utf-8")]
    paths = sorted(p.relative_to(_BACKEND).as_posix() for p in readers)
    display_only = [r for r in paths if r.startswith("dashboards/")]
    assert [r for r in paths if r not in display_only] == [
        "signals/base.py",  # the accessor's own definition on the concrete point-in-time view
        "workbench/scoring.py",  # _runway — the runway meter
    ]
    assert display_only, "the dashboards' readers moved: re-pin this list"
