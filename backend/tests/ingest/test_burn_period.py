"""The quarterly BURN's period, tag and citation, and the form-switcher routing (V2 extractor correctness, PR 2).

An audit of 460 auto-applied quarterly cash-and-burn values (2026-11-15) found the burn wrong three ways:

- **a stale burn**: 8 companies moved their operating cash flow to
  ``NetCashProvidedByUsedInOperatingActivitiesContinuingOperations``, a tag the reader did not know, so the burn came
  from the old tag's last period (EHC's from 2023; QXL's and T's from 2025);
- **a "quarter" spanning 6 to 9 months**: with the prior same-start year to date missing, the reader subtracted an
  earlier column and still called the result a quarter (URI's 9 months minus its first quarter: 183 days, 2x);
- **values not from the cited filing**: five companies that moved from 10-Q to 20-F/40-F (EUDA, CURR, ...) were
  sent down the quarterly path, which read the 20-F's numbers under the old 10-Q's link.

The rules as built (``ingest/edgar/extract.py``): operating cash flow is read under its most current label (plain
or continuing operations, the newest end wins, a tie keeps the plain total); a burn is AUTO only when it spans
``quarterly_span_min_days`` to ``quarterly_span_max_days`` (80-100), ends on the cited filing's period of report,
and the cash is dated that period; otherwise it is offered with a named flag (``burn-span``, ``burn-off-period``,
``cash-after-period``; an older cash date keeps ``stale-cash``). A company whose newest periodic filing by public
time is a 20-F or 40-F takes the annual path.

The fixtures in ``tests/fixtures/edgar/burn_period/`` are RECONSTRUCTED, not copied: the fork's EDGAR cache was not
readable for this work. Each holds only the nodes the reader uses, with the values the audit files record
(the 2026-11-15 quarterly-cash audit's findings and verdicts, and the build plan's findings 3 and 4; none of these
notes is kept in the repo). Every row is marked ``"accn": "reconstructed"``. Recorded: QXL's cash 2,435,000,
continuing-ops six months -1,666,000 and quarter -1,062,000, and the stored stale burn 1,715,000 (Jul-Sep 2025);
EHC's continuing-ops nine months 957.7m and the stored burn's Apr-Jun 2023 span; T's continuing-ops nine months
28,915m and the stored burn's Q4 2025 span; URI's nine months 4,934m and first quarter 1,514m; EUDA's cited 10-Q
(cash 837,292, Q1-2023 operating cash flow -469,908), stored cash 345,145 and stored burn 1,211,112 over
2025-06-30 to 2025-12-31; CURR's cited 10-Q (cash 62,300,298, Q1 -1,487,912), stored cash 75,244,405 and full-year
2025 operating cash flow +7,863,996. Synthetic: every accession, filed date, acceptance time and fiscal label; QXL's
plain-tag year-to-date values (only their difference is recorded) and first quarter (the recorded quarter
subtracted from the recorded six months); EHC's and T's cash, six months and plain-tag rows; URI's cash; EUDA's two
2025 year-to-date values (only their difference is recorded); the tag EUDA's cash sits under. Fed through the old
reader, each reproduces the value the audit found stored, AUTO.

Display-only: the burn feeds the runway meter and the platform value, never a call.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from domain.config import ExtractorConfig
from domain.extraction import ExtractionResult, Tier
from ingest.edgar import extract as ex

_FX = Path(__file__).resolve().parents[1] / "fixtures" / "edgar" / "burn_period"
_CC = Path(__file__).resolve().parents[1] / "fixtures" / "edgar" / "cash_composition"
_PLAIN = "NetCashProvidedByUsedInOperatingActivities"
_CONT = "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"


def _facts(ticker: str, folder: Path = _FX) -> dict:
    return json.loads((folder / f"{ticker}.json").read_text(encoding="utf-8"))["facts"]


def _cb(facts: dict, period: date, cfg: ExtractorConfig | None = None, ref: str = "10-Q"):
    return ex._cash_burn(facts, "", ref, period, cfg or ExtractorConfig())


# ---------------------------------------------------------------------------------------------------------
# (a) the continuing-operations tag: the stale burns
# ---------------------------------------------------------------------------------------------------------


def test_qxl_reads_the_continuing_operations_burn_not_the_stale_2025_quarter():
    """QXL moved its operating cash flow to the continuing-operations tag. The old reader stored 1,715,000, the
    plain tag's Jul-Sep 2025 quarter, under a 2026-06-30 filing."""
    cb = _cb(_facts("QXL"), date(2026, 6, 30))
    assert cb.quarterly_burn_usd == 1_062_000
    assert cb.tier is Tier.AUTO and cb.flags == []
    assert cb.event_date == date(2026, 6, 30)
    assert f"burn over 2026-03-31 → 2026-06-30 (91 days, tagged {_CONT})" in cb.note
    assert "derived (YTD − prior YTD)" in cb.note


