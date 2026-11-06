"""A fund's launch stage, DERIVED ON READ from the stored facts — never stored (the SPAC radar's ``deal_state``
pattern), so the stage rules can change with no backfill. Pure: rows in, launches out; no I/O, no clock.

Four stages, each a public date or None:
- **filed**     — the first public date the series appears as NEW (a 485APOS / N-1A, or an S-1's name-only row);
- **effective** — the first 485BPOS public date that lists the series ID (on or after ``filed``);
- **ticker**    — the first night SEC's fund ticker file showed a symbol for the series;
- **listed**    — the first night the Nasdaq Trader directory showed one of its symbols as an ETF.

A ticker or listing date is the night WE first saw the symbol (neither file carries a date), so a fund that was
already trading when the radar started reads that start night, not its true date.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date

from domain.market_time import market_date_of
from radar.fund_header import KIND_NEW
from radar.fund_repo import SOURCE_NASDAQ, SOURCE_SEC, SeriesView, SymbolRow

STAGES = ("filed", "effective", "ticker", "listed")


@dataclass(frozen=True)
class FundStage:
    filed: date | None = None
    effective: date | None = None
    ticker: date | None = None
    listed: date | None = None

    @property
    def current(self) -> str:
        """The furthest stage reached."""
        for name in reversed(STAGES):
            if getattr(self, name) is not None:
                return name
        return "filed"


@dataclass(frozen=True)
class FundLaunch:
    """One fund (series) and where it stands. ``accession`` / ``form`` / ``public_date`` / ``private_to_public``
    are those of the filing that FIRST announced it as new."""

    key: str  # the series ID, or 'cik:<filer cik>' for a name-only (S-1) fund
    series_id: str | None
    name: str
    trust: str
    accession: str
    form: str
    public_date: date
    private_to_public: bool
    stage: FundStage
    symbols: tuple[str, ...] = field(default_factory=tuple)
    source_ref: str = ""
    # the market date the fund's series was first stored (bookkeeping: see ``activity_dates``); None = unknown
    first_stored: date | None = None


def visible_as_of(
    views: Iterable[SeriesView], symbols: Iterable[SymbolRow], asof: date
) -> tuple[list[SeriesView], list[SymbolRow]]:
    """The facts public by ``asof`` and nothing later (#1, the public clock): a filing counts from its public
    date, a symbol from the night we first saw it. Derive stages from THIS, so a 485BPOS or a ticker that came
    after ``asof`` can never show on a history view."""
    return (
        [v for v in views if v.public_date <= asof],
        [s for s in symbols if s.night <= asof],
    )


def derive_launches(
    views: Iterable[SeriesView], symbols: Iterable[SymbolRow] = ()
) -> list[FundLaunch]:
    """Every fund some filing announced as new, with its derived stage — newest ``filed`` first."""
    by_key: dict[str, list[SeriesView]] = {}
    for v in views:
        by_key.setdefault(v.series_id or v.series_key, []).append(v)
    sym_by_sid: dict[str, list[SymbolRow]] = {}
    for s in symbols:
        sym_by_sid.setdefault(s.series_id, []).append(s)

    out: list[FundLaunch] = []
    for key, rows in by_key.items():
        news = sorted(
            (r for r in rows if r.kind == KIND_NEW), key=lambda r: (r.public_date, r.accession)
        )
        if not news:
            continue  # only a fund some filing announced as new is a launch
        first = news[0]
        filed = first.public_date
        effective = min(
            (r.public_date for r in rows if r.form == "485BPOS" and r.public_date >= filed),
            default=None,
        )
        sid = first.series_id
        mine = sym_by_sid.get(sid, []) if sid else []
        ticker = min((s.night for s in mine if s.source == SOURCE_SEC), default=None)
        listed = min((s.night for s in mine if s.source == SOURCE_NASDAQ), default=None)
        out.append(
            FundLaunch(
                key=key,
                series_id=sid,
                name=news[-1].series_name,
                trust=first.filer_name,
                accession=first.accession,
                form=first.form,
                public_date=filed,
                private_to_public=first.private_to_public,
                stage=FundStage(filed, effective, ticker, listed),
                symbols=tuple(sorted({s.symbol for s in mine})),
                source_ref=first.source_ref,
                first_stored=(
                    market_date_of(min(stored))
                    if (stored := [r.first_stored for r in rows if r.first_stored])
                    else None
                ),
            )
        )
    out.sort(key=lambda lc: (lc.public_date, lc.accession, lc.key), reverse=True)
    return out


def symbol_starts(symbols: Iterable[SymbolRow]) -> dict[str, date]:
    """The first night each source shows ANY symbol (over the rows given): "tickers and listings tracked since"."""
    out: dict[str, date] = {}
    for s in symbols:
        if s.source not in out or s.night < out[s.source]:
            out[s.source] = s.night
    return out


def activity_dates(stage: FundStage, first_stored: date | None) -> list[date]:
    """The dates a fund DID something: filed, effective, and a ticker / listing that is an OBSERVED arrival.

    A ticker or listing date is the night WE first saw the symbol. It counts as activity only when it is STRICTLY
    LATER than the night the fund's series was first stored (``first_stored``, the market date of its earliest
    ``recorded_at``): we never saw a symbol arrive for a fund we had only just learned about (a backfill stores
    old funds that were already trading). ``recorded_at`` is used here ONLY to judge that; it is not a history
    gate. History still filters by public date and by symbol night <= as-of (invariant #1). ``None`` (unknown)
    leaves the date counted. Used to window the per-card view (a fund gets its ticker months after its filing).
    """
    out = [d for d in (stage.filed, stage.effective) if d is not None]
    for d in (stage.ticker, stage.listed):
        if d is not None and (first_stored is None or d > first_stored):
            out.append(d)
    return out
