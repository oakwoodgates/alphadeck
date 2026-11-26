from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from ingest.edgar.submissions import (
    DEREGISTRATION_EVENT_DAYS,
    DEREGISTRATION_EVENT_ITEMS,
    DEREGISTRATION_FORMS,
    DelistingFiling,
    Deregistration,
    DeregistrationEvent,
    acceptance_times,
    delisting_filings,
    deregistration,
    edgar_tickers,
    filings_of,
    form4_doc_url,
    form4_filings,
    parse_acceptance,
    parse_identity,
)

_SUBS = json.loads(
    (
        Path(__file__).resolve().parent.parent / "fixtures" / "edgar" / "cached_sample.json"
    ).read_text(encoding="utf-8")
)


def test_form4_filings_lists_form4s():
    filings = form4_filings(_SUBS)
    assert len(filings) == 1
    assert filings[0]["accession"] == "0001234567-26-000123"
    assert filings[0]["primary_doc"] == "doc4.xml"


def _subs_with_dates() -> dict:
    return {
        "filings": {
            "recent": {
                "form": ["10-Q"],
                "accessionNumber": ["0000723125-26-000047"],
                "primaryDocument": ["mu-10q.htm"],
                "filingDate": ["2026-06-25"],
                "reportDate": ["2026-05-28"],
            }
        }
    }


def test_filings_of_carries_the_report_date_distinct_from_filed():
    """The load-bearing distinction (the every-name "dual-class" mis-flag): ``filed`` is the FILING date,
    ``report_date`` the PERIOD OF REPORT — ~a month apart on a 10-Q. Both must ride."""
    f = filings_of(_subs_with_dates(), "10-Q")[0]
    assert f["filed"] == "2026-06-25"
    assert f["report_date"] == "2026-05-28"


def test_filings_of_defends_a_missing_report_date_array():
    subs = _subs_with_dates()
    del subs["filings"]["recent"]["reportDate"]
    assert filings_of(subs, "10-Q")[0]["report_date"] == ""  # defensive "", never a crash


# --- the SEC acceptance datetime (the honest "disclosed" clock — the MRVL two-clock fix) ---


def _subs_form4(rows: list[tuple[str, str]]) -> dict:
    """A filings.recent block of Form 4s with parallel (accession, acceptanceDateTime) arrays."""
    return {
        "filings": {
            "recent": {
                "form": ["4"] * len(rows),
                "accessionNumber": [a for a, _ in rows],
                "acceptanceDateTime": [t for _, t in rows],
                "primaryDocument": ["doc.xml"] * len(rows),
                "filingDate": ["2026-01-01"] * len(rows),
            }
        }
    }


def test_filings_of_carries_the_acceptance_datetime():
    """``filings_of`` now surfaces ``acceptanceDateTime`` (a parallel array we already fetch) as
    ``accepted`` — the raw string the Form 4 leg threads into the fact's honest disclosure clock."""
    f = form4_filings(_subs_form4([("0001628280-25-042718", "2025-09-27T18:30:41.000Z")]))[0]
    assert f["accepted"] == "2025-09-27T18:30:41.000Z"


def test_filings_of_defends_a_missing_acceptance_array():
    subs = _subs_form4([("acc-1", "2026-01-01T12:00:00.000Z")])
    del subs["filings"]["recent"]["acceptanceDateTime"]
    assert filings_of(subs, "4")[0]["accepted"] == ""  # defensive "", never a crash


def test_acceptance_times_maps_accession_to_acceptance():
    """The backfill source: accession -> raw acceptanceDateTime across the whole recent tape. A blank
    acceptance entry simply doesn't appear (the caller leaves that accession NULL, #9)."""
    subs = _subs_form4([("acc-a", "2025-09-27T18:30:41.000Z"), ("acc-b", "")])
    assert acceptance_times(subs) == {"acc-a": "2025-09-27T18:30:41.000Z"}
    assert acceptance_times({}) == {}  # sparse doc -> empty, never raises


