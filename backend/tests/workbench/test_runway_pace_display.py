"""The runway's PACE on every screen that shows it (V2 extractor correctness PR 3; operator decision D3).

The runway is read at LAST QUARTER'S pace. Where the stored note carries the year-to-date pace
(``domain.burn_pace``; the note is its only storage until a column carries it), each runway reader puts that pace,
the runway it implies, and (only when last quarter is far off it) the caveat sentence beside the runway. Display
only: the pips, the months, the fit label and the sort never read it (the byte-identical pins below), and nothing
under ``calls/`` or ``signals/`` imports it.

The notes are the extractor's own, made from the reconstructed fixtures in ``tests/fixtures/edgar/burn_pace/``
(see ``tests/ingest/test_burn_pace.py``).
"""

from __future__ import annotations

import ast
import json
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from app.routers import admin_platform_values as apv
from dashboards.filings import Who, rows_from_ratified
from dashboards.peer_table import runway_cell
from domain.config import DEFAULT_CONFIG, ExtractorConfig
from ingest.edgar import extract as ex
from workbench import scoring

_ROOT = Path(__file__).resolve().parents[2]
_FX = _ROOT / "tests" / "fixtures" / "edgar"
_SID = uuid.UUID(int=7)
_UP_CAVEAT = (
    "Last quarter's burn ($17.1M) is well below the year-to-date pace ($69.9M a quarter): runway reads 9.1 months "
    "at last quarter's pace and 2.2 months at the year-to-date pace."
)


def _extracted(ticker: str, folder: str = "burn_pace"):
    facts = json.loads((_FX / folder / f"{ticker}.json").read_text(encoding="utf-8"))["facts"]
    return ex._cash_burn(facts, "", "https://x.test/10q", date(2026, 9, 30), ExtractorConfig())


def _row(ticker: str, folder: str = "burn_pace", *, note: bool = True) -> dict[str, Any]:
    """The stored row the machine writer makes from an AUTO extraction (the values and the note)."""
    cb = _extracted(ticker, folder)
    return {
        "id": uuid.UUID(int=1),
        "cash_usd": cb.cash_usd,
        "quarterly_burn_usd": cb.quarterly_burn_usd,
        "note": cb.note if note else None,
        "source": "10-q",
        "source_ref": "https://x.test/10q",
        "ratified_by": "auto",
        "valid_from": date(2026, 9, 30),
        "recorded_at": datetime(2026, 11, 7, 12, tzinfo=timezone.utc),
    }


class _Pit:
    def __init__(self, row: dict[str, Any]) -> None:
        self.row = row

    def cash_burn_facts(self, sid):
        return [self.row]


def _runway(row: dict[str, Any]):
    return scoring._runway(_Pit(row), _SID, DEFAULT_CONFIG)


# --- the scored meter's provenance (the Workbench rail, the Cockpit name panel) ------------------------------


def test_the_meter_carries_the_pace_and_the_caveat_in_its_provenance():
    fig = _runway(_row("UP"))
    d = fig.provenance[0].detail
    assert d["ytd_quarterly_burn_usd"] == 69_943_025
    assert d["ytd_burn_days"] == 272
    assert d["ytd_runway_months"] == 2.2
    assert d["pace_caveat"] == _UP_CAVEAT


@pytest.mark.parametrize(
    ("ticker", "folder"),
    [
        ("UP", "burn_pace"),
        ("RCEL", "burn_pace"),
        ("AAL", "burn_pace"),
        ("RXRX", "burn_pace"),
        ("EHC", "burn_period"),
    ],
)
def test_the_runway_pips_and_months_are_byte_identical_with_or_without_the_pace(
    ticker: str, folder: str
):
    """The display-only pin: the pace never touches the pips or the months."""
    with_pace, without = _runway(_row(ticker, folder)), _runway(_row(ticker, folder, note=False))
    assert (with_pace.pips, with_pace.value) == (without.pips, without.value)
    assert scoring._fit(None, with_pace.pips, None) == scoring._fit(None, without.pips, None)


