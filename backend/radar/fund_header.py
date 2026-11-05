"""The pure parser for an EDGAR submission header (``<accession>.hdr.sgml``) — the fund launch radar's one
source of fund facts (docs/FUND_RADAR.md).

The header is SGML with UNCLOSED leaf tags (``<SERIES-NAME>Some Fund`` runs to the end of the line) and a
handful of paired containers. Read line by line: a leaf tag carries its value on the same line, so no tree is
needed and a raw ``&`` in a name ("Science &Technology") is just text — the names are NOT HTML-unescaped
(``&Technology`` would be mangled by a lenient decoder).

What it reads:
- the envelope: accession, form, ``ACCEPTANCE-DATETIME`` (ET wall clock → an aware instant, via the shared
  ``parse_header_acceptance``), ``FILING-DATE``, ``EFFECTIVENESS-DATE``, the ``<PRIVATE-TO-PUBLIC>`` flag and
  the RELEASE date from the first line (``<SEC-HEADER>….hdr.sgml : 20261104``);
- the filers: one ``<FILER>`` block per co-registrant (CIK + conformed name);
- the series: ``<NEW-SERIES>`` (a new fund), ``<NEW-CLASSES-CONTRACTS>`` (new share classes on an existing
  fund) and ``<SERIES>`` inside ``<EXISTING-SERIES-AND-CLASSES-CONTRACTS>`` (existing funds), each with its
  classes (class ID, name, optional ticker).

A header with no series block (S-1, N-8A) parses to an empty ``series`` tuple. Pure: no I/O, no clock.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime

from ingest.edgar.acceptance import parse_header_acceptance

KIND_NEW = "new"  # a new fund (series) the filing registers
KIND_NEW_CLASSES = "new_classes"  # new share classes on an existing fund
KIND_EXISTING = "existing"  # an existing fund the filing covers

_LINE = re.compile(r"^<(/?)([A-Z0-9-]+)>(.*)$")
_FIRST_LINE = re.compile(r"^<SEC-HEADER>.*?:\s*(\d{8})\s*$")

_CONTAINER_KIND = {
    "NEW-SERIES": KIND_NEW,
    "NEW-CLASSES-CONTRACTS": KIND_NEW_CLASSES,
    "EXISTING-SERIES-AND-CLASSES-CONTRACTS": KIND_EXISTING,
}


class HeaderParseError(ValueError):
    """The text is not a usable submission header (no accession or form)."""


@dataclass(frozen=True)
class FundClass:
    class_id: str
    name: str
    ticker: str | None = None


@dataclass(frozen=True)
class FundSeries:
    series_id: str
    name: str
    kind: str
    classes: tuple[FundClass, ...] = ()


@dataclass(frozen=True)
class FundFiler:
    cik: str  # zero-padded 10 digits, as the header gives it
    name: str


@dataclass(frozen=True)
class FundHeader:
    accession: str
    form: str
    accepted_at: datetime | None
    filing_date: date | None
    effectiveness_date: date | None
    private_to_public: bool
    released_on: (
        date | None
    )  # the first line's date — the RELEASE day for a private-to-public filing
    filers: tuple[FundFiler, ...] = ()
    series: tuple[FundSeries, ...] = field(default_factory=tuple)


def _ymd(s: str | None) -> date | None:
    if not s or not re.fullmatch(r"\d{8}", s.strip()):
        return None
    try:
        return datetime.strptime(s.strip(), "%Y%m%d").date()
    except ValueError:
        return None


def parse_header(text: str) -> FundHeader:
    """Parse one header. Raises ``HeaderParseError`` when the accession or form is missing (an error page, a
    truncated body) — the caller records it, never stores a half-read filing."""
    accession: str | None = None
    form: str | None = None
    filing_date: date | None = None
    effectiveness: date | None = None
    private = False
    released: date | None = None

    filers: list[FundFiler] = []
    in_filer = False
    in_company = False
    cur_name: str | None = None
    cur_cik: str | None = None

    series: list[dict] = []
    kind: str | None = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("<SEC-HEADER>"):
            m0 = _FIRST_LINE.match(line)
            if m0:
                released = _ymd(m0.group(1))
            continue
        m = _LINE.match(line)
        if m is None:
            continue
        closing, tag, value = m.group(1), m.group(2), m.group(3).strip()
        if closing:
            if tag == "FILER":
                if cur_cik:
                    filers.append(FundFiler(cik=cur_cik, name=cur_name or ""))
                in_filer = in_company = False
                cur_name = cur_cik = None
            elif tag == "COMPANY-DATA":
                in_company = False
            elif tag in ("NEW-SERIES", "NEW-CLASSES-CONTRACTS", "SERIES"):
                pass
            elif tag == "NEW-SERIES-AND-CLASSES-CONTRACTS":
                kind = None
            elif tag == "EXISTING-SERIES-AND-CLASSES-CONTRACTS":
                kind = None
            continue
        if tag == "FILER":
            in_filer, in_company = True, False
            cur_name = cur_cik = None
        elif tag == "COMPANY-DATA":
            in_company = True
        elif in_filer and in_company and tag == "CONFORMED-NAME":
            cur_name = value
        elif in_filer and in_company and tag == "CIK":
            cur_cik = value.zfill(10) if value.isdigit() else None
        elif tag == "PRIVATE-TO-PUBLIC":
            private = True
        elif not in_filer and tag == "ACCESSION-NUMBER":
            accession = value
        elif not in_filer and tag == "TYPE":
            form = value
        elif not in_filer and tag == "FILING-DATE":
            filing_date = _ymd(value)
        elif not in_filer and tag == "EFFECTIVENESS-DATE":
            effectiveness = _ymd(value)
        elif tag in _CONTAINER_KIND:
            kind = _CONTAINER_KIND[tag]
        elif tag == "SERIES":  # <SERIES> inside EXISTING-SERIES-AND-CLASSES-CONTRACTS
            kind = KIND_EXISTING
        elif tag == "SERIES-ID":
            series.append({"id": value, "name": "", "kind": kind or KIND_EXISTING, "classes": []})
        elif tag == "SERIES-NAME" and series:
            series[-1]["name"] = value
        elif tag == "CLASS-CONTRACT-ID" and series:
            series[-1]["classes"].append({"id": value, "name": "", "ticker": None})
        elif tag == "CLASS-CONTRACT-NAME" and series and series[-1]["classes"]:
            series[-1]["classes"][-1]["name"] = value
        elif tag == "CLASS-CONTRACT-TICKER-SYMBOL" and series and series[-1]["classes"]:
            series[-1]["classes"][-1]["ticker"] = value.upper() or None

    if not accession or not form:
        raise HeaderParseError("not a submission header: no accession number or form type")

    # one entry per co-registrant CIK (a 485 lists the trust once per 1933/1940 Act file number)
    seen: set[str] = set()
    uniq: list[FundFiler] = []
    for f in filers:
        if f.cik not in seen:
            seen.add(f.cik)
            uniq.append(f)

    return FundHeader(
        accession=accession,
        form=form,
        accepted_at=parse_header_acceptance(text),
        filing_date=filing_date,
        effectiveness_date=effectiveness,
        private_to_public=private,
        released_on=released,
        filers=tuple(uniq),
        series=tuple(
            FundSeries(
                series_id=s["id"],
                name=s["name"],
                kind=s["kind"],
                classes=tuple(FundClass(c["id"], c["name"], c["ticker"]) for c in s["classes"]),
            )
            for s in series
        ),
    )
