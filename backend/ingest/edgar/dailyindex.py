"""EDGAR daily-index enumeration — the SPAC Radar's event spine (radar/spac.py).

One ``master.{YYYYMMDD}.idx`` per posted day lists EVERY filing accepted that day
(``CIK|Company Name|Form Type|Date Filed|Filename``). One fetch enumerates the whole market —
deliberately NOT the EFTS full-text path (``ingest/edgar/fulltext.py``), so the radar can never
drift the recall-gated discovery universe (INVARIANTS #9).

Cache: ``daily-index/`` is a MUTABLE prefix → the 12h default TTL applies (today's index grows
through the day; a re-fetch of a finished day is cheap). Weekends/holidays have no index — the
fetch 404s and the caller treats that date as a no-index day, not an error.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from domain.settings import get_settings
from ingest.edgar.client import EdgarClient


@dataclass(frozen=True)
class IndexFiling:
    """One row of a daily master index — a single filing (accession)."""

    cik: str  # normalized: no leading zeros
    company: str
    form: str
    filed: date
    filename: str  # e.g. "edgar/data/1849056/0001213900-26-041234.txt"

    @property
    def accession(self) -> str:
        """The dashed accession number, from the filename ("0001213900-26-041234")."""
        return self.filename.rsplit("/", 1)[-1].removesuffix(".txt")


def daily_index_url(d: date) -> str:
    quarter = (d.month - 1) // 3 + 1
    return f"{get_settings().sec_daily_index_base}/{d.year}/QTR{quarter}/master.{d:%Y%m%d}.idx"


def parse_master_idx(text: str) -> list[IndexFiling]:
    """Parse a master.idx into rows. No header-state tracking: every line is tried against the
    5-field pipe shape with a digit CIK and an ISO date — header/banner/dash lines fail those
    guards naturally, so a format quirk (missing dashes, extra preamble) can't desync a parser."""
    rows: list[IndexFiling] = []
    for line in text.splitlines():
        parts = line.split("|")
        if len(parts) != 5:
            continue
        cik, company, form, filed, filename = (p.strip() for p in parts)
        if not cik.isdigit() or not filename:
            continue
        try:
            filed_d = date.fromisoformat(filed)
        except ValueError:
            continue
        rows.append(
            IndexFiling(
                cik=str(int(cik)), company=company, form=form, filed=filed_d, filename=filename
            )
        )
    return rows


def fetch_daily_index(client: EdgarClient, d: date) -> list[IndexFiling]:
    """Fetch + parse one day's master index. Raises on a fetch failure (EDGAR answers 403 — not
    404 — for a date with no index posted, i.e. weekends/holidays; the caller decides whether
    that is a skip or an error)."""
    text = client.get_text(daily_index_url(d), f"daily-index/master.{d:%Y%m%d}.idx")
    return parse_master_idx(text)
