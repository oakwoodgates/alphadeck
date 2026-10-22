"""Shared helpers for the S4 split-detection tests — every input is REAL (``tests/fixtures/splits``, see its
README): the filings, the 2026-10-22 Polygon rows, and every stored version of the bars around five splits.

- ``polygon_row`` — one real row of the 2026-10-22 pull.
- ``sec`` — a real filing as the extractor reads it (``SecEvidence``).
- ``first_served`` — the tape at a session as the vendor FIRST served each bar (the detector's own reading is
  ``pipeline.detect_splits.tape_at``; the DB tests load ``tape_rows`` and call it).
- ``tape_rows`` — the stored versions of one name's bars, for loading into ``fact_price_eod``.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from domain.splits import PolygonEvidence, SecEvidence, TapeEvidence
from ingest.edgar.split_filing import extract_split
from ingest.polygon_splits import parse_splits_page
from tests.split_fixtures import FX, documents

ROWS = FX / "polygon" / "rows_2026-10-22.json"
TAPE = FX / "tape_2026-10-22.json"
_ET = ZoneInfo("America/New_York")

# accession -> (ticker, form, filed, the 8-K items the nightly stored for it on prod)
FILINGS = {
    "0001493152-26-039213": ("GAME", "8-K", "2026-08-19", ["3.03", "5.03", "8.01", "9.01"]),
    "0001829126-26-009387": ("NXL", "8-K", "2026-08-27", ["3.03", "5.03", "7.01", "9.01"]),
    "0001062993-26-005382": ("MDCX", "8-K", "2026-10-16", ["3.03", "5.03", "9.01"]),
    "0001493152-26-040184": ("FDCT", "8-K", "2026-08-26", ["3.02", "3.03", "5.03", "9.01"]),
    "0001214659-26-011966": ("VRME", "8-K", "2026-09-25", ["3.03", "5.03", "9.01"]),
    # a 6-K (no item numbers): WSHP's interim results, restating a PAST split
    "0001493152-26-039876": ("WSHP", "6-K", "2026-08-24", None),
}
# ticker -> CIK (the master's)
CIKS = {
    "GAME": "0001714562",
    "NXL": "0001527352",
    "MDCX": "0001997296",
    "FDCT": "0001722731",
    "VRME": "0001104038",
}


def polygon_row(ticker: str) -> PolygonEvidence:
    rows, _, _ = parse_splits_page(ROWS.read_text(encoding="utf-8"))
    return next(r for r in rows if r.ticker == ticker)


def sec(accession: str) -> SecEvidence:
    _, form, filed, _ = FILINGS[accession]
    x = extract_split(documents(f"{accession}.txt"))
    return SecEvidence(
        accession=accession,
        form=form,
        filed=date.fromisoformat(filed),
        url="https://www.sec.gov/",
        ratio_from=x.ratio_from,
        ratio_to=x.ratio_to,
        effective_date=x.effective_date,
        date_kind=x.date_kind,
        passage=x.passage,
        document=x.document,
        abstain_reason=x.abstain_reason,
        date_note=x.date_note,
        about_a_split=x.about_a_split,
    )


def tape_rows(ticker: str) -> list[dict]:
    """Every stored version of the name's bars, oldest first per bar (as read on prod)."""
    return [
        {
            "d": date.fromisoformat(r["d"]),
            "close": Decimal(r["close"]),
            "volume": Decimal(r["volume"]) if r["volume"] is not None else None,
            "recorded_at": datetime.fromisoformat(r["recorded_at"]),
        }
        for r in json.loads(TAPE.read_text(encoding="utf-8"))[ticker]
    ]


def first_served(ticker: str, session: date) -> TapeEvidence:
    first: dict[date, dict] = {}
    for r in sorted(tape_rows(ticker), key=lambda r: (r["d"], r["recorded_at"])):
        first.setdefault(r["d"], r)
    bar = first[session]
    prior = first[max(d for d in first if d < session)]
    return TapeEvidence(
        session=session,
        prior=prior["d"],
        prior_close=float(prior["close"]),
        close=float(bar["close"]),
        prior_volume=float(prior["volume"]),
        volume=float(bar["volume"]),
        prior_served_before=prior["recorded_at"].astimezone(_ET).date() < session,
    )
