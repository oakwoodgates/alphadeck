"""The repair for EXPLICIT (ticker, date) pairs on any security — ``repair_benchmark_bars.repair_bars`` and
the CLI's ``--bars`` / ``--ticker``: the seven bars whose only stored version is a pre-settle pull.

REAL data: ``tests/fixtures/prices/settle_repair_2026_07.json`` holds, read-only from prod on 2026-10-21,
every version prod stored for CBT 2026-06-29..07-20 and SCE-PG 2026-08-24..08-31 (prod's own
``recorded_at`` stamps — CBT's 07-09 bar is the 11:07 ET pull, its only version) and the Yahoo chart payload
the CLI's dry run pulled that evening at the nightly range (CBT trimmed to the window; SCE-PG whole — Yahoo
serves ONE bar, the current day, for it under every range). The store is seeded through the real append
writer with those stamps; a stub ``PriceSource`` serves the vendor payload offline. Every write assertion
COUNTS THE TABLE. The basis check and the symbol rule are stated as contracts with direct inputs.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from db.session import DEFAULT_TENANT_ID
from ingest.prices.eod_loader import ingest_prices, parse_yahoo_chart
from ingest.prices.source import DEFAULT_RANGE as NIGHTLY_RANGE
from pipeline import repair_benchmark_bars as RB
from pipeline.repair_benchmark_bars import (
    APPEND,
    BASIS_DIFFERS,
    NO_VENDOR_BAR,
    ScopeRefused,
    _parse_bars,
    basis_disagreement,
    neighbor_dates,
    repair_bars,
    resolve_targets,
)
from repositories import price_feed_repo

ET = ZoneInfo("America/New_York")
NOW = datetime(
    2026, 10, 21, 19, 0, tzinfo=ET
)  # the repair's pinned instant (the guard + the stamp)
J09, A27 = date(2026, 7, 9), date(2026, 8, 27)
_FIX = json.loads(
    (
        Path(__file__).resolve().parents[1] / "fixtures" / "prices" / "settle_repair_2026_07.json"
    ).read_text(encoding="utf-8")
)


def _vendor(ticker: str) -> list[dict]:
    return parse_yahoo_chart(_FIX[ticker]["vendor_chart"])


class _Source:
    """A PriceSource stub serving the vendor's REAL payload per SYMBOL; records each call."""

    def __init__(self, bars: dict[str, list[dict]] | None = None):
        self.bars = (
            bars if bars is not None else {"CBT": _vendor("CBT"), "SCE-PG": _vendor("SCE-PG")}
        )
        self.calls: list[tuple[str, bool, bool]] = []

    def get_bars(self, ticker, *, allow_live=False, force_refresh=False):
        self.calls.append((ticker, allow_live, force_refresh))
        return list(self.bars.get(ticker, []))


def _security(db, ticker: str, *, price_symbol: str | None = None) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, price_symbol, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, price_symbol, "0001234567", "2020-01-01"),
        )
    db.commit()
    return sid


def _seed(db, ticker: str) -> uuid.UUID:
    """Every version prod held for the window, through the REAL append writer with prod's own stamps."""
    sid = _security(db, ticker)
    for r in _FIX[ticker]["stored_prod"]:
        row = {**r, "d": date.fromisoformat(r["d"])}
        ingest_prices(db, sid, [row], recorded_at=datetime.fromisoformat(r["recorded_at"]))
    db.commit()
    return sid


def _px_count(db) -> int:
    db.rollback()  # a fresh snapshot (the CLI commits on its own connection)
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM fact_price_eod")
        return cur.fetchone()["n"]


def _versions(db, sid, d) -> list[dict]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT close, volume, valid_from, recorded_at FROM fact_price_eod "
            "WHERE security_id = %s AND d = %s ORDER BY recorded_at",
            (sid, d),
        )
        return cur.fetchall()


# --- CBT 2026-07-09: the 11:07 ET pull is the only version --------------------------------------------