def test_parse_acceptance_handles_edgar_formats():
    from datetime import datetime, timezone

    # the EDGAR Z-suffixed format -> tz-aware UTC
    assert parse_acceptance("2025-09-27T18:30:41.000Z") == datetime(
        2025, 9, 27, 18, 30, 41, tzinfo=timezone.utc
    )
    # no offset -> assumed UTC (the read gate compares against a UTC known_at)
    assert parse_acceptance("2026-01-02T09:00:00") == datetime(
        2026, 1, 2, 9, 0, 0, tzinfo=timezone.utc
    )
    # unresolved / malformed -> None (never a guess; the row stays NULL, #9)
    assert parse_acceptance(None) is None
    assert parse_acceptance("") is None
    assert parse_acceptance("not-a-datetime") is None


def test_mrvl_real_accession_two_clock_lags():
    """Real cited example (test-honesty): MRVL Form 4, accession 0001628280-25-042718, transaction
    2025-09-25, RE-INGESTED 2026-08-17 (the demo-rebuild recorded_at). The exact acceptanceDateTime
    was NOT re-fetchable offline (EDGAR 403s a generic User-Agent); this fixture uses the plan's cited
    acceptance illustration (~2 business days after the txn, per the SEC's Form 4 rule + the accession's
    2025 year-stamp). It proves the MECHANISM + the two REAL lags: disclosed ~2d vs ingested 326d.
    """
    from datetime import date

    subs = _subs_form4([("0001628280-25-042718", "2025-09-27T18:30:41.000Z")])
    accepted = parse_acceptance(acceptance_times(subs)["0001628280-25-042718"])
    assert accepted is not None
    txn = date(2025, 9, 25)
    ingested = date(2026, 8, 17)  # the real committed re-ingest stamp (spec's data table)
    assert (accepted.date() - txn).days == 2  # disclosed ~2d later — honest, not 326
    assert (ingested - txn).days == 326  # the ingest lag, real arithmetic — the second clock


def test_latest_filing_threads_the_period_of_report_not_the_filing_date():
    """THE WIRING REGRESSION the golden suite couldn't see (it tests the pure core with a hand-picked
    date): the live wrapper's date must be the PERIOD OF REPORT. With the FILING date, a cover's "as of"
    (always earlier) failed the shares currency gate on every name -> the universal "dual-class" lie.
    MU's real dates pin it: cover 06-17, filed 06-25, period 05-28."""
    from datetime import date

    from ingest.edgar.extract import _latest_filing

    class _FakeClient:
        def get_json(self, url: str, cache_key: str) -> dict:
            return _subs_with_dates()

        def get_text(self, url: str, cache_key: str) -> str:
            return "cover text"

    got = _latest_filing(_FakeClient(), 723125, "10-Q")  # type: ignore[arg-type]
    assert got is not None
    _url, _text, period = got
    assert period == date(2026, 5, 28)  # the PERIOD OF REPORT — not 2026-06-25 (filed)


def test_form4_doc_url_uses_raw_xml_not_xsl_render():
    # submissions gives the xsl-rendered path; we must fetch the raw ownership XML to parse it
    url = form4_doc_url("1773751", "0001773751-26-000086", "xslF345X06/wk-form4_1779828505.xml")
    assert url == (
        "https://www.sec.gov/Archives/edgar/data/1773751/000177375126000086/wk-form4_1779828505.xml"
    )
    assert "xsl" not in url


# --- parse_identity: machine-parsed master identity from submissions (Workbench enrichment, Slice 1) ---


def test_parse_identity_active_reads_sector_and_exchange():
    ident = parse_identity(
        {
            "sicDescription": "Electric Services",
            "exchanges": ["NYSE", "OTC"],
            "tickers": ["OKLO"],
            "formerNames": [],
        }
    )
    assert ident.sector == "Electric Services"
    assert ident.exchange == "NYSE"  # first of exchanges
    assert ident.status == "active"  # a current ticker AND exchange present
    assert ident.former_names == []


def test_parse_identity_reads_filer_category():
    """The SEC filer `category` (a maturity/size tell) is surfaced; absent -> None (never invented). EDGAR joins
    multiple attributes with a literal "<br>" — those tags are stripped to a clean " · "-joined string (no raw
    markup ever reaches the chip)."""
    ident = parse_identity(
        {
            "sicDescription": "Semiconductors",
            "category": "Large accelerated filer",
            "tickers": ["MU"],
        }
    )
    assert ident.category == "Large accelerated filer"
    # the <br>-joined form (the live bug) → tags stripped, joined with " · "
    assert (
        parse_identity({"category": "Non-accelerated filer<br>Smaller reporting company"}).category
        == "Non-accelerated filer · Smaller reporting company"
    )
    # a leading <br> (the SCHMID/GOWell case) → no stray separator
    assert (
        parse_identity({"category": "<br>Emerging growth company"}).category
        == "Emerging growth company"
    )
    assert parse_identity({"sicDescription": "Semiconductors"}).category is None  # absent -> None