def test_a_steady_burner_shows_its_pace_quietly_with_no_caveat():
    d = _runway(_row("RXRX")).provenance[0].detail
    assert d["ytd_runway_months"] == 18.6 and "pace_caveat" not in d


def test_a_cash_generator_has_a_pace_but_no_runway_and_no_caveat():
    fig = _runway(_row("EHC", "burn_period"))
    d = fig.provenance[0].detail
    assert fig.value is None  # cash-generative
    assert d["ytd_quarterly_burn_usd"] < 0
    assert "ytd_runway_months" not in d and "pace_caveat" not in d


def test_a_value_without_a_pace_reads_exactly_as_before():
    d = _runway(_row("UP", note=False)).provenance[0].detail
    # who set it is a kind (W5d): ``set_by``, never the stored ``ratified_by``
    assert set(d) == {"cash_usd", "quarterly_burn_usd", "set_by"} and d["set_by"] == "machine"


# --- the Peer Table cell -------------------------------------------------------------------------------------


def test_the_peer_table_cell_carries_the_pace_beside_the_months():
    c = runway_cell(_row("UP"))
    assert (c.months, c.ytd_months, c.pace_caveat) == (9.1, 2.2, _UP_CAVEAT)
    assert c.ytd_quarterly_burn_usd == 69_943_025
    plain = runway_cell(_row("UP", note=False))
    assert (plain.months, plain.ytd_quarterly_burn_usd, plain.ytd_months, plain.pace_caveat) == (
        9.1,
        None,
        None,
        None,
    )


def test_the_peer_table_cell_names_the_opposite_sign():
    c = runway_cell(_row("AAL"))
    assert c.months == 162.5 and c.ytd_months is None
    assert (
        c.pace_caveat is not None and "the year to date generated $1.5B a quarter" in c.pace_caveat
    )


# --- the Filing Stream row -----------------------------------------------------------------------------------

_W = Who(security_id=_SID, ticker="UP", name="Wheels Up", cik=None)


def test_the_filing_stream_row_says_last_quarter_and_the_pace_and_carries_the_caveat():
    (r,) = rows_from_ratified([_row("UP")], [], [], _W)
    assert (
        r.headline
        == "10-q: cash $51.8M · burn $17.1M/qtr last quarter · year-to-date pace burn $69.9M/qtr"
    )
    assert _UP_CAVEAT in r.detail
    (steady,) = rows_from_ratified([_row("RXRX")], [], [], _W)
    assert "year-to-date pace burn $88.8M/qtr" in steady.headline
    assert not any("year-to-date pace (" in line for line in steady.detail)
    (plain,) = rows_from_ratified([_row("UP", note=False)], [], [], _W)
    assert plain.headline == "10-q: cash $51.8M · burn $17.1M/qtr"


# --- the steward page ----------------------------------------------------------------------------------------


def test_the_steward_reads_the_pace_of_a_value_and_of_a_candidate():
    p = apv._pace(_row("UP"))
    assert p is not None and p.ytd_runway_months == 2.2 and p.caveat == _UP_CAVEAT
    assert apv._pace(_row("UP", note=False)) is None
    assert (
        apv._pace({"shares": 1e6, "note": _row("UP")["note"]}) is None
    )  # a share count has no burn
    cand = {
        **_row("UP"),
        "security_id": _SID,
        "fact_type": "cash_burn",
        "outcome": "flag",
        "flags": ["stale-cash"],
        "located_passages": [],
    }
    out = apv._candidate(cand)
    assert out.pace is not None and out.pace.caveat == _UP_CAVEAT


# --- display only --------------------------------------------------------------------------------------------


def test_nothing_in_calls_or_signals_imports_the_pace():
    """The call path cannot read the pace: no module under ``calls/`` or ``signals/`` imports ``domain.burn_pace``,
    ``workbench.scoring``, ``dashboards`` or the steward routes."""
    banned = (
        "domain.burn_pace",
        "workbench.scoring",
        "dashboards",
        "app.routers.admin_platform_values",
    )
    for pkg in ("calls", "signals"):
        for path in (_ROOT / pkg).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = (
                    [a.name for a in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
                )
                for n in names:
                    assert not any(n == b or n.startswith(b + ".") for b in banned), (path, n)
