"""The filing's own inline XBRL, read raw: the cover's share counts and the EPS share base.

companyfacts drops dimensioned facts (every per-class cover count) and can miss a combined multi-registrant filing for
months, while the cited 10-Q or 10-K document carries both. The extractor already fetches that document
(``ingest.edgar.extract._read_document``: cache-first, no extra request); this module reads its raw text:

- the ``xbrli:context`` elements: an instant or a start and end date, and the explicit members of each axis;
- the ``ix:nonFraction`` facts of two concepts, ``scale`` and ``sign`` applied:
  ``dei:EntityCommonStockSharesOutstanding`` (the cover, ``read_cover``) and
  ``us-gaap:WeightedAverageNumberOfSharesOutstandingBasic`` (the EPS share base, ``eps_share_base``).

An inline fact wraps the printed number, so the cover reading and the printed cover are one reading, not two; the
independent check on a cover count is the same filing's EPS share base (``classes_outside_eps``). Pure and
deterministic, regex over the raw text (no XML parser: filings are not always well-formed); it decides no tier.
"""

from __future__ import annotations

import html
import math
import re
from dataclasses import dataclass
from datetime import date
from itertools import combinations

COVER_CONCEPT = "EntityCommonStockSharesOutstanding"
EPS_CONCEPT = "WeightedAverageNumberOfSharesOutstandingBasic"
_CLASS_AXIS = "StatementClassOfStockAxis"
_ENTITY_AXIS = "LegalEntityAxis"

_CONTEXT_RE = re.compile(
    r"<(?:[\w-]+:)?context\b[^>]*?\bid\s*=\s*\"([^\"]+)\"[^>]*>(.*?)</(?:[\w-]+:)?context\s*>",
    re.S | re.I,
)
_INSTANT_RE = re.compile(r"<(?:[\w-]+:)?instant\s*>\s*([^<\s]+)\s*<", re.I)
_START_RE = re.compile(r"<(?:[\w-]+:)?startDate\s*>\s*([^<\s]+)\s*<", re.I)
_END_RE = re.compile(r"<(?:[\w-]+:)?endDate\s*>\s*([^<\s]+)\s*<", re.I)
_MEMBER_RE = re.compile(
    r"<(?:[\w-]+:)?explicitMember\b[^>]*?\bdimension\s*=\s*\"([^\"]+)\"[^>]*>\s*([^<\s]+)\s*<", re.I
)
_TYPED_RE = re.compile(r"<(?:[\w-]+:)?typedMember\b[^>]*?\bdimension\s*=\s*\"([^\"]+)\"", re.I)
# An opening ``ix:nonFraction`` tag (a self-closing nil fact never matches) and the closing tag. Facts NEST: a
# "basic and diluted" line wraps the basic fact inside the diluted one (SMR, NET, RBLX), so each opening tag reads its
# printed text up to the FIRST closing tag after it, never a regex that consumes the inner fact.
_FACT_OPEN_RE = re.compile(r"<(?:[\w-]+:)?nonFraction\b([^>]*?)(?<!/)>", re.I)
_FACT_CLOSE_RE = re.compile(r"</(?:[\w-]+:)?nonFraction\s*>", re.I)
_NONNUMERIC_RE = re.compile(r"<(?:[\w-]+:)?nonNumeric\b([^>]*?)(?<!/)>", re.I)
_ATTR_RE = re.compile(r"([\w:.-]+)\s*=\s*\"([^\"]*)\"")
_TAG_RE = re.compile(r"<[^>]+>")