def test_parse_identity_no_listing_is_inactive():
    """No current ticker / exchange -> a listing-presence 'inactive' (a HEURISTIC, never a delisting verdict)."""
    ident = parse_identity({"sicDescription": "Blank Checks", "exchanges": [], "tickers": []})
    assert ident.status == "inactive"
    assert ident.exchange is None
    assert ident.sector == "Blank Checks"


def test_parse_identity_extracts_former_names_for_the_bridge():
    """formerNames is parsed (the rebrand history the identity-bridge slice will use) though unused today;
    a blank name is dropped."""
    ident = parse_identity(
        {
            "sicDescription": "Biological Products",
            "exchanges": ["Nasdaq"],
            "tickers": ["ATAI"],
            "formerNames": [
                {
                    "name": "Perception Neuroscience Holdings",
                    "from": "2018-01-01",
                    "to": "2021-05-15",
                },
                {"name": "", "from": "x", "to": "y"},
            ],
        }
    )
    assert ident.former_names == [
        {"name": "Perception Neuroscience Holdings", "from": "2018-01-01", "to": "2021-05-15"}
    ]


def test_parse_identity_tolerates_a_sparse_submissions():
    """A sparse/old submissions (missing keys) -> all-None/empty, status inactive — never raises."""
    ident = parse_identity({})
    assert (ident.sector, ident.exchange, ident.status, ident.former_names) == (
        None,
        None,
        "inactive",
        [],
    )


# --- parse_identity: the ORIGIN ingredients (migration 0028 — raw locators for the derive-on-read chip) ---


def _recent(forms: list[str]) -> dict:
    """A filings.recent block with parallel arrays (the real SEC shape ``filings_of`` walks)."""
    n = len(forms)
    return {
        "filings": {
            "recent": {
                "form": forms,
                "accessionNumber": [f"0000000000-26-{i:06d}" for i in range(n)],
                "primaryDocument": [f"doc{i}.htm" for i in range(n)],
                "filingDate": ["2026-01-01"] * n,
                "reportDate": ["2025-12-31"] * n,
            }
        }
    }


def test_parse_identity_origin_ingredients_nio_shape():
    """The China-ADR class, from live-measured NIO: Cayman incorporation, business country NULL (the SEC
    quirk this slice exists for), city "SHANGHAI" the only populated locator, 20-F/6-K forms."""
    ident = parse_identity(
        {
            "stateOfIncorporationDescription": "Cayman Islands",
            "addresses": {
                "business": {
                    "city": "SHANGHAI",
                    "stateOrCountry": None,
                    "stateOrCountryDescription": None,
                },
                "mailing": {"city": "SHANGHAI"},
            },
            "tickers": ["NIO"],
            "exchanges": ["NYSE"],
            **_recent(["6-K", "20-F", "6-K"]),
        }
    )
    assert ident.incorporation == "Cayman Islands"
    assert ident.business_country is None
    assert ident.business_city == "SHANGHAI"
    assert ident.files_foreign_forms is True


def test_parse_identity_origin_ingredients_apple_shape():
    """A US filer, from live-measured Apple: incorporation "CA" (the description field holds the STATE
    ABBREVIATION for US entities, not "United States"), business country "CA", city "Cupertino", 10-K/10-Q.
    """
    ident = parse_identity(
        {
            "stateOfIncorporationDescription": "CA",
            "addresses": {
                "business": {
                    "city": "Cupertino",
                    "stateOrCountry": "CA",
                    "stateOrCountryDescription": "CA",
                }
            },
            "tickers": ["AAPL"],
            "exchanges": ["Nasdaq"],
            **_recent(["10-K", "10-Q", "10-Q"]),
        }
    )
    assert ident.incorporation == "CA"
    assert ident.business_country == "CA"
    assert ident.business_city == "Cupertino"
    assert ident.files_foreign_forms is False  # no 20-F/40-F in the recent forms


