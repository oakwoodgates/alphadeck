"""REAL companyfacts revenue version histories for the D3a first-knowability tests — measured, not fitted.

Each list is EVERY stored ``fact_fundamentals`` revenue version of one name, exactly as prod held them on
2026-10-16 (read-only ``SELECT ... ORDER BY period_end, recorded_at, id``): ``(period_end, value, filed,
accession, fiscal_period, fiscal_year, basis)``. ``store_versions`` stores them exactly as prod holds them:
the ingest's own row values with the ``valid_from = filed``, ``recorded_at = filed 00:00 UTC`` stamp every
pre-0057 prod row carries (MEASURED: all 120,810 prod rows on 2026-10-16, 120,876 on 2026-10-21, backfill
and nightly alike) — rows the honest-stamp change (migration 0057) leaves untouched. ``store_versions_live``
stores the same history through the REAL writer as it stamps today (one honest database instant per batch).

What each one is for:

- ``GEV`` (GE Vernova) — the D3 example. Q1-2025 revenue of $8,032M was first filed 2025-04-23 and
  re-reported UNCHANGED as the prior-year comparative of the 2026-04-22 10-Q, which also first reported
  Q1-2026 ($9,339M). The 2026-10-01 Nuclear Buildout card cited "Q1 ending 2025-03-31" dated 2026-04-22.
- ``CEG`` (Constellation) — tonight's phantom arm: its Q1-2025 inflection (first filed 2025-05-06) re-dated
  to the 2026-05-11 comparative kept a year-old inflection live at 2026-10-15. Trimmed to periods from
  2023-03-31 (the detector's lineage for every quarter it can fire on since 2025 starts there).
- ``LRHC`` (La Rosa) — a real SHARED filing: the 2026-06-04 FY-2025 10-K restated every quarter from
  2024-Q1 to 2025-Q3 and first reported 2025-Q4, so two inflections (2025-Q1 restated, 2025-Q4 new) share
  one knowability date — the tie-break (d) case.
- ``KULR`` — a genuine restatement of the CURRENT year's quarter: Q1-2026 filed 2026-05-14 at $4,846,430,
  restated 2026-08-13 to $3,946,430 — new knowledge at its own date.
- ``FLR_2017Q1`` (Fluor, one period) — a real flip-flop: the value alternates between an exact and a
  thousands-rounded figure across five filings, so the CURRENT run starts after the last change, not at the
  first time that number ever appeared.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timezone
from pathlib import Path
from uuid import UUID

import psycopg

from db.bitemporal import append_fact
from ingest.fundamentals import QuarterPoint, fundamentals_row, store_quarters

Version = tuple[date, int, date, str, str, int, str]

GEV: list[Version] = [
    (
        date(2023, 3, 31),
        6822000000,
        date(2024, 4, 30),
        "0001996810-24-000008",
        "Q1",
        2024,
        "native",
    ),
    (
        date(2023, 6, 30),
        8119000000,
        date(2024, 7, 24),
        "0001996810-24-000067",
        "Q2",
        2024,
        "native",
    ),
    (
        date(2023, 9, 30),
        8253000000,
        date(2024, 10, 23),
        "0001996810-24-000083",
        "Q3",
        2024,
        "native",
    ),
    (
        date(2023, 12, 31),
        10045000000,
        date(2025, 2, 6),
        "0001996810-25-000011",
        "Q4",
        2024,
        "derived",
    ),
    (
        date(2023, 12, 31),
        10045000000,
        date(2026, 1, 29),
        "0001996810-26-000015",
        "Q4",
        2025,
        "derived",
    ),
    (
        date(2024, 3, 31),
        7260000000,
        date(2024, 4, 30),
        "0001996810-24-000008",
        "Q1",
        2024,
        "native",
    ),
    (
        date(2024, 3, 31),
        7260000000,
        date(2025, 4, 23),
        "0001996810-25-000073",
        "Q1",
        2025,
        "native",
    ),
    (
        date(2024, 6, 30),
        8204000000,
        date(2024, 7, 24),
        "0001996810-24-000067",
        "Q2",
        2024,
        "native",
    ),
    (
        date(2024, 6, 30),
        8204000000,
        date(2025, 7, 23),
        "0001996810-25-000133",
        "Q2",
        2025,
        "native",
    ),
    (
        date(2024, 9, 30),
        8913000000,
        date(2024, 10, 23),
        "0001996810-24-000083",
        "Q3",
        2024,
        "native",
    ),
    (
        date(2024, 9, 30),
        8913000000,
        date(2025, 10, 22),
        "0001996810-25-000160",
        "Q3",
        2025,
        "native",
    ),
    (
        date(2024, 12, 31),
        10559000000,
        date(2025, 2, 6),
        "0001996810-25-000011",
        "Q4",
        2024,
        "derived",
    ),
    (
        date(2024, 12, 31),
        10559000000,
        date(2026, 1, 29),
        "0001996810-26-000015",
        "Q4",
        2025,
        "derived",
    ),
    (
        date(2025, 3, 31),
        8032000000,
        date(2025, 4, 23),
        "0001996810-25-000073",
        "Q1",
        2025,
        "native",
    ),
    (
        date(2025, 3, 31),
        8032000000,
        date(2026, 4, 22),
        "0001996810-26-000064",
        "Q1",
        2026,
        "native",
    ),
    (
        date(2025, 6, 30),
        9111000000,
        date(2025, 7, 23),
        "0001996810-25-000133",
        "Q2",
        2025,
        "native",
    ),
    (
        date(2025, 6, 30),
        9111000000,
        date(2026, 7, 22),
        "0001996810-26-000148",
        "Q2",
        2026,
        "native",
    ),
    (
        date(2025, 9, 30),
        9969000000,
        date(2025, 10, 22),
        "0001996810-25-000160",
        "Q3",
        2025,
        "native",
    ),
    (
        date(2025, 12, 31),
        10956000000,
        date(2026, 1, 29),
        "0001996810-26-000015",
        "Q4",
        2025,
        "derived",
    ),
    (
        date(2026, 3, 31),
        9339000000,
        date(2026, 4, 22),
        "0001996810-26-000064",
        "Q1",
        2026,
        "native",
    ),
    (
        date(2026, 6, 30),
        11104000000,
        date(2026, 7, 22),
        "0001996810-26-000148",
        "Q2",
        2026,
        "native",
    ),
]

CEG: list[Version] = [
    (date(2023, 3, 31), 7565000000, date(2023, 5, 4), "0001868275-23-000032", "Q1", 2023, "native"),
    (date(2023, 3, 31), 7565000000, date(2024, 5, 9), "0001868275-24-000026", "Q1", 2024, "native"),
    (date(2023, 6, 30), 5446000000, date(2023, 8, 3), "0001868275-23-000064", "Q2", 2023, "native"),
    (date(2023, 6, 30), 5446000000, date(2024, 8, 6), "0001868275-24-000054", "Q2", 2024, "native"),
    (
        date(2023, 9, 30),
        6111000000,
        date(2023, 11, 6),
        "0001868275-23-000080",
        "Q3",
        2023,
        "native",
    ),
    (
        date(2023, 9, 30),
        6111000000,
        date(2024, 11, 4),
        "0001868275-24-000069",
        "Q3",
        2024,
        "native",
    ),
    (
        date(2023, 12, 31),
        5796000000,
        date(2024, 2, 27),
        "0001868275-24-000014",
        "Q4",
        2023,
        "derived",
    ),
    (
        date(2023, 12, 31),
        5796000000,
        date(2025, 2, 18),
        "0001868275-25-000023",
        "Q4",
        2024,
        "derived",
    ),
    (
        date(2023, 12, 31),
        5796000000,
        date(2026, 2, 24),
        "0001868275-26-000032",
        "Q4",
        2025,
        "derived",
    ),
    (date(2024, 3, 31), 6161000000, date(2024, 5, 9), "0001868275-24-000026", "Q1", 2024, "native"),
    (date(2024, 3, 31), 6161000000, date(2025, 5, 6), "0001868275-25-000044", "Q1", 2025, "native"),
    (date(2024, 6, 30), 5475000000, date(2024, 8, 6), "0001868275-24-000054", "Q2", 2024, "native"),
    (date(2024, 6, 30), 5475000000, date(2025, 8, 7), "0001868275-25-000073", "Q2", 2025, "native"),
    (
        date(2024, 9, 30),
        6550000000,
        date(2024, 11, 4),
        "0001868275-24-000069",
        "Q3",
        2024,
        "native",
    ),
    (
        date(2024, 9, 30),
        6550000000,
        date(2025, 11, 7),
        "0001868275-25-000092",
        "Q3",
        2025,
        "native",
    ),
    (
        date(2024, 12, 31),
        5382000000,
        date(2025, 2, 18),
        "0001868275-25-000023",
        "Q4",
        2024,
        "derived",
    ),
    (
        date(2024, 12, 31),
        5382000000,
        date(2026, 2, 24),
        "0001868275-26-000032",
        "Q4",
        2025,
        "derived",
    ),
    (date(2025, 3, 31), 6788000000, date(2025, 5, 6), "0001868275-25-000044", "Q1", 2025, "native"),
    (
        date(2025, 3, 31),
        6788000000,
        date(2026, 5, 11),
        "0001868275-26-000067",
        "Q1",
        2026,
        "native",
    ),
    (date(2025, 6, 30), 6101000000, date(2025, 8, 7), "0001868275-25-000073", "Q2", 2025, "native"),
    (date(2025, 6, 30), 6101000000, date(2026, 8, 6), "0001868275-26-000104", "Q2", 2026, "native"),
    (
        date(2025, 9, 30),
        6570000000,
        date(2025, 11, 7),
        "0001868275-25-000092",
        "Q3",
        2025,
        "native",
    ),
    (
        date(2025, 12, 31),
        6074000000,
        date(2026, 2, 24),
        "0001868275-26-000032",
        "Q4",
        2025,
        "derived",
    ),
    (
        date(2026, 3, 31),
        11122000000,
        date(2026, 5, 11),
        "0001868275-26-000067",
        "Q1",
        2026,
        "native",
    ),
    (date(2026, 6, 30), 7504000000, date(2026, 8, 6), "0001868275-26-000104", "Q2", 2026, "native"),
]

LRHC: list[Version] = [
    (date(2022, 9, 30), 6131892, date(2023, 11, 20), "0001575872-23-001807", "Q3", 2023, "native"),
    (date(2022, 12, 31), 5997248, date(2024, 4, 16), "0001213900-24-033444", "Q4", 2023, "derived"),
    (date(2023, 3, 31), 6041636, date(2024, 5, 15), "0001213900-24-043603", "Q1", 2024, "native"),
    (date(2023, 6, 30), 7486720, date(2024, 8, 15), "0001213900-24-069618", "Q2", 2024, "native"),
    (date(2023, 9, 30), 6792250, date(2023, 11, 20), "0001575872-23-001807", "Q3", 2023, "native"),
    (date(2023, 9, 30), 6792250, date(2024, 11, 19), "0001213900-24-100202", "Q3", 2024, "native"),
    (
        date(2023, 12, 31),
        11438798,
        date(2024, 4, 16),
        "0001213900-24-033444",
        "Q4",
        2023,
        "derived",
    ),
    (
        date(2023, 12, 31),
        11438798,
        date(2025, 4, 15),
        "0001213900-25-032211",
        "Q4",
        2024,
        "derived",
    ),
    (date(2024, 3, 31), 13088899, date(2024, 5, 15), "0001213900-24-043603", "Q1", 2024, "native"),
    (date(2024, 3, 31), 13088899, date(2025, 5, 29), "0001213900-25-048370", "Q1", 2025, "native"),
    (date(2024, 3, 31), 10636534, date(2026, 6, 4), "0001213900-26-065276", "FY", 2025, "native"),
    (date(2024, 6, 30), 19051420, date(2024, 8, 15), "0001213900-24-069618", "Q2", 2024, "native"),
    (date(2024, 6, 30), 19051420, date(2025, 8, 18), "0001213900-25-078012", "Q2", 2025, "native"),
    (date(2024, 6, 30), 16365990, date(2026, 6, 4), "0001213900-26-065276", "FY", 2025, "native"),
    (date(2024, 9, 30), 19593036, date(2024, 11, 19), "0001213900-24-100202", "Q3", 2024, "native"),
    (date(2024, 9, 30), 19593036, date(2025, 11, 19), "0001213900-25-112656", "Q3", 2025, "native"),
    (date(2024, 9, 30), 16832463, date(2026, 6, 4), "0001213900-26-065276", "FY", 2025, "native"),
    (
        date(2024, 12, 31),
        17715431,
        date(2025, 4, 15),
        "0001213900-25-032211",
        "Q4",
        2024,
        "derived",
    ),
    (date(2024, 12, 31), 14847152, date(2026, 6, 4), "0001213900-26-065276", "Q4", 2025, "derived"),
    (date(2025, 3, 31), 17514394, date(2025, 5, 29), "0001213900-25-048370", "Q1", 2025, "native"),
    (date(2025, 3, 31), 14635774, date(2026, 6, 4), "0001213900-26-065276", "FY", 2025, "native"),
    (date(2025, 6, 30), 23214218, date(2025, 8, 18), "0001213900-25-078012", "Q2", 2025, "native"),
    (date(2025, 6, 30), 20232097, date(2026, 6, 4), "0001213900-26-065276", "FY", 2025, "native"),
    (date(2025, 9, 30), 20216143, date(2025, 11, 19), "0001213900-25-112656", "Q3", 2025, "native"),
    (date(2025, 9, 30), 17208787, date(2026, 6, 4), "0001213900-26-065276", "FY", 2025, "native"),
    (date(2025, 12, 31), 16431148, date(2026, 6, 4), "0001213900-26-065276", "Q4", 2025, "derived"),
]

KULR: list[Version] = [
    (date(2024, 9, 30), 3185778, date(2025, 11, 18), "0001104659-25-113662", "Q3", 2025, "native"),
    (date(2025, 3, 31), 2448606, date(2026, 5, 14), "0001104659-26-061180", "Q1", 2026, "native"),
    (date(2025, 6, 30), 3652471, date(2026, 8, 13), "0001104659-26-095924", "Q2", 2026, "native"),
    (date(2025, 9, 30), 6884840, date(2025, 11, 18), "0001104659-25-113662", "Q3", 2025, "native"),
    (date(2026, 3, 31), 4846430, date(2026, 5, 14), "0001104659-26-061180", "Q1", 2026, "native"),
    (date(2026, 3, 31), 3946430, date(2026, 8, 13), "0001104659-26-095924", "Q2", 2026, "native"),
    (date(2026, 6, 30), 2080177, date(2026, 8, 13), "0001104659-26-095924", "Q2", 2026, "native"),
]

FLR_2017Q1: list[Version] = [
    (date(2017, 3, 31), 4835905000, date(2017, 5, 4), "0001104659-17-029914", "Q1", 2017, "native"),
    (
        date(2017, 3, 31),
        4835900000,
        date(2018, 2, 20),
        "0001047469-18-000900",
        "FY",
        2017,
        "native",
    ),
    (date(2017, 3, 31), 4835905000, date(2018, 5, 3), "0001104659-18-030273", "Q1", 2018, "native"),
    (date(2017, 3, 31), 4835900000, date(2018, 8, 3), "0001104659-18-049261", "FY", 2017, "native"),
    (
        date(2017, 3, 31),
        4835900000,
        date(2019, 2, 21),
        "0001628280-19-001658",
        "FY",
        2018,
        "native",
    ),
]


def quarter_points(versions: list[Version]) -> list[QuarterPoint]:
    """The fixture as the ingest's own ``QuarterPoint`` values (one per stored version)."""
    return [
        QuarterPoint(
            metric_key="revenue",
            period_end=pe,
            value=float(v),
            filed=filed,
            accession=accn,
            basis=basis,
            fiscal_period=fp,
            fiscal_year=fy,
        )
        for pe, v, filed, accn, fp, fy, basis in versions
    ]


