"""The shared FILING reader (``docs/dashboards/filing-stream.md``): "what did this company file, and what did the
system store of it", normalized to ONE row shape so every page that shows it reads the same rows. Built with the
Filing Stream, shared since the Read-Throughs table.

Every row is a ``FilingRow``. The sources, each read through the SAME point-in-time view the Cockpit builds
(``baskets.per_company`` over ``PointInTimeData``, with the request's one ``known_at, clock = serve_view(asof)``
pair; invariant #1), plus one plain SELECT for the Form 4/A filings:

- ``8k``: ``pit.corporate_event_facts``, sorted under ``filed``.
- ``form4``: ``pit.insider_txns`` grouped by accession, under the SEC acceptance as a market-time date (else the
  instant we first held it, the public clock's own rule for a filing with no acceptance).
- ``form4a``: the ``insider_amendment`` records (one SELECT) plus the rows an applied 4/A put on the Form 4 tape,
  under ``filed`` (else the acceptance date).
- ``stake``: ``pit.activist_stake_facts``, under ``filed``.
- ``revenue``: ``pit.fundamentals_facts`` (``revenue``) grouped by accession, under ``valid_from`` (the 10-Q /
  10-K filing date).
- ``split``: ``pit.split_events``, under the split's own publication as the view knows it.
- ``dilution``: ``pit.dilution_facts``, under ``valid_from`` (the issuance date).
- ``catalyst``: ``pit.catalyst_facts``, under ``valid_from`` (its event date).
- ``ratified``: ``pit.cash_burn_facts`` / ``shares_outstanding_facts`` / ``revenue_mix_facts``, under the instant
  the operator ratified it (the public clock's own instant for those three tables).

The rules, in one place:

- **Pure normalizers.** Each ``rows_from_*`` takes the plain row dicts the point-in-time view returns and builds
  ``FilingRow``s. No clock, no database, no network: hand-testable. The page's words (headline, detail, tags) are
  built HERE, neutral: what the data shows, never a verdict. A tag is a PURE FIELD PREDICATE over the stored row
  (``issuer self-filing``, ``internal transfer``, ``10b5-1 plan``): it never mutates a row, and a missing field
  asserts nothing (a ``NULL`` ``aff_10b5_1`` is not "no plan"; a ``NULL`` footnote list is not "none stated").
- **Facts only.** The reader holds what the ingest stores: 8-Ks (with item codes), Form 4s and 4/As, 13D/G
  stakes, the 10-Q / 10-K revenue series, splits, convertible-note dilution and the operator's ratified facts.
  Other forms are not stored yet; the page says so.
- **Not applied here.** The Cockpit's insider column screens a code-P row against the day's low price and an
  implausible dollar size (they need the price bars and the split scale). The reader does not: it never writes
  "open market", a code-P row with no tag is a purchase and nothing more.
- **Form 4s come through the Cockpit's resolved read.** An APPLIED 4/A's rows replace the original's rows on that
  tape (``domain.insider_amendments``), carrying the 4/A's accession and ``amends_accession``. They are folded
  into the 4/A's own row, never shown as a second filing; the 4/A itself is a filing shown as filed (its
  standing lives on the Admin "Insider amendments" page).
- **A source URL is never made up.** A stored ``source_ref`` that is an http(s) URL is used as is; else the
  EDGAR filing-index URL is built from the company's CIK and the accession (``filing_index_url``); else the row
  has no link.
- **Window.** ``read_filing_rows(..., days=N)`` keeps rows dated from ``asof - N days`` through ``asof``,
  inclusive. A row dated after the as-of cannot occur on a well-formed clock; one is dropped, never shown (#1).

Display only (rule 1): this module READS (the point-in-time view, one SELECT) and writes nothing.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal, NamedTuple
from uuid import UUID

import psycopg
from psycopg import sql

from dashboards.baskets import per_company
from db.clock import db_now
from db.session import DEFAULT_TENANT_ID
from domain.insider_amendments import AMENDS_ACCESSION
from domain.insider_internal_transfers import is_internal_transfer, transfer_with
from domain.insider_joint_reports import JOINT_REPORTERS
from domain.market_time import market_tz
from domain.splits import ratio_label
from securities import master
from signals.corporate_events import item_label

FilingKind = Literal[
    "8k", "stake", "form4", "form4a", "revenue", "split", "dilution", "catalyst", "ratified"
]
# the order of kinds within a day (the page's words: 8-K, 13D/G, Form 4, 4/A, revenue, split, dilution,
# catalyst, ratified fact)
KIND_ORDER: tuple[FilingKind, ...] = (
    "8k",
    "stake",
    "form4",
    "form4a",
    "revenue",
    "split",
    "dilution",
    "catalyst",
    "ratified",
)

# The Form 4 transaction codes the page labels; any other code shows as the code itself.
FORM4_CODE_LABELS: dict[str, str] = {
    "P": "purchase",
    "S": "sale",
    "A": "grant/award",
    "M": "option exercise",
    "F": "tax withholding",
    "G": "gift",
}
_CODE_ORDER = tuple(FORM4_CODE_LABELS)  # P, S, A, M, F, G, then any other code A→Z

TAG_ISSUER_SELF = "issuer self-filing"
TAG_INTERNAL_TRANSFER = "internal transfer"
TAG_10B5_1 = "10b5-1 plan"
TAG_ITEMS_PENDING = "items not yet resolved"
TAG_RE_REPORTED = "re-reported"
TAG_NEEDS_REVIEW = "needs review"
TAG_FILER_UNRESOLVED = "filer identity unresolved"

# how far (days) from "one year earlier" a prior-year period end may sit and still be the same fiscal period
# (a 52/53-week filer's period end drifts a few days from year to year)
YOY_TOLERANCE_DAYS = 10


@dataclass(frozen=True)
class FilingRow:
    """One filing-derived fact for one company, in the shape every filing page shares.

    ``form`` = the form as filed ("8-K", "4/A", "SC 13D", "10-Q") or the row's own label ("split", "catalyst",
    "ratified fact"). ``date`` = the PUBLIC date the row counts from (the day it sorts under). ``headline`` = one
    neutral line; ``detail`` = the lines the expanded row shows. ``url`` = the filing's EDGAR index (or the
    stored source), ``None`` when none is known. ``routine`` = a fold candidate (a Form 4 holding no code-P
    purchase). ``usd`` = the row's dollar size when it has one (a Form 4's purchases minus sales, a dilution's
    principal)."""

    security_id: UUID
    ticker: str
    kind: FilingKind
    form: str
    date: date
    headline: str
    detail: tuple[str, ...] = ()
    url: str | None = None
    accession: str | None = None
    tags: tuple[str, ...] = ()
    routine: bool = False
    usd: float | None = None


class Who(NamedTuple):
    """The company a batch of rows belongs to: its id, the ticker the page shows, its SEC CIK (the EDGAR URL
    builder's, ``None`` = unknown) and its master name (the issuer-self rule's fallback)."""

    security_id: UUID
    ticker: str
    cik: str | None = None
    name: str | None = None


# --- numbers, words, dates ----------------------------------------------------------------------------------


def _f(v: Any) -> float | None:
    """A stored number (Decimal / int / float) as a float; ``None``, a non-number and a non-finite value (a
    Postgres ``numeric`` can hold NaN) stay ``None``: a column the row does not state is left out of the words,
    never filled in, and never fed to arithmetic that would raise."""
    if v is None or isinstance(v, bool):
        return None
    try:
        out = float(v)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def fmt_shares(v: float) -> str:
    return f"{v:,.0f}" if abs(v - round(v)) < 1e-9 else f"{v:,.2f}"


def fmt_price(v: float) -> str:
    return f"${v:,.2f}" if abs(v) >= 1 or v == 0 else f"${v:.4f}"


def fmt_usd(v: float) -> str:
    """Whole dollars ("$49,200", "-$15,000")."""
    return f"-${abs(v):,.0f}" if v < 0 else f"${v:,.0f}"


# the units of a compact figure: (suffix, size, decimals shown)
_UNITS: tuple[tuple[str, float, int], ...] = (
    ("", 1.0, 0),
    ("K", 1e3, 1),
    ("M", 1e6, 1),
    ("B", 1e9, 1),
)


def fmt_compact(v: float, *, dollars: bool = True) -> str:
    """A big number in one figure ("$41.2M", "84.2M", "$845.0K"). THE ROLLOVER RULE: the figure is rounded to what
    the page shows (a whole number below a thousand, one decimal above), and one that rounds up to 1,000 moves
    to the next unit, so 999,949 reads "$999.9K" and 999,950 reads "$1.0M", never "$1000.0K"."""
    sign = "-" if v < 0 else ""
    a = abs(v)
    i = max(k for k, (_, size, _) in enumerate(_UNITS) if a >= size or k == 0)
    while i < len(_UNITS) - 1 and round(a / _UNITS[i][1], _UNITS[i][2]) >= 1000:
        i += 1
    suffix, size, decimals = _UNITS[i]
    return f"{sign}{'$' if dollars else ''}{a / size:,.{decimals}f}{suffix}"


def _instant(v: datetime) -> datetime:
    """An instant as an aware UTC datetime (a naive one is read as UTC), so instants can be compared and
    ordered whatever mix a filing's rows hold."""
    return v.replace(tzinfo=timezone.utc) if v.tzinfo is None else v.astimezone(timezone.utc)