def test_CBT_the_dry_run_shows_the_early_pull_against_the_vendor_final_and_writes_nothing(db):
    _seed(db, "CBT")
    before = _px_count(db)

    result = repair_bars(db, [("CBT", J09)], source=_Source(), now=NOW)

    assert _px_count(db) == before and result.appended == 0 and result.recorded_at is None
    (b,) = result.bars  # only the NAMED pair is compared — never the rest of the tape
    assert (b.ticker, b.d, b.action) == ("CBT", J09, APPEND)
    assert {"close", "volume"} <= set(b.changed)
    assert float(b.stored["close"]) == pytest.approx(85.910004, abs=1e-6)  # 11:07 ET, in progress
    assert float(b.vendor["close"]) == pytest.approx(85.519997, abs=1e-6)  # the settled close
    assert int(b.stored["volume"]) == 88210 and int(b.vendor["volume"]) == 496400
    assert b.stored["versions"] == 1 and b.basis is None  # the neighbors agree: same basis


def test_CBT_apply_appends_ONE_version_stamped_now_and_a_reapply_appends_zero(db):
    sid = _seed(db, "CBT")
    before = _px_count(db)

    result = repair_bars(db, [("CBT", J09)], apply=True, source=_Source(), now=NOW)

    assert result.appended == 1 and _px_count(db) == before + 1  # COUNT the table
    old, new = _versions(db, sid, J09)  # never an UPDATE: the early pull stays, a version is added
    assert float(old["close"]) == pytest.approx(85.910004, abs=1e-6)
    assert float(new["close"]) == pytest.approx(85.519997, abs=1e-6)
    assert new["recorded_at"] == NOW and new["valid_from"] == J09

    again = repair_bars(db, [("CBT", J09)], apply=True, source=_Source(), now=NOW.replace(hour=20))
    assert again.appended == 0 and _px_count(db) == before + 1


# --- SCE-PG: the vendor has no history for it — reported, never fabricated -----------------------------


def test_SCE_PG_the_vendor_serves_one_bar_so_its_early_pulls_are_reported_and_left(db):
    sid = _seed(db, "SCE-PG")
    before = _px_count(db)

    result = repair_bars(db, [("SCE-PG", A27)], apply=True, source=_Source(), now=NOW)

    (b,) = result.bars
    assert b.action == NO_VENDOR_BAR and b.stored["versions"] == 2  # 14:09 and 16:42 ET, both early
    assert result.appended == 0 and _px_count(db) == before
    assert len(_versions(db, sid, A27)) == 2
    assert result.limitations == {} and result.limited(b) is None  # unmarked: reads as before


_LIMITED_WHY = "Yahoo serves one bar (the current day) for SCE-PG under every range"


def test_SCE_PG_MARKED_limited_history_its_no_vendor_bar_is_a_KNOWN_LIMITATION_not_news(db):
    """0056: with SCE-PG marked "the vendor serves no history" (the operator's Admin mark), the same real
    pull is still ``no_vendor_bar`` with nothing appended — the OUTCOME is unchanged — but the result and
    the provenance carry the mark, so the report can say it is expected."""
    sid = _seed(db, "SCE-PG")
    mark, _ = price_feed_repo.mark_limited_history(
        db, sid, reason=_LIMITED_WHY, basis="operator:limited-history"
    )
    db.commit()
    before = _px_count(db)

    result = repair_bars(db, [("SCE-PG", A27)], apply=True, source=_Source(), now=NOW)

    (b,) = result.bars
    assert b.action == NO_VENDOR_BAR and result.appended == 0 and _px_count(db) == before
    note = f"marked {mark.created_at.date().isoformat()}: {_LIMITED_WHY}"
    assert result.limitations == {sid: note} and result.limited(b) == note
    (skipped,) = RB.build_payload(result)["skipped"]
    assert skipped == {
        "ticker": "SCE-PG",
        "d": "2026-08-27",
        "reason": NO_VENDOR_BAR,
        "feed_limitation": note,
    }


# --- resolution, symbol, scope -----------------------------------------------------------------------


def test_the_vendor_symbol_is_the_nightlys_price_symbol_else_the_ticker():
    """``ingest_bars_for_security``'s rule: ``price_symbol or ticker`` (FDCT is priced as FDCTD)."""

    class _Conn:
        def __init__(self):
            self.rows = {
                "FDCT": [{"id": uuid.uuid4(), "ticker": "FDCT", "price_symbol": "FDCTD"}],
                "CBT": [{"id": uuid.uuid4(), "ticker": "CBT", "price_symbol": None}],
            }

        def cursor(self):
            conn = self

            class _Cur:
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

                def execute(self, sql, params):
                    self._rows = conn.rows.get(params[1], [])

                def fetchall(self):
                    return self._rows

            return _Cur()

    targets, missing = resolve_targets(
        _Conn(), [("FDCT", J09), ("CBT", J09), ("CBT", A27), ("GONE", J09)]
    )
    assert {(t.ticker, t.symbol, t.dates) for t in targets} == {
        ("FDCT", "FDCTD", (J09,)),
        ("CBT", "CBT", (J09, A27)),
    }
    assert missing == ["GONE"]