def test_ehc_reads_the_2026_burn_not_the_2023_quarter():
    """EHC's stored burn spanned Apr-Jun 2023 under a 2026-09-30 filing. The 2026 nine months (957.7m) minus the
    six months is the third quarter, cash generated (a negative burn)."""
    cb = _cb(_facts("EHC"), date(2026, 9, 30))
    assert cb.quarterly_burn_usd == -(957_700_000 - 600_000_000)
    assert cb.tier is Tier.AUTO and cb.flags == []
    assert f"burn over 2026-06-30 → 2026-09-30 (92 days, tagged {_CONT})" in cb.note


def test_t_reads_the_2026_burn_not_the_2025_fourth_quarter():
    cb = _cb(_facts("T"), date(2026, 9, 30))
    assert cb.quarterly_burn_usd == -(28_915_000_000 - 18_900_000_000)
    assert cb.tier is Tier.AUTO and cb.flags == []
    assert _CONT in cb.note and "2025-12-31" not in cb.note


def test_without_the_continuing_tag_the_stale_burn_is_flagged_never_auto():
    """Defense in depth: had the reader not known the new tag, the stale quarter still could not go out AUTO, because
    it does not end on the cited filing's period of report."""
    facts = _facts("QXL")
    del facts["us-gaap"][_CONT]
    cb = _cb(facts, date(2026, 6, 30))
    assert cb.quarterly_burn_usd == 1_715_000  # offered, as found
    assert cb.tier is Tier.FLAG and cb.flags == ["burn-off-period"]
    assert (
        "the burn ends 2025-09-30, not on the cited filing's period of report 2026-06-30" in cb.note
    )


def _dur(start: str, end: str, val: float) -> dict:
    return {"start": start, "end": end, "val": val, "filed": end}


def _inst(end: str, val: float) -> dict:
    return {"end": end, "val": val, "filed": end}


def _g(cash: list[dict] | None = None, **ocf: list[dict]) -> dict:
    g: dict = {}
    if cash is not None:
        g["CashAndCashEquivalentsAtCarryingValue"] = {"units": {"USD": cash}}
    for concept, rows in ocf.items():
        g[concept] = {"units": {"USD": rows}}
    return {"us-gaap": g}


_P = date(2026, 6, 30)
_CASH = [_inst("2026-06-30", 50e6)]


def test_the_plain_tag_wins_when_it_is_the_newer_one():
    f = _g(
        _CASH,
        **{
            _PLAIN: [_dur("2026-04-01", "2026-06-30", -7e6)],
            _CONT: [_dur("2025-10-01", "2025-12-31", -3e6)],
        },
    )
    cb = _cb(f, _P)
    assert cb.quarterly_burn_usd == 7e6 and cb.tier is Tier.AUTO
    assert f"tagged {_PLAIN})" in cb.note and _CONT not in cb.note


def test_the_continuing_tag_wins_when_it_is_the_newer_one():
    f = _g(
        _CASH,
        **{
            _PLAIN: [_dur("2025-10-01", "2025-12-31", -7e6)],
            _CONT: [_dur("2026-04-01", "2026-06-30", -3e6)],
        },
    )
    cb = _cb(f, _P)
    assert cb.quarterly_burn_usd == 3e6 and cb.tier is Tier.AUTO
    assert f"tagged {_CONT})" in cb.note