def et_date(instant: datetime) -> date:
    """The market-time (ET) calendar date of an instant; a naive datetime is read as UTC."""
    return _instant(instant).astimezone(market_tz()).date()


def _day(v: Any) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    return v if isinstance(v, date) else None


def _plural(n: int, one: str, many: str) -> str:
    return f"{n:,} {one if n == 1 else many}"


# --- the source URL -----------------------------------------------------------------------------------------

_ACCESSION = re.compile(r"\d{18}")
_HTTP = re.compile(r"https?://\S+", re.I)


def filing_index_url(cik: str | None, accession: str | None) -> str | None:
    """The EDGAR filing-index URL ``https://www.sec.gov/Archives/edgar/data/<cik as int>/<accession without
    dashes>/``, or ``None`` when the CIK or the accession is missing or malformed (never a guessed link).
    """
    if not cik or not accession:
        return None
    digits = re.sub(r"\D", "", str(cik))
    acc = str(accession).strip().replace("-", "")
    if not digits or not _ACCESSION.fullmatch(acc):
        return None
    return f"https://www.sec.gov/Archives/edgar/data/{int(digits)}/{acc}/"


def source_url(source_ref: Any, cik: str | None) -> str | None:
    """A stored ``source_ref`` as a link: an http(s) URL as is, an accession-shaped reference through the EDGAR
    index, anything else (an award id, a free-text reference) no link."""
    if not isinstance(source_ref, str):
        return None
    ref = source_ref.strip()
    if _HTTP.fullmatch(ref):
        return ref
    return filing_index_url(cik, ref)


def _accession_of(source_ref: Any) -> str | None:
    """The accession a ``source_ref`` names when it is accession-shaped, else ``None``."""
    if not isinstance(source_ref, str):
        return None
    ref = source_ref.strip()
    return ref if _ACCESSION.fullmatch(ref.replace("-", "")) else None


# --- 8-K ----------------------------------------------------------------------------------------------------

# ``item_label`` answers "corporate event" for a code it has no label for; that fallback is what marks an item as
# unlabeled here (computed from an impossible code, so a reworded fallback cannot desync this)
_UNLABELED = item_label("")