def test_parse_identity_origin_ingredients_sparse_doc_abstains():
    """A sparse/old filer (no addresses, no incorporation, no filings) -> Nones + False, never raises —
    the chip's honest abstain starts here."""
    ident = parse_identity({})
    assert ident.incorporation is None
    assert ident.business_city is None
    assert ident.business_country is None
    assert ident.files_foreign_forms is False

    # addresses present but empty / business block missing — still tolerated
    ident2 = parse_identity(
        {"addresses": {"mailing": {"city": "X"}}, "stateOfIncorporationDescription": "  "}
    )
    assert (ident2.incorporation, ident2.business_city, ident2.business_country) == (
        None,
        None,
        None,
    )


def test_parse_identity_files_foreign_forms_via_40f():
    """The Canadian-MJDS arm: a 40-F alone flips the stored ingredient."""
    assert parse_identity(_recent(["40-F", "6-K"])).files_foreign_forms is True


# --- parse_identity: the FILER-FORM ingredients (migration 0031 — the foreign-filer explainability tell) ---


def _recent_dated(rows: list[tuple[str, str]]) -> dict:
    """A filings.recent block with per-row (form, filingDate) — for the 20-F-vs-40-F tie-break."""
    forms = [f for f, _ in rows]
    dates = [d for _, d in rows]
    n = len(rows)
    return {
        "filings": {
            "recent": {
                "form": forms,
                "accessionNumber": [f"0000000000-26-{i:06d}" for i in range(n)],
                "primaryDocument": [f"doc{i}.htm" for i in range(n)],
                "filingDate": dates,
                "reportDate": dates,
            }
        }
    }


def test_parse_identity_recent_foreign_form_20f_alone():
    """An FPI: a 20-F present, no 10-K/10-Q → recent_foreign_form "20-F", domestic-forms False (the tell fires)."""
    ident = parse_identity(_recent(["6-K", "20-F", "6-K"]))
    assert ident.recent_foreign_form == "20-F"
    assert ident.files_domestic_forms is False


def test_parse_identity_recent_foreign_form_40f_alone():
    """A Canadian-MJDS filer: a 40-F present, no 10-K/10-Q → "40-F", domestic-forms False."""
    ident = parse_identity(_recent(["40-F", "6-K"]))
    assert ident.recent_foreign_form == "40-F"
    assert ident.files_domestic_forms is False


def test_parse_identity_files_domestic_forms_vetoes_via_10k():
    """The domestic veto (the Energy-Fuels/UUUU shape): a legacy 40-F BUT a recent 10-K/10-Q on file —
    recent_foreign_form still carries the form, but files_domestic_forms is True so the derived tell abstains.
    """
    ident = parse_identity(_recent(["10-K", "10-Q", "40-F"]))
    assert ident.recent_foreign_form == "40-F"
    assert ident.files_domestic_forms is True  # the veto ingredient
    assert ident.files_foreign_forms is True  # the 0028 bool still sees the 40-F


def test_parse_identity_recent_foreign_form_both_present_picks_newer_by_filed():
    """Both a 20-F and a 40-F on file (pathological — the regimes are mutually exclusive): the NEWER filing
    date wins, deterministically."""
    assert (
        parse_identity(
            _recent_dated([("20-F", "2026-04-01"), ("40-F", "2025-04-01")])
        ).recent_foreign_form
        == "20-F"
    )
    assert (
        parse_identity(
            _recent_dated([("40-F", "2026-04-01"), ("20-F", "2025-04-01")])
        ).recent_foreign_form
        == "40-F"
    )


def test_parse_identity_filer_forms_sparse_doc_abstains():
    """No filings at all → recent_foreign_form None, files_domestic_forms False (the tell's honest abstain)."""
    ident = parse_identity({})
    assert ident.recent_foreign_form is None
    assert ident.files_domestic_forms is False


# --- delisting_filings + edgar_tickers: the stop-classification FACTS (G5b/T1, tape monitor) ----------


def test_delisting_filings_is_EMPTY_when_no_delisting_form():
    """A live, listed name files none of the delisting forms → (), so a stopped tape (if any) stays a feed
    gap to repair rather than being mis-marked closed. A sparse/old doc → () too, never a crash."""
    assert delisting_filings(_recent(["10-K", "8-K", "4", "DEF 14A"])) == ()
    assert delisting_filings({}) == ()


