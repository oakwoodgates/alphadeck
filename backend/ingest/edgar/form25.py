"""Q6 — WHICH security class a Form 25 removed from listing (the tape monitor's "closed" evidence).

THE GAP. The tape monitor (``pipeline/tape_health.classify_stop``, T1) reads a Form 25 / 25-NSE filed near
a stopped tape's last bar as evidence the name CLOSED. But a company files one for ANY of its listed
classes, and the filing date cannot tell them apart: VerifyMe's 2025-06-23 Form 25-NSE removed its
WARRANTS ("Warrant expiring 6/23/2025") while the common kept trading. MEASURED 2026-10-17 on prod (the
basket's cached submissions, each near-window Form 25 fetched through ``EdgarClient``): ACHR, ARBE, BKKT,
IONQ, KTTA and PDYN each carry a 25-NSE filed in the last 30 days that removed WARRANTS, so any of them
would have read "closed" the night its tape stopped. The document names the class, so this module reads it.

WHAT IT IS:

- **A fact reader, never a judgment of the tape.** ``class_description`` returns the filing's own words
  (deterministic text extraction, #3 — never a model's reading); ``class_kind`` maps them to ``EQUITY``
  (the common / ordinary stock, or an ADS — the security a basket member IS), ``OTHER`` (warrants, units,
  rights, notes, debentures, preferred), or ``None`` (unknown). The POLICY — only a Form 25 that names
  ANOTHER class stops counting; an unknown one still counts — is the monitor's (``classify_stop``).
- **One immutable fetch per filing.** The document sits under ``forms/<accession>/`` in the EDGAR cache,
  which never expires (``ingest/edgar/client._IMMUTABLE_PREFIXES``), through the polite client (declared
  User-Agent, rate limit). The monitor asks only for a STALE name whose window holds a Form 25, so the
  network is touched once, the first night a name goes stale; every later night is a disk read.
- **Fail-safe toward today's reading.** No CIK, a paper filing, a fetch that fails, a document with no
  class line, or a description that names no class this module knows → ``None``, which the monitor counts
  as a delisting exactly as it did before Q6 (``delisted_class_is_other`` prints one line saying so).

THE TWO DOCUMENT SHAPES, both MEASURED on real filings (the exchange-filed 25-NSE and the issuer-filed 25):

1. **XML** (``notificationOfRemoval``, ``primary_doc.xml``, every 25-NSE measured):
   ``<descriptionClassSecurity>Redeemable Warrants, each whole warrant exercisable for ...</...>``.
2. **The SEC Form 25 template as HTML or text** (issuer-filed, e.g. ``tm268975d1_25.htm``): the class
   sits between the address caption ``(Address, including zip code, ... offices)`` and the caption
   ``(Description of class of securities)`` — "Common Stock - par value $0.01 per share".

HOW A DESCRIPTION IS READ (``class_kind``). A Form 25 description leads with the class it removes and then
describes it: "Warrants to purchase Class A Common Stock, each ...", "Units, each consisting of one share
of Class A common stock and one-half of one redeemable warrant", "American Depositary Shares, each
representing one Ordinary Share". So the LEADING phrase decides, never a keyword anywhere (every warrant
description above names the common it converts into). The description is split on ``;`` into the classes
it lists ("Common Stock; Redeemable Warrants ..." — MEASURED, one filing removing two classes), each one
cut at the first word that starts its explanation (``,`` ``(`` ``each`` ``to purchase`` ``exercisable``
``representing`` ``consisting`` ``convertible`` ``expiring`` ``and`` ``&`` ...), and the leading phrase is
matched in a fixed order:

1. a DEPOSITARY phrase (``American Depositary Shares``, ``Dep Shs``) is the ADR's own security, EQUITY —
   unless its class names preferred stock or notes (``Depositary Shares, each representing 1/20th ...
   Preferred Stock``, ``Dep Shs Rep Series B ... Pfd Stk`` — XOMA's, MEASURED), which is OTHER;
2. ``preferred`` / ``pfd`` / ``preference`` → OTHER;
3. ``note(s)`` / ``debenture(s)`` / ``bond(s)`` → OTHER;
4. ``warrant(s)`` / ``right(s)`` → OTHER (``Contingent Value Rights`` too);
5. ``unit(s)`` → OTHER (the SPAC unit), unless the class is a partnership's or a trust's equity
   (``Limited Partnership Units``, ``Units of beneficial interest``, ``Common Units``) → EQUITY;
6. ``common`` / ``ordinary`` / ``beneficial interest`` / ``capital stock`` → EQUITY;
7. anything else → ``None`` (unknown; counted).

ANY listed class that reads EQUITY makes the filing EQUITY (it removed the common, whatever else it
removed); otherwise any unknown one makes it unknown; only a filing whose every class reads OTHER is
OTHER. That ordering keeps the one direction that matters safe: reading a COMMON delisting as OTHER would
keep a genuinely closed name LOUD (visible friction, never a silence); reading a warrant delisting as
EQUITY or unknown is exactly today's reading. ACCEPTED EDGES, none seen in the measured filings: a
description that LEADS with another class and lists the common second with a comma ("Warrants, Common
Stock") reads OTHER — exchanges file one Form 25 per class (XOMA's preferred and common were two
filings), and a combined one MEASURED so far leads with the common or separates with ``;``; a company
whose NAME carries a class word ("Preferred Bank Common Stock") reads OTHER — both are the loud direction.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ingest.edgar.submissions import form4_doc_url

if TYPE_CHECKING:  # the monitor's record; imported for typing only
    from ingest.edgar.client import EdgarClient
    from ingest.edgar.submissions import DelistingFiling

# The two readings ``class_kind`` can conclude (``None`` = unknown).
EQUITY = "equity"  # the common / ordinary stock, or an ADS: the security a basket member IS
OTHER = "other"  # warrants, units, rights, notes, debentures, preferred: another listed class

# The forms whose document names the class removed — the removal-from-listing pair. The deregistration
# forms (15-12B / 15-12G) are not read: Q6 scoped the class check to the Form 25.
FORM_25 = frozenset({"25", "25-NSE"})

_XML_CLASS = re.compile(
    r"<descriptionClassSecurity>\s*(.*?)\s*</descriptionClassSecurity>", re.IGNORECASE | re.DOTALL
)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
# The SEC Form 25 template: the class description sits between the address caption and its own caption.
_TEMPLATE_CLASS = re.compile(
    r"\(\s*address[^)]*\)\s*(.*?)\s*\(\s*description\s+of\s+(?:the\s+)?class(?:es)?\s+of\s+"
    r"securit(?:y|ies)\s*\)",
    re.IGNORECASE | re.DOTALL,
)
# A description longer than this is not a class line (a template the regex mis-spanned): unknown, never
# a guess.
_MAX_DESCRIPTION = 400
# Template residue BEFORE the class words: an older template's extra caption ("(Zip Code) Common Stock",
# MEASURED on a 2006 text filing), a stray ")" / ":" / a fill-in rule ("_____ Common stock").
_LEADING_RESIDUE = re.compile(r"^(?:\([^)]*\)|[\s:;)_.\-–—•])+")

# Where a class's EXPLANATION starts — the leading phrase before it names the class (see the module doc).
_HEAD_CUT = re.compile(
    r",|\(|\s[-&]\s|\s(?:each|every|to\s+purchase|to\s+acquire|exercisable|representing|rep|"
    r"consisting|convertible\s+into|expiring|exp|entitling|which|and|with|par\s+value|until|due)\b",
    re.IGNORECASE,
)
_DEPOSITARY = re.compile(r"\bdepositary\b|\bdep\.?\s+shs?\b|\badss?\b|\badrs?\b", re.IGNORECASE)
_PREFERRED = re.compile(r"\bpreferred\b|\bpfd\b|\bpreference\b", re.IGNORECASE)
_DEBT = re.compile(r"\bnotes?\b|\bdebentures?\b|\bbonds?\b", re.IGNORECASE)
_WARRANT_RIGHT = re.compile(r"\bwarrants?\b|\brights?\b", re.IGNORECASE)
_UNIT = re.compile(r"\bunits?\b", re.IGNORECASE)
_EQUITY_UNIT = re.compile(
    r"\bpartnership\b|\blimited\s+partner|\bbeneficial\s+interest\b|\bcommon\s+units?\b",
    re.IGNORECASE,
)
_EQUITY = re.compile(
    r"\bcommon\b|\bordinary\b|\bbeneficial\s+interest\b|\bcapital\s+stock\b", re.IGNORECASE
)


@dataclass(frozen=True)
class Form25Class:
    """What one Form 25 says it removed: ``text`` (the filing's own class description, entities unescaped
    and whitespace collapsed — display/log evidence, #6) and ``kind`` (``EQUITY`` / ``OTHER`` / ``None``).
    ``why`` says why ``kind`` is ``None`` when it is (no document, a failed fetch, no class line, an
    unrecognized description) — the log line's reason, never a judgment."""

    text: str | None
    kind: str | None
    why: str | None = None


def _clean(raw: str) -> str:
    return _WS.sub(" ", html.unescape(raw)).strip()


def class_description(doc: str) -> str | None:
    """The class a Form 25 document says it removes, in the filing's own words — or ``None`` when it states
    none this reader can find. Pure. Tries the XML element first (``descriptionClassSecurity``, the 25-NSE
    shape), then the SEC template's caption (the issuer-filed HTML / text shape). An empty or implausibly
    long match is ``None``, never a guess."""
    m = _XML_CLASS.search(doc)
    if m:
        text = _clean(_TAG.sub(" ", m.group(1)))
    else:
        flat = _clean(_TAG.sub(" ", doc))
        t = _TEMPLATE_CLASS.search(flat)
        text = _LEADING_RESIDUE.sub("", t.group(1)).strip() if t else ""
    if not text or len(text) > _MAX_DESCRIPTION:
        return None
    return text


def _head(segment: str) -> str:
    """A listed class's LEADING phrase: the text before its explanation starts (``_HEAD_CUT``)."""
    m = _HEAD_CUT.search(segment)
    return (segment[: m.start()] if m else segment).strip()


def _segment_kind(segment: str) -> str | None:
    """One listed class (a ``;``-separated part of the description) → EQUITY / OTHER / None, by its
    leading phrase in the fixed order the module docstring sets out."""
    head = _head(segment)
    if not head:
        return None
    if _DEPOSITARY.search(head):
        return OTHER if (_PREFERRED.search(segment) or _DEBT.search(segment)) else EQUITY
    if _PREFERRED.search(head) or _DEBT.search(head) or _WARRANT_RIGHT.search(head):
        return OTHER
    if _UNIT.search(head):
        return EQUITY if _EQUITY_UNIT.search(segment) else OTHER
    if _EQUITY.search(head):
        return EQUITY
    return None


def class_kind(text: str | None) -> str | None:
    """Map a Form 25 class description to ``EQUITY`` / ``OTHER`` / ``None`` (unknown). Pure. ANY listed
    class reading EQUITY → EQUITY; else any unknown → ``None``; only all-OTHER → OTHER (the order that
    keeps a misread on the loud side — see the module docstring)."""
    if not text:
        return None
    kinds = [_segment_kind(s) for s in text.split(";") if s.strip()]
    if not kinds:
        return None
    if EQUITY in kinds:
        return EQUITY
    if None in kinds:
        return None
    return OTHER


def fetch_form25_class(client: EdgarClient, filing: DelistingFiling) -> Form25Class:
    """Read ONE Form 25's class through ``client`` — the cached, immutable ``forms/<accession>/<doc>`` key,
    so a document is fetched at most once. NEVER raises: this feeds a monitor inside the nightly loop, so
    every failure (no CIK on the record, a paper filing with no document, a fetch or decode error, a
    document with no class line) is ``kind=None`` with its ``why`` — which the monitor counts as a
    delisting, exactly as it did before Q6."""
    if filing.form not in FORM_25:
        return Form25Class(text=None, kind=None, why=f"form {filing.form} carries no class to read")
    doc_name = (filing.primary_doc or "").rsplit("/", 1)[-1]
    if not doc_name or doc_name.lower().endswith(".paper"):
        return Form25Class(text=None, kind=None, why="a paper filing (no document to read)")
    if not filing.cik:
        return Form25Class(text=None, kind=None, why="no CIK on the filing record")
    try:
        doc = client.get_text(
            form4_doc_url(filing.cik, filing.accession, filing.primary_doc),
            f"forms/{filing.accession}/{doc_name}",
        )
    except Exception as e:  # noqa: BLE001 — a monitor read never costs the pass; unknown is counted
        msg = " ".join(str(e).split())[:160]
        return Form25Class(text=None, kind=None, why=f"the document could not be read ({msg})")
    text = class_description(doc)
    if text is None:
        return Form25Class(
            text=None, kind=None, why="the document states no class this reader finds"
        )
    kind = class_kind(text)
    return Form25Class(
        text=text, kind=kind, why=None if kind else f'an unrecognized class "{text}"'
    )


def delisted_class_is_other(client: EdgarClient, label: str, filing: DelistingFiling) -> bool:
    """The tape monitor's hook (``tape_health.classify_stop``'s ``removes_other_class``): does this Form
    25 remove a class OTHER than the member's own common / ordinary / ADS? ``True`` only on a positive
    OTHER reading. An unknown class is ``False`` — the filing still counts as this security's delisting
    (today's reading, the fail-safe Q6 kept) — and prints ONE line naming the name, the filing and why, so
    an unknown that silenced a tape is never silent itself."""
    c = fetch_form25_class(client, filing)
    if c.kind is None:
        print(
            f"  warn: {label} Form {filing.form} {filing.accession} (filed {filing.filed}): the delisted "
            f"class is unknown — {c.why}; counted as a delisting of this security (fail-safe)"
        )
    return c.kind == OTHER