# The standard 8-K items with the SEC's own short titles (Form 8-K, sections 1-9), neutral lowercase. DISPLAY ONLY,
# like ``signals.corporate_events.ITEM_LABELS`` (which this page cannot edit and which only knows seven items):
# nothing here fires, grades or scores anything. ``item_label``'s own wording wins for the codes it knows (1.01, 1.03,
# 3.01, 3.02, 4.01, 4.02, 5.02), this table fills the rest, and a code in neither (an item the SEC adds later, a
# mistyped one) still shows as its bare code, never a guessed label.
STANDARD_8K_ITEMS: dict[str, str] = {
    "1.01": "entry into a material definitive agreement",
    "1.02": "termination of a material definitive agreement",
    "1.03": "bankruptcy or receivership",
    "1.04": "mine safety: shutdowns and patterns of violations",
    "1.05": "material cybersecurity incidents",
    "2.01": "completion of acquisition or disposition of assets",
    "2.02": "results of operations and financial condition",
    "2.03": "creation of a direct financial obligation",
    "2.04": "triggering events that accelerate a financial obligation",
    "2.05": "costs of exit or disposal activities",
    "2.06": "material impairments",
    "3.01": "notice of delisting or failure to satisfy a listing rule",
    "3.02": "unregistered sales of equity securities",
    "3.03": "material modification to rights of security holders",
    "4.01": "change in certifying accountant",
    "4.02": "non-reliance on previously issued financial statements",
    "5.01": "change in control of registrant",
    "5.02": "departure or appointment of directors or certain officers",
    "5.03": "amendments to articles or bylaws; change in fiscal year",
    "5.04": "temporary suspension of trading under employee benefit plans",
    "5.05": "amendment to the code of ethics, or a waiver of it",
    "5.06": "change in shell company status",
    "5.07": "submission of matters to a vote of security holders",
    "5.08": "shareholder director nominations",
    "6.01": "ABS informational and computational material",
    "6.02": "change of servicer or trustee",
    "6.03": "change in credit enhancement or other external support",
    "6.04": "failure to make a required distribution",
    "6.05": "securities act updating disclosure",
    "7.01": "Regulation FD disclosure",
    "8.01": "other events",
    "9.01": "financial statements and exhibits",
}


def item_words(code: str) -> str:
    """ "5.02 officer/director departure or appointment": the code with its label, ``item_label``'s own wording
    first, then the standard SEC item table above; an item in neither shows as its bare code."""
    label = item_label(code)
    if label == _UNLABELED:
        label = STANDARD_8K_ITEMS.get(code, "")
    return f"{code} {label}" if label else code


def rows_from_corporate_events(facts: Iterable[Mapping[str, Any]], who: Who) -> list[FilingRow]:
    """One row per 8-K / 8-K/A filing: the items with their labels, ``NULL`` items tagged "items not yet
    resolved" (the filing is stored, its item codes are not parsed yet)."""
    out: list[FilingRow] = []
    for f in facts:
        filed = _day(f.get("filed")) or _day(f.get("valid_from"))
        if filed is None:
            continue
        form = str(f.get("form") or "8-K")
        raw = f.get("items")
        items = [str(i) for i in raw] if raw is not None else None
        tags: tuple[str, ...] = ()
        if items is None:
            headline, detail, tags = f"{form} · items not yet resolved", (), (TAG_ITEMS_PENDING,)
        elif not items:
            headline, detail = f"{form} · no item codes stated", ()
        else:
            headline = f"{form} · " + " · ".join(item_words(i) for i in items)
            detail = tuple(f"Item {item_words(i)}" for i in items)
        accession = f.get("accession")
        out.append(
            FilingRow(
                security_id=who.security_id,
                ticker=who.ticker,
                kind="8k",
                form=form,
                date=filed,
                headline=headline,
                detail=detail,
                url=source_url(f.get("source_ref"), who.cik)
                or filing_index_url(who.cik, accession),
                accession=str(accession) if accession else None,
                tags=tags,
            )
        )
    return out


# --- Form 4 -------------------------------------------------------------------------------------------------


def _norm_entity(name: str | None) -> str:
    """casefold + collapse whitespace + drop a trailing period; deliberately NO corporate-suffix stripping (a
    self-filing has the SAME name on both sides, and stripping would widen the match)."""
    if not name:
        return ""
    return " ".join(name.strip().casefold().rstrip(".").split())


def is_issuer_self(txn: Mapping[str, Any], issuer_name: str | None) -> bool:
    """Did the ISSUER file this Form 4 on ITSELF (reporting owner == issuer)? The reader's OWN copy of a rule two
    other modules each hold privately (``signals/insider_conviction._is_issuer_self`` and
    ``signals/display/insider_flow._is_issuer_self``, the seam's duplicate-with-pointer pattern: this module
    cannot import ``signals/``; re-sync by hand if they change). CIK equality (leading zeros ignored) is
    canonical; the filer name against the issuer's name (the row's own ``issuer_name``, else the master's)
    covers rows stored before the CIKs were captured. A missing CIK or name only ever KEEPS the row untagged.
    """
    oc, ic = txn.get("rpt_owner_cik"), txn.get("issuer_cik")
    if oc and ic and str(oc).strip().lstrip("0") == str(ic).strip().lstrip("0"):
        return True
    filer = _norm_entity(txn.get("insider_name"))
    issuer = _norm_entity(txn.get("issuer_name") or issuer_name)
    return bool(filer) and filer == issuer


def _text_list(rows: Sequence[Mapping[str, Any]], key: str) -> tuple[bool, list[str]]:
    """``(captured, distinct texts)`` for a text-array column across a filing's rows: ``captured`` is False when
    every row holds ``NULL`` (not captured: shown as nothing, never as "none"); an empty list on a row is
    "captured, none stated"."""
    captured, out = False, []
    for r in rows:
        v = r.get(key)
        if v is None:
            continue
        captured = True
        for t in [v] if isinstance(v, str) else v:
            t = " ".join(str(t).split())
            if t and t not in out:
                out.append(t)
    return captured, out


def _code_key(code: str) -> tuple[int, str]:
    return (_CODE_ORDER.index(code), "") if code in _CODE_ORDER else (len(_CODE_ORDER), code)