def test_delisting_filings_reads_VRMEs_real_warrant_25_NSE_with_its_FILING_date_and_accession():
    """The real row behind the VRME bug, MEASURED from prod's cached submissions (2026-10-16): Nasdaq's
    Form 25-NSE for the WARRANTS, accession 0001354457-25-000562, filed 2025-06-23. The walk reports it
    faithfully — form, FILING date (valid-time from EDGAR, #1), accession, primary doc — and judges
    nothing: whether it explains a stopped tape is the monitor's call against the tape's last bar.
    """
    subs = {
        "tickers": ["OPNW"],
        "filings": {
            "recent": {
                "form": ["8-K", "25-NSE"],
                "accessionNumber": ["FILLER-8K", "0001354457-25-000562"],
                "primaryDocument": ["f8k.htm", "primary_doc.xml"],
                "filingDate": ["2026-10-01", "2025-06-23"],
            }
        },
    }
    assert delisting_filings(subs) == (
        DelistingFiling(
            form="25-NSE",
            filed=date(2025, 6, 23),
            accession="0001354457-25-000562",
            primary_doc="primary_doc.xml",
        ),
    )


def test_delisting_filings_recognizes_every_delisting_form_type():
    """All four SEC delisting forms count — 25 / 25-NSE (removal from listing) and 15-12B / 15-12G
    (deregistration) — the deterministic evidence (#3, never an LLM)."""
    for form in ("25", "25-NSE", "15-12B", "15-12G"):
        got = delisting_filings(_recent_dated([(form, "2026-07-01")]))
        assert [(f.form, f.filed) for f in got] == [(form, date(2026, 7, 1))]


def test_delisting_filings_returns_ALL_of_them_newest_first():
    """No single pick here: a company can carry a closing pair beside old, unrelated forms (GTLS's real
    record: a 2021 Form 25, a 2025-12 25-NSE, then the 2026-07 25-NSE + 15-12G). Every one is returned,
    newest first, so the monitor can choose by proximity to the tape's last bar."""
    subs = _recent_dated(
        [
            ("15-12G", "2026-07-29"),
            ("25-NSE", "2026-07-16"),
            ("10-Q", "2026-05-01"),
            ("25-NSE", "2025-12-15"),
            ("25", "2021-01-29"),
        ]
    )
    assert [(f.form, f.filed.isoformat()) for f in delisting_filings(subs)] == [
        ("15-12G", "2026-07-29"),
        ("25-NSE", "2026-07-16"),
        ("25-NSE", "2025-12-15"),
        ("25", "2021-01-29"),
    ]


def test_delisting_filings_SKIPS_a_malformed_filing_date():
    """A blank or malformed filing date is not evidence: that row is skipped (never guessed), the rest
    still read, and nothing raises inside the nightly ingest loop."""
    subs = _recent_dated([("25", "not-a-date"), ("15-12G", ""), ("25-NSE", "2026-07-01")])
    assert [f.filed for f in delisting_filings(subs)] == [date(2026, 7, 1)]


def test_edgar_tickers_reads_the_CURRENT_list_in_EDGARs_order():
    """EDGAR's ``tickers`` array, stripped + upper-cased, order kept (the real multi-class shape:
    ['KYOCF', 'KYOAY']). Absent / empty → () — honest absence, never a rename."""
    assert edgar_tickers({"tickers": ["KYOCF", " kyoay ", ""]}) == ("KYOCF", "KYOAY")
    assert edgar_tickers({"tickers": []}) == ()
    assert edgar_tickers({}) == ()


# --- deregistration: a Form 15 and the 8-K events before it (W4-2) ---------------------------------------
#
# Two shapes, read from real EDGAR records of two never-priced basket members and put under neutral labels
# and accessions (the fixture convention): a reorganization in bankruptcy (the equity cancelled; the
# successor's 8-K15D5 and the Form 15 on one day) and an acquisition (an 8-K with items 2.01 / 3.03 / 5.01 six
# days before the Form 15). Their item lists are the real ones; the words are SEC's item titles.


def _dereg_doc(tickers: list[str], rows: list[tuple[str, str, str, str]]) -> dict:
    """A submissions JSON in EDGAR's shape: ``tickers``, ``cik`` and the parallel, reverse-chrono
    ``filings.recent`` arrays, one row per (form, filingDate, accession, items)."""
    rows = sorted(rows, key=lambda r: r[1], reverse=True)
    return {
        "cik": "0009999801",
        "tickers": tickers,
        "filings": {
            "recent": {
                "form": [r[0] for r in rows],
                "filingDate": [r[1] for r in rows],
                "accessionNumber": [r[2] for r in rows],
                "items": [r[3] for r in rows],
                "primaryDocument": [f"{r[2]}.htm" for r in rows],
            }
        },
    }


