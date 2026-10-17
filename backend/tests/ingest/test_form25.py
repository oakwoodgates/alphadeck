"""Q6 — WHICH class a Form 25 removed (``ingest/edgar/form25.py``), on REAL filings.

PROVENANCE. Every document under ``tests/fixtures/edgar/form25_cache/forms/`` is a real EDGAR primary
document, fetched VERBATIM on 2026-10-17 through ``EdgarClient`` (declared User-Agent, rate-limited) from
the Archives URL ``form4_doc_url`` builds. The 25-NSEs are every Form 25 filed inside the tape monitor's
window ([last bar - 30, + 45] days) for the 15 names Q6 was measured on — read from prod's cached
submissions on 2026-10-17 — plus VRME's 2025 warrant 25-NSE (the T1 origin finding). The two issuer-filed
documents (United States Antimony 2026 HTML, Frequency Electronics 2006 text) are the SEC template shape.
The tests read them through the REAL client (``allow_live=False``: a cache miss raises, so the suite never
touches the network), exercising the fetch path, the cache key and the parser as the nightly pass runs them.

The ``class_kind`` strings are real ``descriptionClassSecurity`` / template values too, each cited by the
accession of the EDGAR Form 25 document that states it (from the cache of real Form 25s gathered in the
T1/Q6 investigation). Inputs that are not a filing's own values are labeled CONTRACT inputs.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from ingest.edgar.client import EdgarClient
from ingest.edgar.form25 import (
    EQUITY,
    FORM_25,
    OTHER,
    class_description,
    class_kind,
    delisted_class_is_other,
    fetch_form25_class,
)
from ingest.edgar.submissions import DelistingFiling, delisting_filings, form4_doc_url
from pipeline import tape_health

_CACHE = Path(__file__).resolve().parents[1] / "fixtures" / "edgar" / "form25_cache"


def _client(cache: Path = _CACHE) -> EdgarClient:
    return EdgarClient(cache_dir=cache, allow_live=False)  # a cache miss raises; never the network


def _f25(
    cik: str, filed: str, accession: str, doc: str = "xslF25X02/primary_doc.xml", form="25-NSE"
):
    return DelistingFiling(
        form=form, filed=date.fromisoformat(filed), accession=accession, primary_doc=doc, cik=cik
    )


# The real window 25-NSEs (the rows `delisting_filings` reads from each name's submissions: cik, filing
# date, accession, primary document) and the class each document states, verbatim.
REAL_25_NSE = {
    # the 7 still-trading names (prod 2026-10-17) — a 25-NSE inside 30 days of the last bar
    "ACHR": (
        _f25("0001824502", "2026-09-16", "0000876661-26-000755"),
        "Redeemable Warrants, each whole warrant exercisable for one Class A common stock at an "
        "exercise price of $11.50",
        OTHER,
    ),
    "ARBE": (_f25("0001861841", "2026-10-06", "0001354457-26-000957"), "Warrant", OTHER),
    "BKKT": (
        _f25("0001820302", "2026-10-15", "0000876661-26-000837"),
        "Warrants to purchase Class A Common Stock, each Warrant entitles a holder to purchase 1/25th "
        "of a share of Class A common stock at an exercise price of $287.50 for one share of Class A "
        "Common Stock",
        OTHER,
    ),
    "IONQ": (
        _f25("0001824920", "2026-09-30", "0000876661-26-000804"),
        "Redeemable warrants, each whole warrant exercisable for one share of Common Stock, each at an "
        "exercise price of $11.50 per share",
        OTHER,
    ),
    "KTTA": (_f25("0001841330", "2026-09-16", "0001354457-26-000883"), "warrants", OTHER),
    "PDYN": (_f25("0001826681", "2026-09-28", "0001354457-26-000917"), "Warrant", OTHER),
    "VSTD": (
        _f25("0001898604", "2026-10-09", "0001354457-26-000973"),
        "Class A Common Stock",
        EQUITY,
    ),
    # the 8 genuine closes
    "SKYT": (_f25("0001819974", "2026-07-31", "0001354457-26-000745"), "Common Stock", EQUITY),
    "QRVO": (_f25("0001604778", "2026-10-05", "0001354457-26-000942"), "Common Stock", EQUITY),
    "GTLS": (_f25("0000892553", "2026-07-16", "0000876661-26-000623"), "Common Stock", EQUITY),
    "ATAI": (_f25("0002081043", "2026-09-11", "0001354457-26-000870"), "Common Stock", EQUITY),
    "XOMA-pfd": (
        _f25("0000791908", "2026-07-16", "0001354457-26-000697"),
        "Dep Shs Rep Series B 8.375% Cumulative Pfd Stk & 8.625% Ser A Cumulative Perpetual Pfd Stk",
        OTHER,
    ),
    "XOMA": (_f25("0000791908", "2026-07-14", "0001354457-26-000691"), "Common Stock", EQUITY),
    "CPRX": (_f25("0001369568", "2026-07-15", "0001354457-26-000694"), "Common Stock", EQUITY),
    "AVNS": (_f25("0001606498", "2026-07-27", "0000876661-26-000646"), "Common Stock", EQUITY),
    "RMAX": (
        _f25("0001581091", "2026-08-25", "0000876661-26-000703"),
        "Class A common stock",
        EQUITY,
    ),
    # VRME — the T1 origin: Nasdaq removed its WARRANTS in 2025, the common kept trading 15 months
    "VRME": (
        _f25("0001104038", "2025-06-23", "0001354457-25-000562"),
        "Warrant expiring 6/23/2025",
        OTHER,
    ),
}


@pytest.mark.parametrize("name", sorted(REAL_25_NSE))
def test_the_real_window_25_NSEs_read_their_class_verbatim(name):
    """Through the REAL client + cache key: each document's own class words come back verbatim (entities
    unescaped — XOMA's ``&amp;``), and read to the kind the words state. 6 of the 7 still-trading names'
    filings removed WARRANTS; VSTD's removed its Class A common (a Nasdaq 12d2-2(b) strike, so its tape
    stopping WOULD be a close); every genuine close's filing removed the common — except XOMA's nearer one,
    which removed its preferred depositary shares."""
    filing, text, kind = REAL_25_NSE[name]
    got = fetch_form25_class(_client(), filing)
    assert (got.text, got.kind, got.why) == (text, kind, None)


@pytest.mark.parametrize(
    "cik,accession,doc,text",
    [
        (
            "101538",
            "0001104659-26-028500",
            "tm268975d1_25.htm",
            "Common Stock - par value $0.01 per share",
        ),
        ("39020", "0000039020-06-000021", "form25.txt", "Common Stock, $1.00 par value"),
    ],
)
def test_the_issuer_filed_TEMPLATE_shape_reads_from_html_and_from_2006_text(
    cik, accession, doc, text
):
    """The issuer-filed Form 25 is the SEC template, not XML: the class sits between the address caption
    and "(Description of class of securities)". The 2006 text filing carries an extra "(Zip Code)" caption
    before the class — template residue, stripped, never part of the class. Read through the same client
    and cache key the fetch path uses."""
    raw = _client().get_text(form4_doc_url(cik, accession, doc), f"forms/{accession}/{doc}")
    assert class_description(raw) == text
    assert class_kind(text) == EQUITY


# Real class descriptions -> the kind, each with the accession of the Form 25 that states it. The LEADING
# class decides: every warrant / unit description names the common it converts into, so a keyword-anywhere
# read would call them all equity.
REAL_DESCRIPTIONS = [
    # --- another class: OTHER ---
    (
        "0000876661-20-001282",
        "Units, each consisting of one share of Class A Common Stock and one-half of one redeemable Warrant",
        OTHER,
    ),
    (
        "0000876661-22-000128",
        "Unit, each consisting of one Class A ordinary share and one-third of one redeemable warrant",
        OTHER,
    ),
    ("0001354457-21-000963", "Vector Acquisition Corporation Unit", OTHER),
    ("0001354457-20-000670", "Rights & Units", OTHER),
    ("0001354457-23-000986", "Unit and Rights", OTHER),
    ("0000876661-21-000718", "Preferred Share Purchase Rights", OTHER),
    ("0000876661-21-000003", "Bristol-Myers Squibb Contingent Value Rights", OTHER),
    ("0001354457-18-000277", "Warrants (1 Wt & 824.40 to purchase 1 shr)", OTHER),
    (
        "0001143313-21-000040",
        "Warrants exercisable for one common share until September 20 2021 at an exercise price of $2.45",
        OTHER,
    ),
    (
        "0001143313-25-000103",
        "Warrants to Purchase Shares of Common Stock, Expiring on or before April 25, 2028",
        OTHER,
    ),
    (
        "0000876661-10-000390",
        "Series T 5.75% Senior Public Income NotES (PINES) due January 15, 2044",
        OTHER,
    ),
    ("0000876661-20-000414", "Floating Rate Notes due 2020", OTHER),
    ("0001354457-12-000015", "Debentures convertible into common stock", OTHER),
    ("0000876661-23-000552", "6.00% Series A Mandatory Convertible Preferred Stock", OTHER),
    ("0000876661-17-000351", "Series L Cumulative Preferred Shares of Beneficial Interest", OTHER),
    ("0000876661-17-000418", "Guarantor of the 5.625% Trust Preference Securities", OTHER),
    (
        "0000876661-25-000969",
        "Depositary Shares, each Representing a 1/20th Interest in a Share of 6.75% Series B Mandatory "
        "Convertible Preferred Stock",
        OTHER,
    ),
    ("0000876661-22-000466", "2019 Series A Corporate Units", OTHER),
    ("0000876661-19-000695", "7.00% Tangible Equity Units", OTHER),
    # --- the member's own security: EQUITY ---
    ("0000876661-14-000593", "Common Stock of USEC Inc.", EQUITY),
    ("0000876661-22-000721", "Jacobs Engineering Group Inc. Common Stock", EQUITY),
    ("0001354457-21-000576", "Seagate Technology PLC Ordinary Shares", EQUITY),
    ("0001062993-26-000002", "Common Shares", EQUITY),
    ("0001296945-06-000075", "common stock $0.1 par value", EQUITY),
    (
        "0000876661-26-000744",
        "American Depositary Shares, each representing one Ordinary Share",
        EQUITY,
    ),
    (
        "0000876661-18-000478",
        "American Depositary Shares (each representing Five Common Shares)",
        EQUITY,
    ),
    ("0001354457-09-000192", "American Depositary Receipts", EQUITY),
    ("0000876661-26-000303", "Limited Partnership Units", EQUITY),
    # one filing, several classes — any one that is the common makes it the common's delisting
    (
        "0000876661-24-000878",
        "Common Stock; Redeemable Warrants, each Warrant exercisable for 1/35th of a share of Common "
        "Stock at an exercise price of $402.50",
        EQUITY,
    ),
    ("0001354457-26-000085", "Common Stock, Warrants", EQUITY),
    (
        "0001628280-25-056420",
        "Class A Common Stock - par value $0.10 per share Rights to purchase Series A Participating "
        "Cumulative Preferred Stock, par value $1.00 per share",
        EQUITY,
    ),
]


@pytest.mark.parametrize("accession,text,kind", REAL_DESCRIPTIONS)
def test_class_kind_on_real_descriptions(accession, text, kind):
    assert class_kind(text) == kind, accession


def test_class_kind_UNKNOWN_is_None_never_a_guess():
    """Nothing to read → None; and (a CONTRACT input, not a filing) a description that names no class the
    rules cover → None, which the monitor COUNTS as a delisting — today's reading, the fail-safe."""
    assert class_kind(None) is None and class_kind("") is None and class_kind(" ; ") is None
    assert class_kind("Class A Subordinate Voting Shares") is None  # CONTRACT input