@pytest.mark.parametrize("continuing_first", [True, False])
def test_both_tags_on_the_same_period_read_the_total(continuing_first: bool):
    """Both reported for the same period: the plain tag is the statement's total (continuing plus discontinued), so
    it wins the tie (plan Q3; D8's recommended reading). The order the tags appear in companyfacts never matters.
    """
    plain = [_dur("2026-04-01", "2026-06-30", -10e6)]
    cont = [_dur("2026-04-01", "2026-06-30", -8e6)]
    ocf = {_CONT: cont, _PLAIN: plain} if continuing_first else {_PLAIN: plain, _CONT: cont}
    cb = _cb(_g(_CASH, **ocf), _P)
    assert cb.quarterly_burn_usd == 10e6 and cb.tier is Tier.AUTO
    assert f"tagged {_PLAIN})" in cb.note


def test_a_derivation_never_subtracts_across_the_two_tags():
    """The six months sit under the continuing tag, the first quarter only under the plain one: subtracting one
    tag's column from the other's mixes a total with a part, so nothing is derived (the raw six months, ``ytd-raw``).
    """
    f = _g(
        _CASH,
        **{
            _PLAIN: [_dur("2026-01-01", "2026-03-31", -4e6)],
            _CONT: [_dur("2026-01-01", "2026-06-30", -9e6)],
        },
    )
    cb = _cb(f, _P)
    assert cb.quarterly_burn_usd == 9e6
    assert cb.tier is Tier.FLAG and "ytd-raw" in cb.flags


# ---------------------------------------------------------------------------------------------------------
# (b) the span: a "quarter" must cover about one quarter
# ---------------------------------------------------------------------------------------------------------


def test_uri_a_six_month_difference_is_offered_flagged_never_a_quarter():
    """URI's six months are missing, so the old reader took nine months minus the first quarter (183 days) and
    stored it AUTO as one quarter: 3,420m, 2x too big."""
    cb = _cb(_facts("URI"), date(2026, 9, 30))
    assert cb.quarterly_burn_usd == -(4_934_000_000 - 1_514_000_000)  # offered as found
    assert cb.tier is Tier.FLAG and cb.flags == ["burn-span"]
    assert "burn over 2026-03-31 → 2026-09-30 (183 days" in cb.note
    assert "the burn spans 183 days, outside the 80-100-day quarter" in cb.note


def test_a_short_native_column_is_flagged():
    """AIAI's 54-day column (the audit's short span): a native column under 80 days is not a quarter."""
    f = _g(_CASH, **{_PLAIN: [_dur("2026-05-07", "2026-06-30", -2e6)]})
    cb = _cb(f, _P)
    assert cb.quarterly_burn_usd == 2e6
    assert cb.tier is Tier.FLAG and cb.flags == ["burn-span"]
    assert "(54 days" in cb.note


@pytest.mark.parametrize(
    "prior_end, days, auto",
    [
        ("2026-07-13", 79, False),
        ("2026-07-12", 80, True),
        ("2026-06-22", 100, True),
        ("2026-06-21", 101, False),
    ],
)
def test_the_derived_span_window_is_inclusive(prior_end: str, days: int, auto: bool):
    f = _g(
        [_inst("2026-09-30", 50e6)],
        **{
            _PLAIN: [
                _dur("2026-01-01", prior_end, -6e6),
                _dur("2026-01-01", "2026-09-30", -9e6),
            ]
        },
    )
    cb = _cb(f, date(2026, 9, 30))
    assert cb.quarterly_burn_usd == 3e6  # the derivation itself never changes
    assert f"({days} days" in cb.note
    assert (cb.tier is Tier.AUTO) is auto
    assert ("burn-span" in cb.flags) is (not auto)


def test_the_span_window_is_a_config_dial():
    f = _g(_CASH, **{_PLAIN: [_dur("2026-04-01", "2026-06-30", -2e6)]})  # 90 days
    assert _cb(f, _P).tier is Tier.AUTO
    tight = _cb(f, _P, ExtractorConfig(quarterly_span_min_days=95))
    assert tight.flags == ["burn-span"] and "outside the 95-100-day quarter" in tight.note


def test_a_first_quarter_with_no_prior_is_unchanged():
    """A Q1 10-Q: a native first-quarter column (89 days), nothing to subtract, AUTO as before."""
    f = _g([_inst("2026-03-31", 50e6)], **{_PLAIN: [_dur("2026-01-01", "2026-03-31", -2e6)]})
    cb = _cb(f, date(2026, 3, 31))
    assert cb.tier is Tier.AUTO and cb.flags == [] and cb.quarterly_burn_usd == 2e6
    assert "derived" not in cb.note