def _txn_line(r: Mapping[str, Any]) -> str:
    code = str(r.get("txn_code") or "?")
    parts = [str(_day(r.get("valid_from")) or "undated"), code]
    sh, px, usd = _f(r.get("shares")), _f(r.get("price")), _f(r.get("usd"))
    if sh is not None:
        parts.append(f"{fmt_shares(sh)} sh" + (f" @ {fmt_price(px)}" if px is not None else ""))
    if usd is not None:
        parts.append(fmt_usd(usd))
    di = r.get("direct_indirect")
    if di in ("D", "I"):
        parts.append("direct" if di == "D" else "indirect")
    after = _f(r.get("shares_owned_after"))
    if after is not None:
        parts.append(f"{fmt_shares(after)} sh owned after")
    if is_internal_transfer(r):
        with_ = ", ".join(transfer_with(r))
        parts.append("internal transfer" + (f" with {with_}" if with_ else ""))
    return " · ".join(parts)


def _code_summary(code: str, rows: Sequence[Mapping[str, Any]]) -> tuple[str, float | None]:
    """ "P purchase 12,000 sh @ $4.10 · $49,200" for one code's rows, and its dollar total (``None`` when no row
    states dollars, or shares and a price to make them from: a total is never filled in). The price is the
    shares-weighted mean over the rows that state both."""
    label = FORM4_CODE_LABELS.get(code)
    head = f"{code} {label}" if label else code
    shares = [(_f(r.get("shares")), _f(r.get("price")), _f(r.get("usd"))) for r in rows]
    total_sh = sum(s for s, _, _ in shares if s is not None)
    priced = [(s, p) for s, p, _ in shares if s is not None and p is not None]
    # The mean price is weighted by shares, so it needs a positive weight: a real Form 4 row can state a price
    # against a share count of zero (the stream's first 500 on dev). With no weight there is no mean to state,
    # and a column the rows do not state is left out of the phrase, never filled in.
    weight = sum(s for s, _ in priced if s > 0)
    mean_px = sum(s * p for s, p in priced if s > 0) / weight if weight > 0 else None
    stated = [u for _, _, u in shares if u is not None]
    total_usd = sum(stated) if stated else (sum(s * p for s, p in priced) if priced else None)
    # "P purchase 12,000 sh @ $4.10 · $49,200": the code and its shares read as one phrase, the dollars after
    if any(s is not None for s, _, _ in shares):
        head += f" {fmt_shares(total_sh)} sh" + (
            f" @ {fmt_price(mean_px)}" if mean_px is not None else ""
        )
    return (f"{head} · {fmt_usd(total_usd)}" if total_usd else head), total_usd


def _form4_group(accession: str, rows: Sequence[Mapping[str, Any]], who: Who) -> FilingRow | None:
    first = rows[0]
    owner = str(first.get("insider_name") or "reporting owner not named")
    names = list(dict.fromkeys(str(r.get("insider_name")) for r in rows if r.get("insider_name")))
    role = first.get("insider_role")
    filer = f"{owner} ({role})" if role else owner
    if len(names) > 1:
        filer += f" and {len(names) - 1} more"
    by_code: dict[str, list[Mapping[str, Any]]] = {}
    for r in rows:
        by_code.setdefault(str(r.get("txn_code") or "?"), []).append(r)
    summaries, net = [], 0.0
    has_net = False
    for code in sorted(by_code, key=_code_key):
        text, total = _code_summary(code, by_code[code])
        summaries.append(text)
        if total is not None and code in ("P", "S"):
            net, has_net = net + (total if code == "P" else -total), True

    detail = [_txn_line(r) for r in rows]
    joint = list(
        dict.fromkeys(n for r in rows for n in (r.get(JOINT_REPORTERS) or ()) if n and n != owner)
    )
    if joint:
        detail.append("Also reported by: " + ", ".join(joint))
    natures = list(
        dict.fromkeys(
            " ".join(str(r["ownership_nature"]).split())
            for r in rows
            if r.get("ownership_nature") and str(r["ownership_nature"]).strip()
        )
    )
    if natures:
        detail.append("Ownership nature: " + "; ".join(natures))
    captured, notes = _text_list(rows, "txn_footnotes")
    if notes:
        detail += [f"Footnote: {t}" for t in notes]
    elif captured:
        detail.append("Footnotes: none stated")
    captured, held = _text_list(rows, "filing_natures")
    if held:
        detail.append("Indirect holdings the filing states: " + "; ".join(held))
    elif captured:
        detail.append("Indirect holdings the filing states: none stated")

    tags = []
    if any(is_issuer_self(r, who.name) for r in rows):
        tags.append(TAG_ISSUER_SELF)
    if any(is_internal_transfer(r) for r in rows):
        tags.append(TAG_INTERNAL_TRANSFER)
    if any(r.get("aff_10b5_1") is True for r in rows):
        tags.append(TAG_10B5_1)

    # instants are read as aware UTC before they are ordered, so a mix of aware and naive stamps cannot raise
    accepted = [_instant(a) for a in (r.get("accepted") for r in rows) if isinstance(a, datetime)]
    held_since = [
        _instant(r["recorded_at"]) for r in rows if isinstance(r.get("recorded_at"), datetime)
    ]
    # the public date: SEC acceptance (market time); a filing with none counts from the instant we first held it
    # (the public clock's own never-early rule for it)
    if accepted:
        dated = et_date(min(accepted))
    elif held_since:
        dated = et_date(min(held_since))
    else:
        return None  # cannot be dated on any clock (impossible from the point-in-time view)

    amends = next((r.get(AMENDS_ACCESSION) for r in rows if r.get(AMENDS_ACCESSION)), None)
    if amends:
        detail.insert(0, f"Rows the system took from this 4/A, which amends accession {amends}")
        tags.append("applied 4/A")
    return FilingRow(
        security_id=who.security_id,
        ticker=who.ticker,
        kind="form4a" if amends else "form4",
        form="4/A" if amends else "4",
        date=dated,
        headline=f"{filer}: " + "; ".join(summaries),
        detail=tuple(detail),
        url=filing_index_url(who.cik, accession),
        accession=accession,
        tags=tuple(tags),
        routine=amends is None and "P" not in by_code,
        usd=net if has_net else None,
    )