def test_class_description_finds_none_in_a_document_without_a_class_line():
    """The auto-generated stand-in EDGAR serves for a PAPER Form 25 (accession 9999999997-04-000699, from
    the investigation's cache) states no class: None, never a guess."""
    paper = (
        "<DOCUMENT>\n<TYPE>25\n<SEQUENCE>1\n<FILENAME>9999999997-04-000699.paper\n"
        "<DESCRIPTION>AUTO-GENERATED PAPER DOCUMENT\n<TEXT>\n"
        "This document was generated as part of a paper submission.\n"
        "Please reference the Document Control Number 03043230 for access to the original document.\n"
        "</TEXT>\n</DOCUMENT>"
    )
    assert class_description(paper) is None


def test_fetch_NEVER_raises_and_says_why_the_class_is_unknown(tmp_path):
    """Every failure is kind=None with its reason — a monitor read inside the nightly loop never costs the
    pass. A paper filing and a CIK-less record are judged WITHOUT a fetch (an empty cache with live pulls
    off would raise CacheMiss if they tried); an uncached document is the read failure; a deregistration
    form carries no class to read."""
    empty = _client(tmp_path)
    achr = REAL_25_NSE["ACHR"][0]
    # CONTRACT inputs: the real paper accession + its document name, with ACHR's date and no CIK (neither
    # is read on this path); ACHR's real row with its CIK blanked.
    paper = DelistingFiling(
        form="25",
        filed=achr.filed,
        accession="9999999997-04-000699",
        primary_doc="9999999997-04-000699.paper",
    )
    no_cik = DelistingFiling(
        form=achr.form, filed=achr.filed, accession=achr.accession, primary_doc=achr.primary_doc
    )
    # GTLS's real 2026-07-29 15-12G row
    deregistration = _f25(
        "0000892553", "2026-07-29", "0001193125-26-322203", "d135037d1512g.htm", form="15-12G"
    )
    got = {
        "paper": fetch_form25_class(empty, paper),
        "no_cik": fetch_form25_class(empty, no_cik),
        "uncached": fetch_form25_class(empty, achr),
        "15": fetch_form25_class(empty, deregistration),
    }
    assert all(c.kind is None and c.text is None for c in got.values())
    assert "paper filing" in got["paper"].why
    assert "no CIK" in got["no_cik"].why
    assert "could not be read" in got["uncached"].why
    assert "carries no class" in got["15"].why
    assert empty.live_fetches == 0  # nothing reached for the network