# the bankruptcy shape: the plan's 8-K (1.03) 13 days before, an older 8-K (1.03) 56 days before (outside
# the window), the exchange's 25-NSE (not an event) and the successor's 8-K15D5 beside the 15-12G
TKBK = _dereg_doc(
    [],
    [
        ("15-12G", "2026-05-20", "0009999801-26-000009", ""),
        (
            "8-K15D5",
            "2026-05-20",
            "0009999801-26-000008",
            "1.01,1.02,1.03,2.03,3.02,3.03,4.01,5.01,5.02,5.03,7.01,8.01,9.01",
        ),
        ("8-K", "2026-05-07", "0009999801-26-000007", "1.03,7.01,9.01"),
        ("25-NSE", "2026-05-01", "0009999801-26-000006", ""),
        ("8-K", "2026-03-25", "0009999801-26-000005", "1.01,1.03,2.04,7.01,8.01,9.01"),
    ],
)
# the acquisition shape: the closing 8-K six days before the 15-12G, a vote 8-K (5.07) and an 8.01 before it
TKAQ = _dereg_doc(
    [],
    [
        ("15-12G", "2026-06-10", "0009999802-26-000004", ""),
        ("8-K", "2026-06-04", "0009999802-26-000003", "1.02,2.01,3.03,5.01,5.02,5.03,8.01,9.01"),
        ("8-K", "2026-05-18", "0009999802-26-000002", "5.07"),
        ("8-K", "2026-05-07", "0009999802-26-000001", "8.01"),
    ],
)


def test_the_item_words_are_SECs_own_item_titles():
    """The evidence words are the Form 8-K item titles, pinned verbatim: nothing is read from a document."""
    assert DEREGISTRATION_EVENT_ITEMS == {
        "1.03": "Bankruptcy or Receivership",
        "2.01": "Completion of Acquisition or Disposition of Assets",
        "3.03": "Material Modification to Rights of Security Holders",
        "5.01": "Changes in Control of Registrant",
    }
    assert DEREGISTRATION_FORMS == frozenset(
        {"15-12B", "15-12G", "15-15D", "15F-12B", "15F-12G", "15F-15D"}
    )
    assert DEREGISTRATION_EVENT_DAYS == 45


def test_a_BANKRUPTCY_shape_carries_the_form_15_the_8_K15D5_and_the_bankruptcy_title():
    """15-12G and the successor's 8-K15D5 on one day, the 1.03 8-K 13 days before: both events, newest
    first, each with only its evidence items and SEC's titles for them. The 56-day-old 8-K is outside the
    window and the 25-NSE is no event."""
    d = deregistration(TKBK)
    assert d == Deregistration(
        form="15-12G",
        filed=date(2026, 5, 20),
        accession="0009999801-26-000009",
        primary_doc="0009999801-26-000009.htm",
        cik="0009999801",
        events=(
            DeregistrationEvent(
                form="8-K15D5",
                filed=date(2026, 5, 20),
                accession="0009999801-26-000008",
                items=("1.03", "3.03", "5.01"),
            ),
            DeregistrationEvent(
                form="8-K",
                filed=date(2026, 5, 7),
                accession="0009999801-26-000007",
                items=("1.03",),
            ),
        ),
    )
    assert d.events[0].item_titles == (
        "Bankruptcy or Receivership",
        "Material Modification to Rights of Security Holders",
        "Changes in Control of Registrant",
    )
    assert "Bankruptcy or Receivership" in d.describe()
    assert d.describe().startswith("15-12G filed 2026-05-20")


def test_an_ACQUISITION_shape_carries_the_closing_8_K_and_no_bankruptcy_word():
    """15-12G with the closing 8-K six days before (items 2.01 / 3.03 / 5.01 among others): one event, the
    acquisition titles, never "Bankruptcy". The 5.07 and 8.01 8-Ks carry no evidence item."""
    d = deregistration(TKAQ)
    assert (d.form, d.filed, d.accession) == ("15-12G", date(2026, 6, 10), "0009999802-26-000004")
    (e,) = d.events
    assert (e.form, e.filed, e.items) == ("8-K", date(2026, 6, 4), ("2.01", "3.03", "5.01"))
    assert e.item_titles == (
        "Completion of Acquisition or Disposition of Assets",
        "Material Modification to Rights of Security Holders",
        "Changes in Control of Registrant",
    )
    assert "Bankruptcy" not in d.describe()
    assert "Completion of Acquisition or Disposition of Assets" in d.describe()