# Members of the class axis that are not a class of common stock, so never summed into a cover count: an ADR line
# (ordinary shares held as ADSs, a subset of the ordinary count), a preferred class, warrants (pre-funded or not), a
# fully diluted figure, exchangeable units (an Up-C's LP units). Matched on the member's local name, lower-cased.
_SET_ASIDE = (
    (("adr", "americandepositary", "depositary"), "an ADR line, a subset of the ordinary shares"),
    (("preferred", "preference"), "a preferred class"),
    (("warrant",), "warrants"),
    (("fullydiluted", "diluted"), "a fully diluted figure"),
    (("exchangeable",), "exchangeable units"),
)
# A class key from a member's local name: the letter (and digit) after "Class" -- CommonClassAMember, ClassACommon-
# StockMember, CapitalClassCMember and CommonClassA1Member give A, A, C and A1 -- so a cover member and an EPS member
# of one class match even when the filer names them differently (GOOGL: goog:CapitalClassCMember on the cover,
# us-gaap:CommonClassCMember in the EPS table).
_CLASS_KEY_RE = re.compile(r"Class([A-Z]\d*)(?![a-z])")


@dataclass(frozen=True)
class Context:
    """One ``xbrli:context``: an instant, or a start and end date; its explicit members as (axis local name, member
    QName); ``other_axis`` when it carries a typed member."""

    id: str
    instant: date | None
    start: date | None
    end: date | None
    members: tuple[tuple[str, str], ...]
    other_axis: bool = False


@dataclass(frozen=True)
class InlineFact:
    """One ``ix:nonFraction`` fact: ``value`` with scale and sign applied (None when the printed text is not a
    number), the printed text as shown, its context and its offset in the raw document."""

    concept: str
    value: float | None
    shown: str
    context: Context
    offset: int


@dataclass(frozen=True)
class CoverCount:
    """One cover share count: ``member`` is the class member's QName, None for the undimensioned count."""

    member: str | None
    value: float
    shown: str
    offset: int

    @property
    def label(self) -> str:
        return member_label(self.member)


@dataclass(frozen=True)
class CoverReading:
    """The cover's share counts at its own as-of instant (``asof``).

    ``counted`` are the counts that make the cover figure (one, or one per class of common stock); ``figure`` is
    their sum, or ``total`` when the filing tags a count equal to the sum of the others beside them (KNSA's
    ``CommonStockMember``: the total, never added to its parts). ``set_aside`` names every other count at the instant
    and why it is not summed: an ADR line, a preferred class, warrants, another registrant of a combined filing
    (``dei:LegalEntityAxis``) or a context on another axis."""

    asof: date
    counted: tuple[CoverCount, ...]
    figure: float
    total: CoverCount | None = None
    set_aside: tuple[tuple[CoverCount, str], ...] = ()

    @property
    def multi_class(self) -> bool:
        return len(self.counted) >= 2


@dataclass(frozen=True)
class EpsBase:
    """The EPS share base of one period: the basic weighted-average count ending on the period of report.
    ``by_class`` (member QName, value) are the per-class facts; ``figure`` prefers them over the ``total``.
    """

    start: date
    end: date
    total: float | None
    by_class: tuple[tuple[str, float], ...] = ()

    @property
    def figure(self) -> float | None:
        if self.by_class:
            return sum(v for _, v in self.by_class)
        return self.total


def _local(qname: str) -> str:
    return qname.rsplit(":", 1)[-1]


def member_label(member: str | None) -> str:
    """A readable label for a class member: ``us-gaap:CommonClassAMember`` gives "Common Class A"; None (the
    undimensioned count) gives "common stock"."""
    if member is None:
        return "common stock"
    name = _local(member)
    name = name[: -len("Member")] if name.endswith("Member") and len(name) > 6 else name
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", name)


def class_key(member: str | None) -> str:
    """The class a member names (A, C, A1), else its local name; the empty string for the undimensioned count."""
    if member is None:
        return ""
    name = _local(member)
    name = name[: -len("Member")] if name.endswith("Member") else name
    m = _CLASS_KEY_RE.search(name)
    return m.group(1) if m else name


def set_aside_reason(member: str) -> str | None:
    """Why a class-axis member is never summed into a cover count, or None for a class of common stock."""
    name = _local(member).lower()
    for needles, why in _SET_ASIDE:
        if any(n in name for n in needles):
            return why
    return None


def _date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s.strip()[:10])
    except ValueError:
        return None