def test_the_year_to_date_subtraction_still_works():
    """No regression (the audit found the derivation right in 50 of 50): TSLA's nine months minus six months."""
    cb = _cb(_facts("TSLA", _CC), date(2026, 9, 30))
    assert cb.quarterly_burn_usd == -(14_897_000_000 - 8_634_000_000)
    assert cb.tier is Tier.AUTO and cb.flags == []
    assert f"burn over 2026-06-30 → 2026-09-30 (92 days, tagged {_PLAIN})" in cb.note


# ---------------------------------------------------------------------------------------------------------
# the citation: the burn and the cash belong to the cited filing's period
# ---------------------------------------------------------------------------------------------------------


def test_cash_dated_after_the_period_is_flagged():
    """RMIX-shaped (synthetic): the cash balance is dated 31 days after the cited filing's period of report."""
    f = _g([_inst("2026-07-31", 50e6)], **{_PLAIN: [_dur("2026-04-01", "2026-06-30", -2e6)]})
    cb = _cb(f, _P)
    assert cb.tier is Tier.FLAG and cb.flags == ["cash-after-period"]
    assert (
        "cash is dated 2026-07-31, after the cited filing's period of report 2026-06-30" in cb.note
    )


def test_a_burn_ending_after_the_period_is_flagged():
    f = _g(_CASH, **{_PLAIN: [_dur("2026-07-01", "2026-09-30", -2e6)]})
    cb = _cb(f, _P)
    assert cb.tier is Tier.FLAG and cb.flags == ["burn-off-period"]


def test_the_note_names_the_cited_filing():
    cb = _cb(_facts("QXL"), date(2026, 6, 30), ref="https://www.sec.gov/Archives/x/qxl-10q.htm")
    assert (
        "cited filing: the 10-Q for the period ending 2026-06-30 "
        "(https://www.sec.gov/Archives/x/qxl-10q.htm)" in cb.note
    )


# ---------------------------------------------------------------------------------------------------------
# (c) the form switchers: routed to the annual path
# ---------------------------------------------------------------------------------------------------------


class _Client:
    """Serves one company's reconstructed submissions and companyfacts; a document's text is a stand-in."""

    def __init__(self, ticker: str) -> None:
        self.ticker = ticker

    def get_json(self, url: str, cache: str) -> dict:
        if cache.startswith("submissions/"):
            return json.loads((_FX / f"{self.ticker}.submissions.json").read_text(encoding="utf-8"))
        return json.loads((_FX / f"{self.ticker}.json").read_text(encoding="utf-8"))

    def get_text(self, url: str, cache: str) -> str:
        return "cash and cash equivalents ... operating activities ..."


@pytest.mark.parametrize("ticker", ["EUDA", "CURR"])
def test_a_company_now_filing_20f_takes_the_annual_path(monkeypatch, ticker: str):
    """EUDA and CURR once filed 10-Qs and now file 20-Fs. The router sent them down the quarterly path, which cited
    the old 10-Q (2023-03-31 and 2025-03-31) under the 20-F's numbers, AUTO."""
    import ingest.edgar.annual_runway as annual

    sentinel = ExtractionResult(facts=[], runway_empty_reason="statements-not-located")
    seen: list[int] = []

    def fake_annual(client, cik, cfg):
        seen.append(cik)
        return sentinel

    monkeypatch.setattr(annual, "annual_facts_for_security", fake_annual)
    out = ex.extract_with_annual_fallback(_Client(ticker), 4242)
    assert out is sentinel, [
        (f.fact_type, f.tier, f.quarterly_burn_usd, f.source_ref) for f in out.facts
    ]
    assert seen == [4242]


def test_euda_on_the_quarterly_path_would_be_flagged_three_ways():
    """Defense in depth, had the router still sent EUDA down the quarterly path: the 20-F's numbers under the
    2023-03-31 10-Q are cash dated after the period, a burn ending off the period, over 184 days."""
    cb = _cb(_facts("EUDA"), date(2023, 3, 31))
    assert cb.tier is Tier.FLAG
    assert cb.flags == ["burn-span", "burn-off-period", "cash-after-period"]
    assert cb.quarterly_burn_usd == 1_211_112 and cb.cash_usd == 345_145  # offered as found