def test_NO_form_15_is_None():
    """A 25-NSE and 8-Ks without a Form 15: no deregistration. A sparse doc: None, never a raise."""
    doc = _dereg_doc(
        [],
        [
            ("25-NSE", "2026-05-01", "0009999803-26-000002", ""),
            ("8-K", "2026-04-28", "0009999803-26-000001", "1.03"),
        ],
    )
    assert deregistration(doc) is None
    assert deregistration({}) is None
    assert (
        deregistration({"filings": {"recent": {"form": ["15-12G"]}}}) is None
    )  # no date: not evidence


def test_a_form_15_beside_CURRENT_tickers_is_still_read_as_a_fact():
    """The reader judges nothing: a company that deregistered one class and still lists symbols (a
    redomicile: 15-12G + 8-K12B on one day) is read. Whether a ROW carries it is the tape monitor's call
    (only a never-priced row whose EDGAR list is empty — ``tests/pipeline/test_tape_health.py``)."""
    doc = _dereg_doc(
        ["TKRD", "TKRD-WT"],
        [
            ("15-12G", "2026-06-16", "0009999804-26-000002", ""),
            ("8-K12B", "2026-06-16", "0009999804-26-000001", "1.01,3.01,3.02,3.03,5.01,5.02,5.03"),
        ],
    )
    d = deregistration(doc)
    assert d is not None and d.form == "15-12G"
    assert [(e.form, e.items) for e in d.events] == [("8-K12B", ("3.03", "5.01"))]


def test_every_deregistration_form_counts_and_the_NEWEST_wins():
    for form in sorted(DEREGISTRATION_FORMS):
        d = deregistration(_dereg_doc([], [(form, "2026-07-01", "0009999805-26-000001", "")]))
        assert d is not None and (d.form, d.filed) == (form, date(2026, 7, 1)), form
    doc = _dereg_doc(
        [],
        [
            ("15-15D", "2026-08-01", "0009999806-26-000002", ""),
            ("15-12B", "2026-07-01", "0009999806-26-000001", ""),
            ("25", "2026-06-20", "0009999806-26-000000", ""),
        ],
    )
    assert deregistration(doc).form == "15-15D"


def test_the_event_window_is_45_days_up_to_the_form_15_inclusive():
    """An 8-K filed on the Form 15's day and one 45 days before count; 46 days before and the day after do
    not. An 8-K15D5 counts by its form even with no evidence item; a malformed date is skipped."""
    doc = _dereg_doc(
        [],
        [
            ("8-K", "2026-07-02", "0009999807-26-000006", "1.03"),  # the day after
            ("15-12G", "2026-07-01", "0009999807-26-000005", ""),
            ("8-K", "2026-07-01", "0009999807-26-000004", "5.01"),
            ("8-K15D5", "2026-06-20", "0009999807-26-000003", "8.01"),
            ("8-K", "2026-05-17", "0009999807-26-000002", "2.01"),  # 45 days before
            ("8-K", "2026-05-16", "0009999807-26-000001", "3.03"),  # 46 days before
            ("8-K", "not-a-date", "0009999807-26-000000", "1.03"),
        ],
    )
    d = deregistration(doc)
    assert [(e.form, e.filed.isoformat(), e.items) for e in d.events] == [
        ("8-K", "2026-07-01", ("5.01",)),
        ("8-K15D5", "2026-06-20", ()),
        ("8-K", "2026-05-17", ("2.01",)),
    ]


def test_the_deregistration_round_trips_through_its_json():
    d = deregistration(TKBK)
    assert Deregistration.from_json(d.to_json()) == d
    assert d.to_json()["events"][1] == {
        "form": "8-K",
        "filed": "2026-05-07",
        "accession": "0009999801-26-000007",
        "items": ["1.03"],
    }
    assert Deregistration.from_json(None) is None
    assert Deregistration.from_json({"form": "15-12G"}) is None  # no date: not evidence