def read_contexts(raw: str) -> dict[str, Context]:
    """Every ``xbrli:context`` of the document by id."""
    out: dict[str, Context] = {}
    for cid, body in _CONTEXT_RE.findall(raw):
        inst, start, end = _INSTANT_RE.search(body), _START_RE.search(body), _END_RE.search(body)
        out[cid] = Context(
            id=cid,
            instant=_date(inst.group(1)) if inst else None,
            start=_date(start.group(1)) if start else None,
            end=_date(end.group(1)) if end else None,
            members=tuple((_local(axis), member) for axis, member in _MEMBER_RE.findall(body)),
            other_axis=bool(_TYPED_RE.search(body)),
        )
    return out


def _number(shown: str, fmt: str) -> float | None:
    """The printed text as a number under its ``format``: a dash, "no" or "none" is zero (``ixt:fixed-zero``,
    ``ixt-sec:numwordsen``); comma-decimal formats swap the separators. None when it is not a number.
    """
    s = shown.strip()
    f = fmt.lower()
    if "zero" in f or s in {"-", "\u2013", "\u2014"} or s.lower() in {"no", "none", "nil", "zero"}:
        return 0.0
    s = s.replace("\u00a0", "").replace(" ", "")
    if "comma" in f and "decimal" in f:  # ixt:num-comma-decimal: 1.234.567,89
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def read_facts(
    raw: str, concepts: frozenset[str], contexts: dict[str, Context] | None = None
) -> list[InlineFact]:
    """The ``ix:nonFraction`` facts whose concept's local name is in ``concepts``, in document order, nested ones
    included. A fact whose context is not in the document is skipped."""
    ctx = read_contexts(raw) if contexts is None else contexts
    out: list[InlineFact] = []
    for m in _FACT_OPEN_RE.finditer(raw):
        attrs = dict(_ATTR_RE.findall(m.group(1)))
        concept = _local(attrs.get("name", ""))
        if concept not in concepts:
            continue
        c = ctx.get(attrs.get("contextRef", ""))
        close = _FACT_CLOSE_RE.search(raw, m.end())
        if c is None or close is None:
            continue
        shown = re.sub(
            r"\s+", " ", html.unescape(_TAG_RE.sub("", raw[m.end() : close.start()]))
        ).strip()
        value = _number(shown, attrs.get("format", ""))
        if value is not None:
            try:
                value *= 10 ** int(attrs.get("scale", "0") or "0")
            except ValueError:
                value = None
        if value is not None and attrs.get("sign") == "-":
            value = -value
        out.append(InlineFact(concept, value, shown, c, m.start()))
    return out


def document_period_end(raw: str, contexts: dict[str, Context] | None = None) -> date | None:
    """The end of the context of ``dei:DocumentPeriodEndDate`` (an undimensioned one first)."""
    ctx = read_contexts(raw) if contexts is None else contexts
    ends: list[tuple[bool, date]] = []
    for m in _NONNUMERIC_RE.finditer(raw):
        attrs = dict(_ATTR_RE.findall(m.group(1)))
        if _local(attrs.get("name", "")) != "DocumentPeriodEndDate":
            continue
        c = ctx.get(attrs.get("contextRef", ""))
        if c is not None and (c.end or c.instant):
            ends.append((bool(c.members), c.end or c.instant))  # type: ignore[arg-type]
    return min(ends)[1] if ends else None


def _class_member(c: Context) -> tuple[bool, str | None]:
    """(usable, member): a context with no dimension (member None) or with the class axis alone is usable."""
    if c.other_axis:
        return False, None
    if not c.members:
        return True, None
    if len(c.members) == 1 and c.members[0][0] == _CLASS_AXIS:
        return True, c.members[0][1]
    return False, None