def test_the_pull_uses_the_nightly_range_live_and_force_refreshed(db, monkeypatch):
    _seed(db, "CBT")
    src = _Source()
    made: list[dict] = []

    def _yahoo(**kw):
        made.append(kw)
        return src

    monkeypatch.setattr(RB, "YahooPriceSource", _yahoo)
    repair_bars(db, [("CBT", J09)], now=NOW)
    assert made[0]["range_"] == NIGHTLY_RANGE and src.calls == [("CBT", True, True)]


def test_a_ticker_no_master_row_carries_is_reported_missing(db):
    result = repair_bars(db, [("NOPE", J09)], apply=True, source=_Source(), now=NOW)
    assert result.missing == ["NOPE"] and result.bars == [] and result.appended == 0


def test_the_scope_guard_refuses_a_weekend_and_today_and_pulls_nothing(db):
    _seed(db, "CBT")
    before = _px_count(db)
    src = _Source()
    with pytest.raises(ScopeRefused, match="weekend"):
        repair_bars(db, [("CBT", date(2026, 7, 11))], apply=True, source=src, now=NOW)
    with pytest.raises(ScopeRefused, match="today or later"):
        repair_bars(db, [("CBT", J09), ("CBT", NOW.date())], apply=True, source=src, now=NOW)
    with pytest.raises(ScopeRefused, match="no bars given"):
        repair_bars(db, [], apply=True, source=src, now=NOW)
    assert src.calls == [] and _px_count(db) == before


def test_explicit_pairs_parse_and_never_cross():
    assert _parse_bars("cbt:2026-07-09, SCE-PG:2026-08-27") == [("CBT", J09), ("SCE-PG", A27)]
    for bad in ("CBT-2026-07-09", ":2026-07-09", "CBT:07/09/2026"):
        with pytest.raises(ValueError):
            _parse_bars(bad)


# --- the basis check (contract, direct inputs) --------------------------------------------------------


def test_neighbors_are_the_vendor_sessions_within_a_week_minus_the_repaired_dates():
    vendor = [{"d": date(2026, 7, d)} for d in (1, 2, 6, 7, 8, 9, 10, 13, 17)]
    assert neighbor_dates(J09, vendor, exclude=[J09, date(2026, 7, 8)]) == [
        date(2026, 7, 2),
        date(2026, 7, 6),
        date(2026, 7, 7),
        date(2026, 7, 10),
        date(2026, 7, 13),
    ]


def test_a_neighbor_on_another_basis_is_named_and_a_settled_revision_is_not():
    n = date(2026, 7, 8)
    stored = {n: {"close": 82.90}}
    assert basis_disagreement([n], vendor=[{"d": n, "close": 82.95}], stored=stored) is None
    assert (
        basis_disagreement([n], vendor=[{"d": n, "close": 41.45}], stored=stored)  # a 2:1 re-base
        == "2026-07-08 stored 82.900000 vs vendor 41.450000"
    )
    assert basis_disagreement([n], vendor=[], stored=stored) is None  # nothing comparable


def test_a_vendor_tape_on_ANOTHER_BASIS_is_never_appended(db):
    """Contract: were CBT's vendor tape re-based 2:1 (every bar halved — what a split does to a split-
    adjusted chart), the 07-09 bar differs but so does every neighbor, so it is BASIS_DIFFERS and nothing
    is written — appending one re-based bar would plant a mixed-basis island."""
    _seed(db, "CBT")
    before = _px_count(db)
    halved = [
        {
            **r,
            **{k: r[k] / 2 for k in ("open", "high", "low", "close") if r[k] is not None},
            "volume": r["volume"] * 2,
        }
        for r in _vendor("CBT")
    ]

    result = repair_bars(db, [("CBT", J09)], apply=True, source=_Source({"CBT": halved}), now=NOW)

    (b,) = result.bars
    assert b.action == BASIS_DIFFERS and b.basis.startswith("2026-07-02 stored 86.349998")
    assert result.appended == 0 and _px_count(db) == before