def legacy_stamp(filed: date) -> datetime:
    """The stamp every row written before migration 0057 carries — ``recorded_at = filed 00:00 UTC`` (MEASURED:
    all 120,876 prod rows on 2026-10-21; those rows are kept exactly as written)."""
    return datetime.combine(filed, time.min, tzinfo=timezone.utc)


def store_versions(conn: psycopg.Connection, security_id: UUID, versions: list[Version]) -> None:
    """Store every version EXACTLY as prod holds it today: the ingest's own row values
    (``ingest.fundamentals.fundamentals_row``) with the pre-0057 backdated stamp (``legacy_stamp``) — the
    shape of every existing prod row, which the honest-stamp change leaves untouched. The writer itself no
    longer produces this stamp (``store_versions_live`` stores through it); then commit."""
    for q in quarter_points(versions):
        append_fact(
            conn,
            "fact_fundamentals",
            {**fundamentals_row(security_id, q), "recorded_at": legacy_stamp(q.filed)},
        )
    conn.commit()


def store_versions_live(
    conn: psycopg.Connection, security_id: UUID, versions: list[Version]
) -> datetime:
    """Store every version through the REAL ingest writer — today's honest stamp: one database instant for
    the whole batch, as a name's onboarding pull writes its full history — then commit. Returns that
    stamp, read back from the table (the newest one on the security: anything already there is older).
    """
    result = store_quarters(conn, security_id, quarter_points(versions))
    assert result.appended == len(versions), "every real version is a distinct (period_end, filed)"
    conn.commit()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT recorded_at, count(*) AS n FROM fact_fundamentals WHERE security_id = %s "
            "GROUP BY recorded_at ORDER BY recorded_at DESC LIMIT 1",
            (security_id,),
        )
        row = cur.fetchone()
    assert row["n"] == len(versions), "the batch carries ONE stamp"
    return row["recorded_at"]


