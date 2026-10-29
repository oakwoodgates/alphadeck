"""The cover date's bound (2026-10-29): a cover share count dated after its own filing is refused.

ASLE's stored shares fact read 52,990,947 as of 2034-03-05, auto-applied under the 2026-06-30 10-Q's URL. The
count is AerSale's 10-K/A (0001558370-24-004010, filed 2024-03-27): its page says "as of March 5, 2024", but the
inline-XBRL context behind the count is instant 2034-03-05, a typo in the filer's own XBRL. companyfacts carries
it verbatim and the reader took the newest ``end``, so it beat every later cover.

The fixtures are REAL SEC content (``tests/fixtures/edgar/cover_dates/``), copied read-only from the dev EDGAR
cache on 2026-10-29 (the 10-K/A excerpt fetched that day, once, through the polite client):

- ``ASLE_companyfacts.json`` — data.sec.gov companyfacts for CIK 1754170, cut to ``cik`` / ``entityName`` and the
  ``dei:EntityCommonStockSharesOutstanding`` node, verbatim (32 rows, the 2034 one included);
- ``ASLE_submissions.json`` — data.sec.gov submissions, cut to the 10-Q / 10-K / 10-K/A rows of ``recent`` (every
  array filtered by the same index);
- ``asle-20260630x10q_cover.txt`` — ``clean_filing_text`` of the 2026-06-30 10-Q (0001104659-26-092710), cut
  after its first "PART I" (the cover); the shares reader returns the same candidate on the cut as on the whole
  document (checked when the fixture was cut);
- ``asle-20231231x10ka_cover_excerpt.htm`` — two verbatim excerpts of the 10-K/A: the XBRL context and the cover
  sentence carrying the count.

Nothing is edited. The two contract tests at the end state the rule with direct inputs and say so.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from domain.extraction import Tier
from ingest.edgar import extract as ex
from ingest.edgar.annual_shares import _latest_dei_cover, extract_annual_shares

_FX = Path(__file__).resolve().parents[1] / "fixtures" / "edgar" / "cover_dates"
_ANNUAL = Path(__file__).resolve().parents[1] / "fixtures" / "sec_extractor" / "annual"
_REF = "https://www.sec.gov/Archives/edgar/data/1754170/000110465926092710/asle-20260630x10q.htm"
_PERIOD = date(2026, 6, 30)  # the 10-Q's period of report


def _cf() -> dict:
    return json.loads((_FX / "ASLE_companyfacts.json").read_text(encoding="utf-8"))


def _cover_rows() -> list[dict]:
    return _cf()["facts"]["dei"]["EntityCommonStockSharesOutstanding"]["units"]["shares"]


def _tenq() -> str:
    return (_FX / "asle-20260630x10q_cover.txt").read_text(encoding="utf-8")


def test_the_2034_date_is_the_filers_own_xbrl_context():
    """The root cause, on the filing itself: the page says March 5, 2024; the context behind the count says
    2034-03-05. companyfacts reproduces the context, with the 10-K/A's real filing date."""
    excerpt = (_FX / "asle-20231231x10ka_cover_excerpt.htm").read_text(encoding="utf-8")
    ctx = re.search(
        r'<xbrli:context id="(As_Of_3_5_2034[^"]*)">.*?<xbrli:instant>([^<]+)<', excerpt
    )
    assert ctx is not None and ctx.group(2) == "2034-03-05"
    tag = re.search(
        r'contextRef="([^"]+)"[^>]*name="dei:EntityCommonStockSharesOutstanding"', excerpt
    )
    assert tag is not None and tag.group(1) == ctx.group(1)  # the count is tagged to that context
    assert "outstanding as of March 5, 2024 was" in excerpt and ">52,990,947<" in excerpt
    [row] = [r for r in _cover_rows() if r["end"] == "2034-03-05"]
    assert (row["val"], row["accn"], row["form"], row["filed"]) == (
        52_990_947,
        "0001558370-24-004010",
        "10-K/A",
        "2024-03-27",
    )


def test_the_old_reader_takes_the_2034_row():
    """The bug, replayed: the candidate over the UNSCREENED rows is the auto-applied 2034 count."""
    f = ex._shares_candidate(_cover_rows(), _tenq(), _REF, _PERIOD)
    assert (f.tier, f.value, f.event_date) == (Tier.AUTO, 52_990_947, date(2034, 3, 5))


def test_the_rule_bounds_the_cover_by_its_filing_date():
    assert ex.cover_after_filing(date(2034, 3, 5), date(2024, 3, 27))
    assert ex.cover_after_filing(date(2026, 8, 8), date(2026, 8, 7))
    assert not ex.cover_after_filing(
        date(2026, 8, 7), date(2026, 8, 7)
    )  # dated its filing day: kept
    assert not ex.cover_after_filing(date(2026, 8, 3), date(2026, 8, 7))