# --- the CLI -------------------------------------------------------------------------------------------


@pytest.fixture
def cli(monkeypatch, tmp_path):
    src = _Source()
    monkeypatch.setattr(RB, "YahooPriceSource", lambda **kw: src)
    log_dir = tmp_path / "repairs"
    monkeypatch.setattr(RB, "_DEFAULT_LOG_DIR", log_dir)
    seen: dict = {}
    real = RB.repair_bars

    def _spy(conn, pairs, **kw):
        seen["read_only"] = conn.read_only
        seen["pairs"] = list(pairs)
        return real(conn, pairs, **kw)

    monkeypatch.setattr(RB, "repair_bars", _spy)
    return log_dir, seen


def test_the_cli_bars_dry_run_is_read_only_and_writes_nothing(db, cli, capsys):
    log_dir, seen = cli
    _seed(db, "CBT")
    _seed(db, "SCE-PG")
    before = _px_count(db)

    RB.main(["--bars", "CBT:2026-07-09,SCE-PG:2026-08-27"])

    assert seen["read_only"] is True and _px_count(db) == before and not log_dir.exists()
    out = capsys.readouterr().out
    assert "DRY-RUN" in out and f"({NIGHTLY_RANGE}," in out
    assert "SUMMARY: would append 1 · matches 0 · no vendor bar 1 · no stored bar 0" in out
    assert "85.910004" in out and "85.519997" in out
    assert "KNOWN FEED LIMITATION" not in out  # nothing marked: the no_vendor_bar line is unchanged


def test_the_cli_says_a_MARKED_names_no_vendor_bar_is_a_known_feed_limitation(db, cli, capsys):
    """The operator's report: SCE-PG's line names the limitation and its reason, the summary counts it, and
    CBT (unmarked) beside it reads exactly as before. Still a dry run: nothing written."""
    _, seen = cli
    _seed(db, "CBT")
    sid = _seed(db, "SCE-PG")
    price_feed_repo.mark_limited_history(
        db, sid, reason=_LIMITED_WHY, basis="operator:limited-history"
    )
    db.commit()
    before = _px_count(db)

    RB.main(["--bars", "CBT:2026-07-09,SCE-PG:2026-08-27"])

    assert seen["read_only"] is True and _px_count(db) == before
    out = capsys.readouterr().out
    assert "SCE-PG 2026-08-27  the vendor has NO bar for this date — a KNOWN FEED LIMITATION" in out
    assert _LIMITED_WHY in out and "can never be repaired from it" in out
    assert "no vendor bar 1 (1 a known feed limitation) · no stored bar 0" in out
    assert "CBT 2026-07-09  APPEND (" in out


def test_the_cli_ticker_form_is_the_same_pairs(db, cli, capsys):
    _, seen = cli
    _seed(db, "CBT")
    RB.main(["--ticker", "cbt", "--dates", "2026-07-09"])
    assert seen["pairs"] == [("CBT", J09)]


def test_the_cli_bars_apply_appends_and_writes_the_price_bars_provenance(db, cli, capsys):
    log_dir, seen = cli
    sid = _seed(db, "CBT")
    before = _px_count(db)

    RB.main(["--bars", "CBT:2026-07-09", "--apply"])

    assert seen["read_only"] is False and _px_count(db) == before + 1
    (path,) = list(log_dir.glob("*-price-bars.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["kind"] == "repair_price_bars" and payload["applied"] is True
    assert payload["pairs"] == [{"ticker": "CBT", "d": "2026-07-09"}]
    (row,) = payload["appended"]
    assert row["security_id"] == str(sid) and row["old"]["close"] == pytest.approx(
        85.910004, abs=1e-6
    )
    assert "SUMMARY: appended 1" in capsys.readouterr().out


@pytest.mark.parametrize(
    "argv",
    [
        ["--bars", "CBT:2026-07-09", "--dates", "2026-07-09"],
        ["--bars", "CBT-2026-07-09"],
        ["--ticker", "CBT"],
    ],
)
def test_the_cli_rejects_mixed_or_malformed_requests(cli, capsys, argv):
    with pytest.raises(SystemExit) as exc:
        RB.main(argv)
    assert exc.value.code == 2
