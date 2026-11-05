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
            )
        )
    out.sort(key=lambda lc: (lc.public_date, lc.accession, lc.key), reverse=True)
    return out