def rows_from_insider_txns(rows: Iterable[Mapping[str, Any]], who: Who) -> list[FilingRow]:
    """One row per Form 4 filing: the stored transaction rows GROUPED BY ACCESSION, the transactions summed by
    code. A group of an applied 4/A's emitted rows (``amends_accession``) is a ``form4a`` row; ``merge_4a``
    folds it into the 4/A's own record row. A group that cannot be dated at all (no acceptance and no recorded
    instant: impossible from the point-in-time view) is left out."""
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for r in rows:
        groups.setdefault(str(r.get("accession") or ""), []).append(r)
    out = []
    for accession, group in groups.items():
        row = _form4_group(accession, group, who) if accession else None
        if row is not None:
            out.append(row)
    return out


# --- Form 4/A -----------------------------------------------------------------------------------------------


def rows_from_amendments(rows: Iterable[Mapping[str, Any]], who: Who) -> list[FilingRow]:
    """One row per Form 4/A FILING (``insider_amendment``), shown as filed: who filed it, which Form 4 it says it
    amends, the filer's remarks. Never resolved against its original here; its standing (applied, kept,
    pending) lives on the Admin "Insider amendments" page. ``day`` (else ``filed``) is its public date.
    """
    out = []
    for r in rows:
        day = _day(r.get("day")) or _day(r.get("filed"))
        if day is None:
            continue
        owners = [
            str(o["name"])
            for o in (r.get("owners") or ())
            if isinstance(o, Mapping) and o.get("name")
        ]
        original = _day(r.get("date_of_original_submission"))
        amends = f"amends the Form 4 filed {original}" if original else "amends an earlier Form 4"
        by = f" by {', '.join(owners)}" if owners else ""
        n = len(r.get("rows") or ())
        detail = []
        if r.get("remarks") and str(r["remarks"]).strip():
            detail.append("Filer's remarks: " + " ".join(str(r["remarks"]).split()))
        detail.append(f"{_plural(n, 'transaction row', 'transaction rows')} as filed")
        accession = r.get("accession")
        out.append(
            FilingRow(
                security_id=who.security_id,
                ticker=who.ticker,
                kind="form4a",
                form="4/A",
                date=day,
                headline=f"4/A{by} {amends}",
                detail=tuple(detail),
                url=filing_index_url(who.cik or r.get("issuer_cik"), accession),
                accession=str(accession) if accession else None,
            )
        )
    return out


def merge_4a(
    form4a_from_tape: Sequence[FilingRow], records: Sequence[FilingRow]
) -> list[FilingRow]:
    """One row per 4/A accession. The record row (the filing as filed) leads; the rows an applied 4/A put on the
    Form 4 tape fold into its detail and set its dollar size. A tape group with no record row in view (a 4/A
    whose record sits outside the gate or the window) stays a row of its own: a filing is never dropped.
    """
    tape = {r.accession: r for r in form4a_from_tape if r.accession}
    out: list[FilingRow] = []
    for rec in records:
        got = tape.pop(rec.accession, None) if rec.accession else None
        if got is None:
            out.append(rec)
            continue
        out.append(
            replace(
                rec,
                detail=rec.detail + got.detail,
                tags=tuple(dict.fromkeys(rec.tags + got.tags)),
                usd=got.usd,
            )
        )
    out.extend(tape.values())
    out.extend(r for r in form4a_from_tape if not r.accession)
    return out


# --- 13D / 13G ----------------------------------------------------------------------------------------------


def rows_from_stakes(facts: Iterable[Mapping[str, Any]], who: Who) -> list[FilingRow]:
    """One row per 13D/G filing about the company (it is the SUBJECT): "SC 13D · <filer> · 7.2%". A filing whose
    filer identity is not resolved says so in a tag."""
    out = []
    for f in facts:
        filed = _day(f.get("filed")) or _day(f.get("valid_from"))
        if filed is None:
            continue
        form = str(f.get("form") or "13D/G")
        filer = f.get("filer_name")
        pct = _f(f.get("pct_owned"))
        parts = [form, str(filer) if filer else "filer not named"]
        if pct is not None:
            parts.append(f"{pct:.1f}%")
        accession = f.get("accession")
        detail = []
        if f.get("filer_cik"):
            detail.append(f"Filer CIK {f['filer_cik']}")
        out.append(
            FilingRow(
                security_id=who.security_id,
                ticker=who.ticker,
                kind="stake",
                form=form,
                date=filed,
                headline=" · ".join(parts),
                detail=tuple(detail),
                url=source_url(f.get("source_ref"), who.cik)
                or filing_index_url(who.cik, accession),
                accession=str(accession) if accession else None,
                tags=() if filer else (TAG_FILER_UNRESOLVED,),
            )
        )
    return out


# --- revenue ------------------------------------------------------------------------------------------------


def revenue_form(fiscal_period: Any) -> str:
    """The form a revenue print came from: a fiscal Q4 / FY row is the 10-K, a Q1-Q3 row the 10-Q (the stored
    series is quarterly, Q4 derived from the annual); a row with no fiscal period is "10-Q / 10-K".
    """
    fp = str(fiscal_period or "").upper()
    if fp in ("Q4", "FY"):
        return "10-K"
    return "10-Q" if fp else "10-Q / 10-K"


def _year_earlier(d: date) -> date:
    try:
        return d.replace(year=d.year - 1)
    except ValueError:  # Feb 29
        return d.replace(year=d.year - 1, day=28)