def read_cover(raw: str, contexts: dict[str, Context] | None = None) -> CoverReading | None:
    """The cover's share counts at its latest as-of instant, or None when the document tags none it can use.

    Usable: an undimensioned count, or one per member of ``StatementClassOfStockAxis``. Never a member of
    ``dei:LegalEntityAxis`` (another registrant of a combined filing: EXC's subsidiaries) or of any other axis, and
    never a set-aside class (``set_aside_reason``). Zero counts are dropped. A count equal to the sum of all the
    others is their total: the figure, not added again. None (fail closed) when a usable count is not a number, or
    an undimensioned count sits beside class counts it does not total."""
    ctx = read_contexts(raw) if contexts is None else contexts
    facts = [f for f in read_facts(raw, frozenset({COVER_CONCEPT}), ctx) if f.context.instant]
    usable = [f for f in facts if _class_member(f.context)[0]]
    if not usable:
        return None
    asof = max(f.context.instant for f in usable)  # type: ignore[type-var]
    at = [f for f in facts if f.context.instant == asof]
    counted: list[CoverCount] = []
    aside: list[tuple[CoverCount, str]] = []
    seen: set[str | None] = set()
    for f in at:
        ok, member = _class_member(f.context)
        if f.value is None:
            if ok:
                return None
            continue
        count = CoverCount(member, f.value, f.shown, f.offset)
        if not ok:
            axes = {a for a, _ in f.context.members}
            why = (
                "another registrant of this combined filing (LegalEntityAxis)"
                if _ENTITY_AXIS in axes
                else "a context on another axis"
            )
            aside.append((CoverCount(_entity_member(f.context), f.value, f.shown, f.offset), why))
            continue
        why = set_aside_reason(member) if member else None
        if why:
            aside.append((count, why))
        elif member in seen:
            if any(c.member == member and c.value != count.value for c in counted):
                return None  # one class tagged twice with two counts: ambiguous
        elif count.value != 0:
            seen.add(member)
            counted.append(count)
    if not counted:
        return None
    total = _total_beside(counted)
    if total is not None:
        parts = tuple(c for c in counted if c is not total)
        return CoverReading(asof, parts, total.value, total, tuple(aside))
    if len(counted) > 1 and any(c.member is None for c in counted):
        return None  # an undimensioned count beside class counts it does not total: ambiguous
    return CoverReading(asof, tuple(counted), sum(c.value for c in counted), None, tuple(aside))


def _entity_member(c: Context) -> str | None:
    return next(
        (m for a, m in c.members if a == _ENTITY_AXIS), c.members[0][1] if c.members else None
    )


def _total_beside(counted: list[CoverCount]) -> CoverCount | None:
    """The count equal to the sum of all the others (two or more counts), the undimensioned one first."""
    if len(counted) < 2:
        return None
    whole = sum(c.value for c in counted)
    hits = [c for c in counted if c.value == whole - c.value]
    if not hits:
        return None
    return next((c for c in hits if c.member is None), hits[0])


def eps_share_base(
    raw: str,
    period_end: date,
    min_days: int,
    max_days: int,
    contexts: dict[str, Context] | None = None,
) -> EpsBase | None:
    """The EPS share base of the cited period: the basic weighted-average share count whose context ends on the
    period of report (else on the document's own ``dei:DocumentPeriodEndDate``) and spans ``min_days`` to
    ``max_days``, the shortest such span (a 10-Q's quarter, a 10-K's year). Undimensioned is the total; class-axis
    members (set-aside members excepted) are the per-class facts. None when the document tags none.
    """
    ctx = read_contexts(raw) if contexts is None else contexts
    facts = [
        f
        for f in read_facts(raw, frozenset({EPS_CONCEPT}), ctx)
        if f.value is not None and f.context.start and f.context.end and _class_member(f.context)[0]
    ]
    ends = [period_end]
    doc_end = document_period_end(raw, ctx)
    if doc_end is not None and doc_end != period_end:
        ends.append(doc_end)
    for end in ends:
        spans = sorted(
            {
                (f.context.end - f.context.start).days  # type: ignore[operator]
                for f in facts
                if f.context.end == end
                and min_days <= (f.context.end - f.context.start).days <= max_days  # type: ignore[operator]
            }
        )
        if not spans:
            continue
        pick = [
            f
            for f in facts
            if f.context.end == end and (f.context.end - f.context.start).days == spans[0]  # type: ignore[operator]
        ]
        total = next((f.value for f in pick if not f.context.members), None)
        by_class: dict[str, float] = {}
        for f in pick:
            member = _class_member(f.context)[1]
            if member and not set_aside_reason(member):
                by_class.setdefault(member, f.value)  # type: ignore[arg-type]
        start = pick[0].context.start
        return EpsBase(start, end, total, tuple(by_class.items()))  # type: ignore[arg-type]
    return None