def test_curr_on_the_quarterly_path_would_be_flagged():
    cb = _cb(_facts("CURR"), date(2025, 3, 31))
    assert cb.tier is Tier.FLAG
    assert cb.flags == ["burn-span", "burn-off-period", "cash-after-period"]
    assert "(275 days" in cb.note


def _subs(rows: list[tuple[str, str, str]]) -> dict:
    """rows newest first: (form, filed, acceptance)."""
    return {
        "filings": {
            "recent": {
                "form": [r[0] for r in rows],
                "accessionNumber": [f"a-{i}" for i in range(len(rows))],
                "primaryDocument": [f"d{i}.htm" for i in range(len(rows))],
                "filingDate": [r[1] for r in rows],
                "reportDate": ["" for _ in rows],
                "acceptanceDateTime": [r[2] for r in rows],
            }
        }
    }


def test_an_older_20f_and_a_newer_10q_stay_on_the_quarterly_path():
    subs = _subs(
        [
            ("10-Q", "2026-05-12", "2026-05-12T20:05:00.000Z"),
            ("20-F", "2022-04-29", "2022-04-29T20:05:00.000Z"),
        ]
    )
    assert ex._takes_annual_path(subs) is False


def test_a_newer_40f_takes_the_annual_path():
    subs = _subs(
        [
            ("40-F", "2026-03-20", "2026-03-20T21:00:00.000Z"),
            ("10-K", "2025-03-14", "2025-03-14T21:00:00.000Z"),
        ]
    )
    assert ex._takes_annual_path(subs) is True


def test_the_routing_reads_public_time_not_list_order_or_period():
    """A 20-F accepted after the 10-Q wins even when listed second (no lookahead: by acceptance, never by the period
    of report)."""
    subs = _subs(
        [
            ("10-Q", "2026-05-12", "2026-05-12T20:05:00.000Z"),
            ("20-F", "2026-05-12", "2026-05-12T21:30:00.000Z"),
        ]
    )
    assert ex._takes_annual_path(subs) is True


def test_a_tie_on_public_time_keeps_the_quarterly_path():
    subs = _subs(
        [
            ("20-F", "2026-05-12", "2026-05-12T20:05:00.000Z"),
            ("10-Q", "2026-05-12", "2026-05-12T20:05:00.000Z"),
        ]
    )
    assert ex._takes_annual_path(subs) is False


def test_no_periodic_form_at_all_takes_the_annual_path_as_before():
    assert ex._takes_annual_path(_subs([("6-K", "2026-05-12", "")])) is True


def test_a_domestic_filer_end_to_end_stays_quarterly(monkeypatch):
    """A plain 10-Q filer (QXL's fixture under a periodic submissions index) rides the quarterly path."""
    import ingest.edgar.annual_runway as annual

    monkeypatch.setattr(
        annual,
        "annual_facts_for_security",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("annual path must not run")),
    )

    class _Q(_Client):
        def get_json(self, url: str, cache: str) -> dict:
            if cache.startswith("submissions/"):
                s = _subs([("10-Q", "2026-08-14", "2026-08-14T20:05:00.000Z")])
                s["filings"]["recent"]["reportDate"] = ["2026-06-30"]
                return s
            return json.loads((_FX / "QXL.json").read_text(encoding="utf-8"))

    out = ex.extract_with_annual_fallback(_Q("QXL"), 77, values_only=True)
    cb = next(f for f in out.facts if f.fact_type == "cash_burn")
    assert cb.tier is Tier.AUTO and cb.quarterly_burn_usd == 1_062_000


def test_the_new_rules_route_through_config():
    src = Path(ex.__file__).read_text(encoding="utf-8")
    assert "cfg.quarterly_span_min_days" in src and "cfg.quarterly_span_max_days" in src


def test_extractor_modules_stay_out_of_the_call_path():
    """Nothing here feeds a call: no module under ``calls/`` or ``signals/`` imports the extractor."""
    backend = Path(__file__).resolve().parents[2]
    for pkg in ("calls", "signals"):
        for path in (backend / pkg).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert "ingest.edgar.extract" not in text, path
            assert "ingest.edgar import extract" not in text, path