def prior_year_row(
    row: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]
) -> Mapping[str, Any] | None:
    """The row for the same fiscal period a year earlier, from the SAME as-of read: the one whose period end is
    nearest one year before this row's, within ``YOY_TOLERANCE_DAYS`` (a 52/53-week filer drifts a few days).
    """
    end = _day(row.get("period_end"))
    if end is None:
        return None
    target = _year_earlier(end)
    best: tuple[int, Mapping[str, Any]] | None = None
    for r in rows:
        pe = _day(r.get("period_end"))
        if pe is None or r is row:
            continue
        gap = abs((pe - target).days)
        if gap <= YOY_TOLERANCE_DAYS and (best is None or gap < best[0]):
            best = (gap, r)
    return best[1] if best else None


def _yoy_words(row: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> str | None:
    prior = prior_year_row(row, rows)
    now, then = _f(row.get("value")), _f(prior.get("value")) if prior else None
    if now is None or then is None or then <= 0:
        return None
    pct = (now / then - 1.0) * 100.0
    return f"{pct:+.0f}% YoY" if abs(pct) >= 10 else f"{pct:+.1f}% YoY"


def rows_from_revenue(facts: Iterable[Mapping[str, Any]], who: Who) -> list[FilingRow]:
    """One row per filing that carries a revenue print (``metric_key == 'revenue'``), GROUPED BY ACCESSION: the
    headline is the latest period the filing states, with its year-over-year change when the same fiscal period
    a year earlier is in the same as-of read. A row whose first-knowable date is before the carrying filing's is
    a re-report (a later filing restating a period): tagged, with the first-knowable date in the detail.
    """
    rev = [f for f in facts if f.get("metric_key") == "revenue"]
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for f in rev:
        groups.setdefault(str(f.get("accession") or ""), []).append(f)
    out = []
    for accession, group in groups.items():
        group.sort(
            key=lambda r: (_day(r.get("period_end")) or date.min, r.get("basis") == "native")
        )
        main = group[-1]
        filed = _day(main.get("valid_from"))
        end, value = _day(main.get("period_end")), _f(main.get("value"))
        if filed is None or end is None or value is None:
            continue
        yoy = _yoy_words(main, rev)
        head = f"revenue, period ending {end}: {fmt_compact(value)} ({main.get('basis') or 'basis not stated'})"
        detail = []
        for g in reversed(group[:-1]):
            gv = _f(g.get("value"))
            if gv is not None and _day(g.get("period_end")):
                detail.append(
                    f"Also states period ending {_day(g.get('period_end'))}: {fmt_compact(gv)} "
                    f"({g.get('basis') or 'basis not stated'})"
                )
        tags = []
        knowable = _day(main.get("knowable_from"))
        if knowable is not None and knowable < filed:
            tags.append(TAG_RE_REPORTED)
            first = main.get("knowable_accession")
            detail.append(
                f"First knowable {knowable}" + (f" in accession {first}" if first else "")
            )
        out.append(
            FilingRow(
                security_id=who.security_id,
                ticker=who.ticker,
                kind="revenue",
                form=revenue_form(main.get("fiscal_period")),
                date=filed,
                headline=head + (f" · {yoy}" if yoy else ""),
                detail=tuple(detail),
                url=filing_index_url(who.cik, accession),
                accession=accession or None,
                tags=tuple(tags),
            )
        )
    return out


# --- splits -------------------------------------------------------------------------------------------------


def split_public_date(row: Mapping[str, Any], clock: Literal["record", "public"]) -> date | None:
    """The split's own publication as the view knows it. On the public clock the row's ``recorded_at`` IS its
    public instant (``db.public_clock``: the start of the market day of the earliest filing or session any of its
    versions carries); on the live record clock ``recorded_at`` is only when we stored it, so the earlier of the
    row's own ``valid_from`` (its first SEC filing, else its first session) and its effective date stands.
    """
    if clock == "public" and isinstance(row.get("recorded_at"), datetime):
        return et_date(row["recorded_at"])
    days = [d for d in (_day(row.get("valid_from")), _day(row.get("effective_date"))) if d]
    return min(days) if days else None


def rows_from_splits(
    rows: Iterable[Mapping[str, Any]], who: Who, *, clock: Literal["record", "public"]
) -> list[FilingRow]:
    """One row per split on the list (every status, as stored): the ratio, the effective date, the sources."""
    out = []
    for r in rows:
        published = split_public_date(r, clock)
        rf, rt = r.get("ratio_from"), r.get("ratio_to")
        if published is None or rf is None or rt is None:
            continue
        status = str(r.get("status") or "unknown")
        eff = _day(r.get("effective_date"))
        detail = [
            f"Status as stored: {status}"
            + (f" ({r['confirmed_via']})" if r.get("confirmed_via") else "")
        ]
        if r.get("sec_accession"):
            ratio = (
                f", {ratio_label(r['sec_ratio_from'], r['sec_ratio_to'])}"
                if r.get("sec_ratio_from") is not None and r.get("sec_ratio_to") is not None
                else ""
            )
            detail.append(
                f"SEC {r.get('sec_form') or 'filing'} {r['sec_accession']}"
                + (f" filed {_day(r.get('sec_filed'))}" if r.get("sec_filed") else "")
                + ratio
            )
        if r.get("polygon_split_from") is not None and r.get("polygon_split_to") is not None:
            detail.append(
                f"Massive.com: {ratio_label(r['polygon_split_from'], r['polygon_split_to'])}"
                + (
                    f", first session {_day(r.get('polygon_execution_date'))}"
                    if r.get("polygon_execution_date")
                    else ""
                )
            )
        tags = {
            "needs_review": (TAG_NEEDS_REVIEW,),
            "rejected": ("rejected",),
            "revoked": ("revoked",),
        }
        out.append(
            FilingRow(
                security_id=who.security_id,
                ticker=who.ticker,
                kind="split",
                form="split",
                date=published,
                headline=f"{ratio_label(rf, rt)}" + (f", effective {eff}" if eff else ""),
                detail=tuple(detail),
                url=r.get("sec_url") if _HTTP.fullmatch(str(r.get("sec_url") or "")) else None,
                accession=str(r["sec_accession"]) if r.get("sec_accession") else None,
                tags=tags.get(status, ()),
            )
        )
    return out


# --- dilution -----------------------------------------------------------------------------------------------


def convert_overhang_pct(fact: Mapping[str, Any]) -> float | None:
    """The as-converted share count as a percent of the row's stored ``shares_outstanding`` basis:
    ``principal ÷ $1,000 × conversion rate`` over ``shares_outstanding``. ``None`` unless the principal, the
    conversion rate and a positive share basis are all stored (the same measure the dilution signal's overhang
    reads, here from this one offering)."""
    terms = fact.get("terms")
    rate = _f(terms.get("conversion_rate")) if isinstance(terms, Mapping) else None
    principal, basis = _f(fact.get("principal_total_usd")), _f(fact.get("shares_outstanding"))
    if not rate or not principal or not basis or basis <= 0:
        return None
    return 100.0 * (principal / 1000.0 * rate) / basis


def rows_from_dilution(facts: Iterable[Mapping[str, Any]], who: Who) -> list[FilingRow]:
    """One row per dilution fact (a convertible-note issuance): the kind, the principal, the overhang as a
    percent of the stored shares-outstanding basis when both are present."""
    out = []
    for f in facts:
        day = _day(f.get("valid_from"))
        if day is None:
            continue
        kind = str(f.get("instrument_kind") or "dilution").replace("_", " ")
        principal = _f(f.get("principal_total_usd"))
        pct = convert_overhang_pct(f)
        head = kind + (f": {fmt_compact(principal)} principal" if principal else "")
        if pct is not None:
            head += f" · {pct:.1f}% of shares outstanding as converted"
        terms = f.get("terms") if isinstance(f.get("terms"), Mapping) else {}
        detail = []
        coupon, price = _f(terms.get("coupon_pct")), _f(terms.get("conversion_price_usd"))
        bits = []
        if coupon is not None:
            bits.append(f"coupon {coupon:g}%")
        if terms.get("maturity_date"):
            bits.append(f"matures {terms['maturity_date']}")
        if price is not None:
            bits.append(f"conversion price {fmt_price(price)}")
        if bits:
            detail.append(" · ".join(bits))
        basis = _f(f.get("shares_outstanding"))
        if basis:
            ref = f" ({f['shares_outstanding_ref']})" if f.get("shares_outstanding_ref") else ""
            detail.append(
                f"Shares-outstanding basis stored with the offering: {fmt_shares(basis)}{ref}"
            )
        if pct is not None:
            detail.append(
                "As-converted shares = principal ÷ $1,000 × the conversion rate; the percent is of that basis"
            )
        accession = f.get("accession")
        out.append(
            FilingRow(
                security_id=who.security_id,
                ticker=who.ticker,
                kind="dilution",
                form="dilution",
                date=day,
                headline=head,
                detail=tuple(detail),
                url=filing_index_url(who.cik, accession),
                accession=str(accession) if accession else None,
                usd=principal,
            )
        )
    return out


# --- ratified catalysts and facts ---------------------------------------------------------------------------


def _source_line(f: Mapping[str, Any]) -> str:
    """ "Source: ratified, ratified by operator": only the parts the row states."""
    parts = [str(f["source"])] if f.get("source") else []
    if f.get("ratified_by"):
        parts.append(f"ratified by {f['ratified_by']}")
    return "Source: " + (", ".join(parts) if parts else "not stated")


def rows_from_catalysts(facts: Iterable[Mapping[str, Any]], who: Who) -> list[FilingRow]:
    """One row per ratified catalyst, dated by its event date (``valid_from``; a future-dated one appears once
    its day passes, the as-of read sees to that). The card-level catalysts are the Catalyst Calendar's.
    """
    out = []
    for f in facts:
        day = _day(f.get("valid_from"))
        if day is None:
            continue
        ctype = str(f.get("catalyst_type") or "catalyst").replace("_", " ")
        detail = [f"Type: {ctype}"]
        if f.get("grade"):
            detail.append(f"Grade: {f['grade']}")
        horizon = _day(f.get("horizon_end"))
        if horizon:
            detail.append(f"Signal horizon ends {horizon}")
        detail.append(_source_line(f))
        out.append(
            FilingRow(
                security_id=who.security_id,
                ticker=who.ticker,
                kind="catalyst",
                form="catalyst",
                date=day,
                headline=f"{ctype}: {f.get('label') or 'no label stated'}",
                detail=tuple(detail),
                url=source_url(f.get("source_ref"), who.cik),
                accession=_accession_of(f.get("source_ref")),
            )
        )
    return out


def _ratified_row(f: Mapping[str, Any], who: Who, headline: str) -> FilingRow | None:
    """One operator-ratified fact. Its public date is the instant the operator ratified it (``recorded_at``, the
    public clock's own instant for these tables); the period or cover date it states rides in the detail.
    """
    stamped = f.get("recorded_at")
    if not isinstance(stamped, datetime):
        return None
    detail = []
    if _day(f.get("valid_from")):
        detail.append(f"Period or cover date: {_day(f.get('valid_from'))}")
    detail.append(_source_line(f))
    return FilingRow(
        security_id=who.security_id,
        ticker=who.ticker,
        kind="ratified",
        form="ratified fact",
        date=et_date(stamped),
        headline=headline,
        detail=tuple(detail),
        url=source_url(f.get("source_ref"), who.cik),
        accession=_accession_of(f.get("source_ref")),
    )


def rows_from_ratified(
    cash_burn: Iterable[Mapping[str, Any]],
    shares: Iterable[Mapping[str, Any]],
    mix: Iterable[Mapping[str, Any]],
    who: Who,
) -> list[FilingRow]:
    """The operator's own ratifications, one row each: cash and burn, shares outstanding, revenue mix."""
    out = []
    for f in cash_burn:
        cash, burn = _f(f.get("cash_usd")), _f(f.get("quarterly_burn_usd"))
        if cash is None or burn is None:
            continue
        tail = f"burn {fmt_compact(burn)}/qtr" if burn > 0 else "cash-positive quarter"
        out.append(_ratified_row(f, who, f"cash {fmt_compact(cash)} · {tail}"))
    for f in shares:
        n = _f(f.get("shares"))
        if n is not None:
            out.append(_ratified_row(f, who, f"shares outstanding {fmt_compact(n, dollars=False)}"))
    for f in mix:
        pct = _f(f.get("mix_pct"))
        if pct is not None:
            out.append(_ratified_row(f, who, f"revenue mix: {f.get('segment_label')} {pct:g}%"))
    return [r for r in out if r is not None]


# --- the read -----------------------------------------------------------------------------------------------


def read_amendments(
    conn: psycopg.Connection,
    sids: Sequence[UUID],
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
    *,
    start: date,
    tenant_id: UUID = DEFAULT_TENANT_ID,
) -> dict[UUID, list[dict[str, Any]]]:
    """The Form 4/A filings of ``sids`` as the view reads them: the newest stored version of each accession,
    SELECT only, scoped to the tenant. THE TWO-CLOCK GATE (invariant #1): on BOTH clocks the filing must be dated
    on/before the as-of (``filed``; a 4/A counts from its own filing, never earlier); on the RECORD clock (the
    live read) the version must also have been recorded by ``known_at``; the PUBLIC clock never reads
    ``recorded_at`` (operator decision 2026-10-21). A 4/A with no ``filed`` is dated by its acceptance in market
    time, and by the instant we first held it only when neither is stored (the public clock's own order for a
    4/A, ``db.public_clock``): never early, never dropped. Rows dated before ``start`` are left out.
    """
    ids = list(dict.fromkeys(sids))
    if not ids:
        return {}
    day = sql.SQL(
        "COALESCE(filed, (accepted AT TIME ZONE %(tz)s)::date, (recorded_at AT TIME ZONE %(tz)s)::date)"
    )
    params: dict[str, Any] = {
        "t": tenant_id,
        "ids": ids,
        "asof": asof,
        "start": start,
        "tz": market_tz().key,
    }
    record_gate = sql.SQL("")
    if clock == "record":
        params["known"] = known_at or db_now(conn)
        record_gate = sql.SQL(" AND recorded_at <= %(known)s")
    query = sql.SQL(
        "SELECT DISTINCT ON (security_id, accession) security_id, accession, issuer_cik, owners, "
        "date_of_original_submission, remarks, rows, filed, accepted, recorded_at, {day} AS day "
        "FROM insider_amendment WHERE tenant_id = %(t)s AND security_id = ANY(%(ids)s) "
        "AND {day} BETWEEN %(start)s AND %(asof)s{gate} "
        "ORDER BY security_id, accession, recorded_at DESC, id DESC"
    ).format(day=day, gate=record_gate)
    out: dict[UUID, list[dict[str, Any]]] = {}
    with conn.cursor() as cur:
        cur.execute(query, params)
        for r in cur.fetchall():
            out.setdefault(r["security_id"], []).append(dict(r))
    return out


def rows_for_company(
    pit: Any,
    who: Who,
    *,
    clock: Literal["record", "public"],
    amendments: Sequence[Mapping[str, Any]] = (),
) -> list[FilingRow]:
    """Every source's rows for one company, through the point-in-time view ``pit`` (the accessors
    ``PointInTimeData`` serves), the 4/A records folded in. The window is the caller's."""
    sid = who.security_id
    revenue = pit.fundamentals_facts(sid)
    tape_4a: list[FilingRow] = []
    form4: list[FilingRow] = []
    for row in rows_from_insider_txns(pit.insider_txns(sid), who):
        (tape_4a if row.kind == "form4a" else form4).append(row)
    return [
        *rows_from_corporate_events(pit.corporate_event_facts(sid), who),
        *rows_from_stakes(pit.activist_stake_facts(sid), who),
        *form4,
        *merge_4a(tape_4a, rows_from_amendments(amendments, who)),
        *rows_from_revenue(revenue, who),
        *rows_from_splits(pit.split_events(sid), who, clock=clock),
        *rows_from_dilution(pit.dilution_facts(sid), who),
        *rows_from_catalysts(pit.catalyst_facts(sid), who),
        *rows_from_ratified(
            pit.cash_burn_facts(sid),
            pit.shares_outstanding_facts(sid),
            pit.revenue_mix_facts(sid),
            who,
        ),
    ]


def read_filing_rows(
    conn: psycopg.Connection,
    sids: Sequence[UUID],
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
    *,
    days: int,
    tickers: Mapping[UUID, str] | None = None,
    tenant_id: UUID = DEFAULT_TENANT_ID,
) -> dict[UUID, tuple[FilingRow, ...]]:
    """``{security_id: its FilingRows}`` dated from ``asof - days`` through ``asof``, each company's list in
    no particular order (a page sorts). ``known_at`` + ``clock`` are the request's ``serve_view(asof)`` pair,
    threaded into the point-in-time view and the 4/A SELECT alike. ``tickers`` = the ticker a page shows per
    company (a card's own, the Cockpit's ``?name=`` key); the master's ticker stands in for a company not named.
    Every company in ``sids`` has an entry (``()`` = nothing in the window)."""
    ids = list(dict.fromkeys(sids))
    if not ids:
        return {}
    start = asof - timedelta(days=days)
    names = {k: v for k, v in master.tickers_for(conn, ids, tenant_id=tenant_id).items() if v}
    names.update({k: v for k, v in (tickers or {}).items() if v})
    amendments = read_amendments(conn, ids, asof, known_at, clock, start=start, tenant_id=tenant_id)

    def read(pit: Any, sid: UUID) -> tuple[FilingRow, ...]:
        who = Who(sid, names.get(sid, "?"), pit.security_cik(sid), pit.security_name(sid))
        rows = rows_for_company(pit, who, clock=clock, amendments=amendments.get(sid, ()))
        return tuple(r for r in rows if start <= r.date <= asof)

    return per_company(conn, ids, asof, known_at, clock, read)
