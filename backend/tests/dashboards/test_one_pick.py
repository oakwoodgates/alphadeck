"""V2 W2a: ONE pick for a platform value (``domain.platform_values.pick_current``), used by every reader.

Before V2 each reader of ``fact_shares_outstanding`` / ``fact_cash_burn`` chose "the value" by its own inline
``max(rows, key=valid_from)``. On a date tie that kept the FIRST row, and the as-of read sorts rows by
``source_ref``, so the alphabetically first filing URL won, whatever was written last. Approved decision D1 (a):
the latest date wins, a date tie goes to the newest write (``recorded_at``, then ``id``).

(a) A guard: every product file that reads these tables either imports ``pick_current``, or is named below with
    the reason it does not pick (and the module it hands its rows to, which must import it). No such file may
    pick by a ``valid_from`` key of its own. A stale entry fails too.
(b) Behavior: two share rows dated the same day, the OLDER write under the alphabetically FIRST URL. The Workbench
    meters, the Hot Board cap, the data flags, the Market Map's and the Track Record's pick all read the newer
    write.

Display only: neither table feeds a signal or the call.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from db.bitemporal import append_fact
from domain.config import DEFAULT_CONFIG
from domain.scopes import DATA_TENANT_ID

_BACKEND = Path(__file__).resolve().parents[2]
_ACCESSORS = ("shares_outstanding_facts(", "cash_burn_facts(")
_PICK = "pick_current"

# The readers that do not pick "the value" themselves, each with its reason. A delegate module is held to the
# same rule as a direct reader (it must import ``pick_current`` and must not pick by its own key).
_NO_PICK: dict[str, str] = {
    "signals/base.py": "the accessors' own definition",
    "dashboards/filings.py": "lists EVERY per-filing row (the Filing Stream, the ledger): no single value shown",
    "app/routers/dashboards/track_record.py": "hands the rows to dashboards/market_cap.py::shares_at_event",
    "app/routers/dashboards/market_map.py": "hands the rows to dashboards/market_cap.py::pick_shares",
}
_DELEGATES = ("dashboards/market_cap.py",)
# a ``valid_from``-keyed max / min / sorted in a reader that is NOT over share or cash rows: (file, function)
_OTHER_ROWS = {("dashboards/market_cap.py", "cover_shares")}  # the filing cover's fundamentals rows


def _product_sources() -> Iterator[Path]:
    for d in sorted(_BACKEND.iterdir()):
        if d.is_dir() and not d.name.startswith(".") and d.name not in ("tests", "__pycache__"):
            yield from sorted(d.rglob("*.py"))


def _readers() -> list[str]:
    return sorted(
        p.relative_to(_BACKEND).as_posix()
        for p in _product_sources()
        if any(a in p.read_text(encoding="utf-8") for a in _ACCESSORS)
    )


def _imports_pick(rel: str) -> bool:
    tree = ast.parse((_BACKEND / rel).read_text(encoding="utf-8"))
    return any(
        isinstance(n, ast.ImportFrom)
        and n.module == "domain.platform_values"
        and any(a.name == _PICK for a in n.names)
        for n in ast.walk(tree)
    )


def _own_valid_from_picks(rel: str) -> list[str]:
    """``max`` / ``min`` / ``sorted`` / ``.sort`` calls whose ``key=`` reads ``valid_from``, as ``function:line``."""
    tree = ast.parse((_BACKEND / rel).read_text(encoding="utf-8"))
    found: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if (rel, fn.name) in _OTHER_ROWS:
            continue
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name not in ("max", "min", "sorted", "sort"):
                continue
            for kw in node.keywords:
                if kw.arg == "key" and "valid_from" in ast.unparse(kw.value):
                    found.append(f"{fn.name}:{node.lineno}")
    return found


# --- (a) the guard -------------------------------------------------------------------------------------------


def test_every_reader_uses_the_one_pick():
    readers = _readers()
    assert readers, "the accessor names moved: re-pin this guard"
    stale = sorted(set(_NO_PICK) - set(readers))
    assert stale == [], f"these no longer read the tables: drop them from _NO_PICK: {stale}"
    own = [r for r in readers if r not in _NO_PICK and not _imports_pick(r)]
    assert own == [], (
        f"{own} read fact_shares_outstanding / fact_cash_burn: choose the value with "
        "domain.platform_values.pick_current (or name the file in _NO_PICK with the reason it shows no single "
        "value)"
    )
    for d in _DELEGATES:
        assert _imports_pick(d), f"{d} picks for a reader: it must use pick_current"


def test_no_reader_picks_by_a_valid_from_key_of_its_own():
    files = [r for r in _readers() if r != "signals/base.py"] + list(_DELEGATES)
    own = {r: picks for r in files if (picks := _own_valid_from_picks(r))}
    assert (
        own == {}
    ), f"pick the value with domain.platform_values.pick_current, not a key of your own: {own}"


# --- (b) the behavior: a same-date tie goes to the newer write ------------------------------------------------

_DAY = date(2026, 6, 30)
_ASOF = date(2026, 10, 30)
_KNOWN = datetime(2100, 1, 1, tzinfo=timezone.utc)
_FIRST_URL = "https://www.sec.gov/Archives/edgar/data/1/a-older.htm"  # sorts first, written first
_SECOND_URL = "https://www.sec.gov/Archives/edgar/data/1/b-newer.htm"  # sorts second, written later
_OLD_COUNT, _NEW_COUNT = 10_000_000.0, 20_000_000.0


def _share(db, sid: UUID, url: str, count: float, written: datetime, **extra) -> None:
    append_fact(
        db,
        "fact_shares_outstanding",
        {
            "tenant_id": DATA_TENANT_ID,
            "security_id": sid,
            "shares": count,
            "source": "cover",
            "source_ref": url,
            "valid_from": _DAY,
            "ratified_by": "auto",
            "recorded_at": written,
            **extra,
        },
    )


def _cash(db, sid: UUID, url: str, cash: float, written: datetime) -> None:
    append_fact(
        db,
        "fact_cash_burn",
        {
            "tenant_id": DATA_TENANT_ID,
            "security_id": sid,
            "cash_usd": cash,
            "quarterly_burn_usd": 3_000_000.0,
            "source": "10-Q",
            "source_ref": url,
            "valid_from": _DAY,
            "ratified_by": "auto",
            "recorded_at": written,
        },
    )


@pytest.fixture
def tie(db) -> UUID:
    """One company with a same-date tie on both tables: the older write under the first-sorting URL."""
    sid = uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) VALUES (%s, %s, %s, %s, %s)",
            (sid, DATA_TENANT_ID, "TIE", "0000000001", date(2026, 1, 1)),
        )
    t0 = datetime(2026, 7, 1, tzinfo=timezone.utc)
    _share(db, sid, _FIRST_URL, _OLD_COUNT, t0)
    _share(db, sid, _SECOND_URL, _NEW_COUNT, t0 + timedelta(days=1), ads_ratio_status="unread")
    _cash(db, sid, _FIRST_URL, 1_000_000.0, t0)
    _cash(db, sid, _SECOND_URL, 9_000_000.0, t0 + timedelta(days=1))
    db.commit()
    return sid


def _pit(db, sid: UUID, clock: str = "record"):
    from signals.base import PointInTimeData

    return PointInTimeData(
        db,
        asof=_ASOF,
        known_at=_KNOWN,
        workspace=DATA_TENANT_ID,
        basket=[sid],
        clock=clock,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize("clock", ["record", "public"])
def test_the_workbench_meters_show_the_newer_write(db, tie, clock):
    from workbench.scoring import _market_cap, _runway

    pit = _pit(db, tie, clock)
    cap = _market_cap(pit, tie)  # no price yet: the count on file rides the provenance
    assert cap.provenance[0].ref == _SECOND_URL
    assert cap.provenance[0].detail["shares"] == _NEW_COUNT
    runway = _runway(pit, tie, DEFAULT_CONFIG)
    assert runway.provenance[0].ref == _SECOND_URL
    assert runway.provenance[0].detail["cash_usd"] == 9_000_000.0


def test_the_hot_board_cap_shows_the_newer_write(db, tie):
    from app.routers.dashboards.hot_board import read_tape

    tape = read_tape(_pit(db, tie), tie, _ASOF, {}, None, None, None)
    assert tape.cap.shares == _NEW_COUNT


def test_the_data_flags_read_the_newer_write(db, tie):
    """The newer write's ADS ratio is unread: the cap's flag fires only when the flags read that row."""
    from pipeline.data_flags import read_flags

    append_fact(
        db,
        "fact_price_eod",
        {
            "tenant_id": DATA_TENANT_ID,
            "security_id": tie,
            "d": date(2026, 10, 29),
            "close": 5.0,
            "volume": 1000.0,
            "valid_from": date(2026, 10, 29),
            "recorded_at": datetime(2026, 10, 29, 22, tzinfo=timezone.utc),
        },
    )
    db.commit()
    flags = read_flags(
        db,
        tenant_id=DATA_TENANT_ID,
        security_ids=[tie],
        tickers={tie: "TIE"},
        asof=_ASOF,
        known_at=_KNOWN,
        clock="public",
        stale_days=30,
    )
    kinds = {f.kind for f in flags[tie].flags}
    assert "market_cap_share_count" in kinds


def test_the_market_map_and_track_record_picks_show_the_newer_write():
    from dashboards.market_cap import pick_shares, shares_at_event

    t0 = datetime(2026, 7, 1, tzinfo=timezone.utc)
    older = {
        "id": UUID(int=2),
        "shares": _OLD_COUNT,
        "valid_from": _DAY,
        "source_ref": _FIRST_URL,
        "recorded_at": t0,
    }
    newer = {
        "id": UUID(int=1),
        "shares": _NEW_COUNT,
        "valid_from": _DAY,
        "source_ref": _SECOND_URL,
        "recorded_at": t0 + timedelta(days=1),
    }
    assert pick_shares([older, newer], (), _ASOF).shares["shares"] == _NEW_COUNT
    assert shares_at_event([older, newer], (), _ASOF).shares["shares"] == _NEW_COUNT