def test_the_screen_refuses_exactly_the_2034_row():
    rows = _cover_rows()
    kept, refused = ex.screen_cover_rows(rows)
    assert [(r.asof, r.filed, r.value, r.accession, r.form) for r in refused] == [
        (date(2034, 3, 5), date(2024, 3, 27), 52_990_947.0, "0001558370-24-004010", "10-K/A")
    ]
    assert len(kept) == len(rows) - 1
    assert all(r["end"] <= r["filed"] for r in kept)


def test_the_reader_reads_the_10q_cover_and_names_the_refusal():
    """The fixed reader on the real documents: the 10-Q's own cover count, AUTO, with the refusal visible as a
    flag and in the note (which the auto-confirm stores with the fact)."""
    text = _tenq()
    assert "outstanding as of August 3, 2026 was 47,726,763" in re.sub(r"\s+", " ", text).replace(
        "​", ""
    )
    f = ex._shares(_cf()["facts"], text, _REF, _PERIOD)
    assert (f.tier, f.value, f.event_date) == (Tier.AUTO, 47_726_763, date(2026, 8, 3))
    assert f.source_ref == _REF and f.source == "10-q-cover"
    assert f.flags == [ex.COVER_AFTER_FILING]
    assert f.note.startswith("Cover-page shares outstanding as of 2026-08-03 (single class).")
    assert "52,990,947 as of 2034-03-05 (10-K/A 0001558370-24-004010, filed 2024-03-27)" in f.note


def test_extract_facts_carries_the_fix():
    """The public entry the Workbench extract + auto-confirm read."""
    facts = ex.extract_facts(
        _cf(),
        _tenq(),
        _tenq(),
        tenq_ref=_REF,
        tenk_ref=_REF,
        tenq_date=_PERIOD,
        tenk_date=_PERIOD,
    )
    [shares] = [f for f in facts if f.fact_type == "shares_outstanding"]
    assert (shares.value, shares.event_date) == (47_726_763, date(2026, 8, 3))


def test_the_annual_reader_refuses_the_row_too():
    """The annual-cover reader's later-as-of-wins rule would let a mistyped future date win forever."""
    assert _latest_dei_cover(_cf()) == (47_726_763.0, date(2026, 8, 3))


def test_annual_contract_a_refused_row_is_flagged_and_named():
    """CONTRACT (direct inputs, stated): NVMI's real 20-F cover text read beside ASLE's real companyfacts — two
    issuers' documents, so the VALUE is no claim about either company; what is pinned is that the refused 2034
    row cannot win the later-as-of rule and is named on the candidate."""
    [f] = extract_annual_shares(
        _cf(),
        (_ANNUAL / "NVMI-20f.txt").read_text(encoding="utf-8"),
        annual_ref="https://sec.gov/NVMI-20f.txt",
        annual_form="20-F",
        report_date=date(2025, 12, 31),
        today=date(2026, 10, 29),
    )
    assert f.event_date == date(2026, 8, 3) and f.value == 47_726_763
    assert ex.COVER_AFTER_FILING in f.flags
    assert "52,990,947 as of 2034-03-05" in f.note


def test_contract_a_mistyped_current_cover_falls_to_stale_cover():
    """CONTRACT (direct inputs, stated): if the CURRENT filing's own cover row were the mistyped one, the reader
    does not fall back to AUTO on the previous quarter — it offers that older count as ``stale-cover`` (the
    operator ratifies from the located cover), with the refusal named. ASLE's real Q1 row + a Q2 row whose date
    is put in 2036 here."""
    q1 = next(r for r in _cover_rows() if r["accn"] == "0001104659-26-057983")
    q2_typo = {
        **next(r for r in _cover_rows() if r["accn"] == "0001104659-26-092710"),
        "end": "2036-08-03",
    }
    facts = {"dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [q1, q2_typo]}}}}
    f = ex._shares(facts, _tenq(), _REF, _PERIOD)
    assert (f.tier, f.value, f.event_date) == (Tier.FLAG, 47_252_829, date(2026, 5, 4))
    assert f.flags == ["stale-cover", ex.COVER_AFTER_FILING]
    assert "47,726,763 as of 2036-08-03" in f.note


def test_contract_every_row_refused_says_so():
    """CONTRACT (direct inputs, stated): only the refused row -> no value, and the note does not claim
    companyfacts has no cover concept."""
    facts = {
        "dei": {
            "EntityCommonStockSharesOutstanding": {
                "units": {"shares": [r for r in _cover_rows() if r["end"] == "2034-03-05"]}
            }
        }
    }
    f = ex._shares(facts, "no cover text", _REF, _PERIOD)
    assert f.value is None and f.flags == ["no-companyfacts", ex.COVER_AFTER_FILING]
    assert "has no cover-shares concept" not in f.note
    assert "dated after their own filing" in f.note