@dataclass(frozen=True)
class OutsideEps:
    """The EPS share base leaves part of a multi-class cover out. ``how`` names the evidence: "per-class" (the
    per-class EPS facts name no class for ``outside``) or "nearest" (the base is nearer the sum of the ``inside``
    classes, the nearest strict subset, than the cover sum; which class is out is then not established).
    ``inside_sum`` is the sum of the ``inside`` cover counts."""

    how: str
    inside: tuple[CoverCount, ...]
    outside: tuple[CoverCount, ...]
    inside_sum: float


def classes_outside_eps(cover: CoverReading, eps: EpsBase | None) -> OutsideEps | None:
    """Whether the EPS share base of the cited period leaves a cover class out, or None (no, or undecidable).

    Per-class EPS facts decide first (MSTR: its Class B has its own EPS fact, so a gap from issuance is never read as
    a class outside): the cover classes whose class key (``class_key``) matches no per-class EPS fact are the
    candidates, when at least one other class matches. Without per-class facts, the candidates are the classes
    outside the strict subset whose sum is nearest the base (by ratio): ENHA's 13.4M base against Class A's 13.8M
    and a cover sum of 39.7M.

    Either way the numbers must agree, with no tolerance: the classes kept (sum S) must be nearer the base E than
    the cover sum F is (``|S - E| < |F - E|``), and the classes left out (``F - S``) must be larger than what S still
    misses by (``|S - E|``). A class smaller than that miss explains nothing (FHI's 9,000 Class A shares beside a 4%
    gap). The figure offered never changes on this; the flag and the note carry the evidence.
    """
    if not cover.multi_class or eps is None or eps.figure is None or eps.figure <= 0:
        return None
    base, full = eps.figure, cover.figure

    def agrees(inside: tuple[CoverCount, ...]) -> bool:
        kept = sum(c.value for c in inside)
        miss = abs(kept - base)
        return miss < abs(full - base) and miss < full - kept

    if eps.by_class:
        keys = {class_key(m) for m, _ in eps.by_class}
        inside = tuple(c for c in cover.counted if class_key(c.member) in keys)
        if len(inside) == len(cover.counted):
            return None
        if inside:
            if not agrees(inside):
                return None
            outside = tuple(c for c in cover.counted if c not in inside)
            return OutsideEps("per-class", inside, outside, sum(c.value for c in inside))

    def far(x: float) -> float:
        return abs(math.log(x / base)) if x > 0 else math.inf

    best: tuple[float, tuple[CoverCount, ...]] | None = None
    for k in range(1, len(cover.counted)):
        for subset in combinations(cover.counted, k):
            d = far(sum(c.value for c in subset))
            if best is None or d < best[0]:
                best = (d, subset)
    if best is None or not agrees(best[1]):
        return None
    inside = best[1]
    outside = tuple(c for c in cover.counted if c not in inside)
    return OutsideEps("nearest", inside, outside, sum(c.value for c in inside))


__all__ = [
    "COVER_CONCEPT",
    "EPS_CONCEPT",
    "Context",
    "CoverCount",
    "CoverReading",
    "EpsBase",
    "InlineFact",
    "OutsideEps",
    "class_key",
    "classes_outside_eps",
    "document_period_end",
    "eps_share_base",
    "member_label",
    "read_contexts",
    "read_cover",
    "read_facts",
    "set_aside_reason",
]