def first_filed(versions: list[Version]) -> dict[date, tuple[date, str]]:
    """Per period, the (filed, accession) of its EARLIEST version — the first knowability of a period whose
    value never changed (true for every GEV / CEG period)."""
    out: dict[date, tuple[date, str]] = {}
    for pe, _v, filed, accn, *_ in versions:
        if pe not in out or filed < out[pe][0]:
            out[pe] = (filed, accn)
    return out


# --- REAL companyfacts documents, trimmed to their revenue nodes (the label-switch fix, 2026-10-20) --------
#
# ``tests/fixtures/edgar/companyfacts_revenue/<TICKER>.json`` = data.sec.gov companyfacts as the dev EDGAR cache
# held it (fetched by the nightly 2026-09-12, 02:30 UTC), cut to ``cik`` / ``entityName`` and the
# ``REVENUE_CONCEPTS`` nodes present, every row verbatim (the source's own spelling and keys). Nothing is edited.
#
# - ``AMD`` — tagged ``Revenues`` for two 2017 quarters, then ``RevenueFromContract…ExcludingAssessedTax``
#   to 2026-06-27; the old first-present rule kept the 2017 pair (MEASURED on prod: its stored revenue
#   series ended 2017-09-30).
# - ``HON`` — ``Revenues`` 2008-2012, the contract tag 2017-2026 (it also restates 2025 for the Solstice spin).
# - ``NEE`` — ``Revenues`` 2008-2013, ``RevenueFromContract…IncludingAssessedTax`` 2018-2026.
# - ``VICR`` — both labels on the SAME filings in 2024-2025, equal on every shared filing but one: the
#   2025-08-01 10-Q's Q2-2025 is 141,046,000 under ``Revenues`` and 96,046,000 under the contract tag (the
#   9-month YTDs differ by the same 45,000,000). The overlap case, and the same-filing supersession.
# - ``LHX`` — a ``Revenues`` node whose rows extract to NO quarter; the old rule picked it and stored no revenue.
#   Also the data-quality screen's two quarter-context cases: the 2026 10-K files FY2024's $21.325B under a Q4
#   context (beside the FY row), and FY2025's $21.865B ONLY under a Q4 context (no FY row at all).
# - ``LFMD`` — FY2024 restated to $154.8M (continuing operations) on 2026-03-10, after its 9-month last moved on
#   2025-11-17: FY − 9-month = a $5.5M "Q4" (the screen's restated-basis case).
# - ``TTMI`` — the tie-break (``Revenues`` 4 quarters vs the contract tag 70, both to 2026-06-29), and a recast
#   that IS on one basis (FY2019 recast 2021-02-22 after its 9-month was recast) beside one that is not (FY2018).
# - ``KULR`` — the tie-break again (29 vs 52 quarters, both to 2026-06-30).
# - ``XEL`` / ``HAPN`` — the two tags appended to ``REVENUE_CONCEPTS``: a utility's
#   ``RegulatedAndUnregulatedOperatingRevenue`` and a bank's ``RevenuesNetOfInterestExpense``; their listed tags
#   stop in 2019 / 2020. ``NEE`` carries the utility tag too (162 quarters vs the contract tag's 68).
COMPANYFACTS_REVENUE_DIR = (
    Path(__file__).resolve().parent / "fixtures" / "edgar" / "companyfacts_revenue"
)
COMPANYFACTS_CIK = {
    "AMD": "0000002488",
    "HON": "0000773840",
    "NEE": "0000753308",
    "VICR": "0000751978",
    "LHX": "0000202058",
    "LFMD": "0000948320",
    "TTMI": "0001116942",
    "KULR": "0001662684",
    "XEL": "0000072903",
    "HAPN": "0001409970",
}


def companyfacts_revenue(ticker: str) -> dict:
    """One trimmed real companyfacts document (see above)."""
    return json.loads((COMPANYFACTS_REVENUE_DIR / f"{ticker}.json").read_text(encoding="utf-8"))


def companyfacts_cache(cache_dir: Path, *tickers: str) -> Path:
    """Lay the fixtures out as an EDGAR cache (``companyfacts/CIK<10>.json``) under ``cache_dir`` — what a
    cache-first ``EdgarClient(cache_dir=..., allow_live=False)`` reads, so the REAL fetch path runs offline.
    """
    fc = cache_dir / "companyfacts"
    fc.mkdir(parents=True, exist_ok=True)
    for t in tickers:
        name = f"CIK{int(COMPANYFACTS_CIK[t]):010d}.json"
        (fc / name).write_text(
            (COMPANYFACTS_REVENUE_DIR / f"{t}.json").read_text(encoding="utf-8"), encoding="utf-8"
        )
    return cache_dir