def test_the_monitor_hook_is_True_ONLY_on_another_class_and_LOGS_an_unknown(capsys, tmp_path):
    """``delisted_class_is_other``: True on a positive OTHER (ACHR's warrants), False on the common (RMAX)
    — both silent — and False on an UNKNOWN (an unreadable document), which still counts as this
    security's delisting and prints ONE line naming the name, the filing and why."""
    achr = REAL_25_NSE["ACHR"][0]
    rmax = REAL_25_NSE["RMAX"][0]
    assert delisted_class_is_other(_client(), "ACHR", achr) is True
    assert delisted_class_is_other(_client(), "RMAX", rmax) is False
    assert capsys.readouterr().out == ""
    assert delisted_class_is_other(_client(tmp_path), "ACHR", achr) is False  # not cached → unknown
    line = capsys.readouterr().out
    assert "ACHR" in line and "0000876661-26-000755" in line and "2026-09-16" in line
    assert "counted as a delisting of this security (fail-safe)" in line


def test_delisting_filings_carries_the_CIK_so_a_record_can_open_its_document():
    """The submissions doc's own ``cik`` rides every DelistingFiling (the Archives URL needs it) — ACHR's
    real row; a doc without one leaves it blank (the class then reads unknown, and counts)."""
    subs = {
        "cik": "0001824502",
        "filings": {
            "recent": {
                "form": ["25-NSE"],
                "accessionNumber": ["0000876661-26-000755"],
                "primaryDocument": ["xslF25X02/primary_doc.xml"],
                "filingDate": ["2026-09-16"],
            }
        },
    }
    (f,) = delisting_filings(subs)
    assert f == REAL_25_NSE["ACHR"][0]
    assert delisting_filings({**subs, "cik": None})[0].cik == ""


def test_the_Form_25_set_is_the_same_in_the_reader_and_the_monitor():
    """Two homes for one set (the pure monitor imports nothing from ingest at run time) — pinned."""
    assert tape_health.FORM_25 == FORM_25 == frozenset({"25", "25-NSE"})
