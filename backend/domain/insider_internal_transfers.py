"""INTERNAL TRANSFERS — a buy and a sale between LINKED filers, kept on the read and screened from the signals.

THE DEFECT (found 2026-10-21; the operator's decisions the same day: "yes to all"). An internal transfer looks
like a buy plus a sale. On 2025-02-24 James J. Kim SOLD 869,565 AMKR shares at $21.85 and Sujoda Investments,
LP — the partnership of his children Susan, John and David — BOUGHT the same 869,565 at $21.85 the same day
("a transaction between members of the Kim family group"). Read as two trades, the buy was a CORE insider
trigger (a 2025 re-run at today's knowledge: AMKR armed in its thesis on 94 of 261 weekdays, on that buy) and the
sale read "3 insiders sold $38,119,047" for one $19.0M transfer (Agnes C. Kim re-reports her husband's sale "By
Spouse"). Fund families do it inside one filing: Pershing Square's funds "purchased and sold Subject Securities
among themselves"; ValueAct's "transferred in an internal restructuring".

MEASURED 2026-10-21 on prod (read-only; this module over every P/S signature on prod, the 0055 text as the repair
would write it): 46 of 46 same-filer signatures screened; 4 of the 44 cross-owner ones (AMKR 2025-02-24 and
2025-08-01, every leg; SLSN 2012 x2, Whitmore's own buy and sale); the other 40 stay counted — the private sales
between separate people (FTHM, RDNW, ASTH, FRMM), the 34 with no evidence at all (IAC/MGM), and AMKR 2021-12-30,
whose own filings name no counterparty holding ("By GRATs", "By trusts" — the naming natures first appear in 2022+
filings, outside the pair-local evidence by design).

THE RULE (``screen_internal_transfers``). A SIGNATURE is the code-P and code-S rows on one security's tape that
share (date, shares, price). On a signature holding both a buy and a sale, two rows ("legs") are LINKED by any
of three pieces of the FILINGS' OWN evidence — the legs' own filings only (a joint-report survivor's co-reporters'
filings included), never another date's, so every reader of the read sees the same verdict whatever its window:

1. SAME FILER — one reporting owner behind both legs (by CIK, else name — a joint survivor's co-reporters count).
   The operator's decision 2: a fund family rebalancing among its own accounts, a market-maker's matched fills.
2. SHARED HOLDING — a joint-report fingerprint (``domain.insider_joint_reports.filing_links``: a shared
   non-trivial post-trade holding, never two direct holders) links a buy-leg filing and a sale-leg filing.
3. NAMED HOLDING — a leg's filing reports INDIRECT shares (an ``<ownershipNature>`` on a transaction or a
   holding, ``filing_natures``, migration 0055) whose nature NAMES an owner behind the other leg: Susan Y. Kim's
   purchase filing reports shares "By James J. Kim 2024 GRAT" (she is a trustee of her father's trusts), James
   Kim's sale filing reports shares "By John T. Kim Family Trust". "One reporting the other's shares indirectly."

Within a signature every buy is paired with ONE sale it is linked to (a maximum matching — DIRECT legs first,
then read order): each pair is one internal transfer, and both of its legs are SCREENED. An INDIRECT leg left
over that is linked to a paired leg by a holding (2 or 3, never the same-filer link alone) is that leg's shares
reported AGAIN — Agnes Kim's "By Spouse" report of her husband's sale, linked by Susan Kim's "By Agnes C. Kim
2024 GRAT" — and is screened with it. Any other leftover (a direct row, a row of unknown ownership, the same
filer's genuine extra trade on the same terms — Goldman's second 63-share fill) stays COUNTED.

A private purchase between UNRELATED people — a director buying a founder's block, a Stock Purchase Agreement
between two holders, IAC buying $202.5M of MGM from Corvex — is NOT linked and stays real conviction (the
operator's decision 3; its quiet ledger note lives in ``domain/insider_private_purchases.py``).

KEEP AND SCREEN, NEVER DROP. A screened leg stays ON the read as a new dict carrying ``INTERNAL_TRANSFER`` (the
evidence and the counterparties); the detectors leave it out of their totals and NAME it ("2 internal-transfer
txns ($37,999,990) screened"), the Scoreboard ledger shows it grayed and labeled, the Cockpit flow sets it aside and
says so (#6, WB#2). ``fact_insider_txn`` is untouched.

SAFE ON MISSING EVIDENCE. The named-holding link reads ``filing_natures``; a row stored before migration 0055 and
not yet repaired (``pipeline.repair_insider_filer_text``) has none and links nothing that way, so it can only
stay counted, never be screened wrongly. The co-report rule needs ``direct_indirect == 'I'``; unknown is kept.

PURE and engine-agnostic: no clock, no DB; numbers compared as rounded floats (``Decimal`` on Postgres, ``float``
on the DuckDB mirror), the text arrays as lists on both. It runs inside
``domain.insider_amendments.ResolutionInputs.resolve`` AFTER the 4/A resolution and the joint-report collapse —
the one seam every as-of read of ``fact_insider_txn`` passes through (the live PIT, the Scoreboard overlays, the
replay mirror) — so live, replay and the ledgers screen identically (PIT parity). A read with no screened leg is
returned UNCHANGED (the same list), so no call golden can move on a tape without an internal transfer.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from domain.insider_joint_reports import (
    JOINT_ACCESSIONS,
    JOINT_REPORTERS,
    _num,
    filing_links,
    owner_key,
)

# The key a SCREENED leg carries (not a table column — like ``joint_reporters`` on a joint survivor): a dict
# ``{"with": (counterparty names...), "evidence": (kinds...), "co_report": bool}``.
INTERNAL_TRANSFER = "internal_transfer"

# the linking evidence kinds (``evidence`` on the tag, in this order)
SAME_FILER = "same_filer"
SHARED_HOLDING = "shared_holding"
NAMED_HOLDING = "named_holding"
_EVIDENCE_ORDER = (SAME_FILER, SHARED_HOLDING, NAMED_HOLDING)

BUY, SELL = "P", "S"
DIRECT, INDIRECT = "D", "I"

# --- naming: does a filer's text NAME an owner? ----------------------------------------------------------------
# Words that never identify an owner by themselves: corporate suffixes and generic fund/trust vocabulary. An
# owner is named when EVERY remaining word of its name (letters only, 3+ characters) appears in the text —
# "KIM JAMES J" -> {kim, james} ⊆ "By James J. Kim 2024 GRAT dtd. 8/5/24". A name left with a single word must
# be 5+ letters to count ("SUJODA Investments, LP" -> {sujoda}); a lone short word ("LEE S III" -> {lee}) names
# no one — it would match any Lee.
_NAME_STOP = frozenset(
    {
        "the",
        "and",
        "for",
        "llc",
        "inc",
        "corp",
        "corporation",
        "ltd",
        "limited",
        "plc",
        "company",
        "fund",
        "funds",
        "management",
        "capital",
        "partners",
        "partnership",
        "investments",
        "investment",
        "group",
        "holdings",
        "holding",
        "trust",
        "trusts",
        "advisors",
        "advisers",
        "associates",
        "ventures",
        "master",
        "offshore",
        "international",
        "global",
    }
)
_WORD = re.compile(r"[a-z]+")


def name_words(name: str | None) -> frozenset[str]:
    """The words that identify an owner name (letters only, 3+ characters, minus ``_NAME_STOP``)."""
    return frozenset(
        w for w in _WORD.findall((name or "").casefold()) if len(w) >= 3 and w not in _NAME_STOP
    )


def names_owner(owner_name: str | None, text: str | None) -> bool:
    """Does ``text`` NAME the owner ``owner_name``? Every identifying word of the name appears in the text; a
    single identifying word must be 5+ letters (see ``_NAME_STOP``). Deterministic — never a model's reading.
    """
    words = name_words(owner_name)
    if not words or (len(words) == 1 and len(next(iter(words))) < 5):
        return False
    return words <= frozenset(_WORD.findall((text or "").casefold()))


# --- reading a screened row (the detectors, the display rail, the Scoreboard ledger) --------------------------


def is_internal_transfer(row: Mapping[str, Any]) -> bool:
    """Is this row a screened internal-transfer leg (kept on the read, left out of every signal total)?"""
    return bool(row.get(INTERNAL_TRANSFER))


def transfer_with(row: Mapping[str, Any]) -> tuple[str, ...]:
    """The counterparties a screened leg transferred with (``()`` for any other row)."""
    tag = row.get(INTERNAL_TRANSFER)
    return tuple(tag.get("with") or ()) if isinstance(tag, Mapping) else ()


def screened_label(rows: Sequence[Mapping[str, Any]]) -> str | None:
    """``"2 internal-transfer txns ($37,999,990) screened"`` for the screened legs among ``rows``, else ``None`` —
    the detectors' named set-aside (#6), the 10b5-1 screen's phrasing."""
    legs = [r for r in rows if is_internal_transfer(r)]
    if not legs:
        return None
    usd = sum(float(r.get("usd") or 0.0) for r in legs)
    n = len(legs)
    return f"{n} internal-transfer txn{'s' if n != 1 else ''} (${usd:,.0f}) screened"


# --- the screen ----------------------------------------------------------------------------------------------


def _signature(row: Mapping[str, Any]) -> tuple[Any, ...] | None:
    if row.get("txn_code") not in (BUY, SELL) or row.get("valid_from") is None:
        return None
    sh = _num(row.get("shares"))
    if sh is None or sh <= 0:
        return None
    return (row["valid_from"], sh, _num(row.get("price")))


class _Leg:
    """One signature row and the evidence behind it (its own row plus, for a joint survivor, every co-reporter's
    row of the same trade)."""

    __slots__ = ("i", "row", "code", "owners", "accessions", "natures", "direct")

    def __init__(self, i: int, row: Mapping[str, Any], group: Sequence[Mapping[str, Any]]) -> None:
        self.i = i
        self.row = row
        self.code = row["txn_code"]
        self.direct = row.get("direct_indirect") == DIRECT
        self.owners: dict[str, str] = {}
        for r in (row, *group):
            self.owners.setdefault(owner_key(r), str(r.get("insider_name") or ""))
        for name in row.get(JOINT_REPORTERS) or ():
            # a joint survivor's co-reporter (its row left the read) — by name; its CIK, if any, is on the
            # group row above, already added
            self.owners.setdefault("name:" + " ".join(str(name).casefold().split()), str(name))
        self.accessions = {str(row.get("accession"))} | {
            str(a) for a in row.get(JOINT_ACCESSIONS) or ()
        }
        # the INDIRECT natures the leg's filings state — None when no row behind it carries the capture (0055)
        captured = [
            r.get("filing_natures") for r in (row, *group) if r.get("filing_natures") is not None
        ]
        self.natures: list[str] | None = (
            [str(n) for ns in captured for n in ns] if captured else None
        )


def _names_any(natures: list[str] | None, owners: Mapping[str, str]) -> bool:
    if not natures:
        return False
    return any(names_owner(name, nature) for name in owners.values() for nature in natures)


def screen_internal_transfers(
    rows: Sequence[dict[str, Any]], evidence_rows: Sequence[Mapping[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """PURE — ONE security's read with every internal-transfer leg TAGGED (``INTERNAL_TRANSFER``), never dropped.

    ``rows`` are the read as every consumer sees it (after the joint-report collapse); ``evidence_rows`` the same
    read BEFORE the collapse (a joint survivor's co-reporters' rows carry its evidence — their filings' natures);
    ``None`` means ``rows`` is its own evidence. Kept rows keep their order; a screened leg is a NEW dict in its
    place (the input is never mutated). Nothing to screen -> the same list."""
    by_sig: dict[tuple[Any, ...], list[int]] = {}
    for i, r in enumerate(rows):
        sig = _signature(r)
        if sig is not None:
            by_sig.setdefault(sig, []).append(i)
    sigs = {
        sig: idx
        for sig, idx in by_sig.items()
        if any(rows[i]["txn_code"] == BUY for i in idx)
        and any(rows[i]["txn_code"] == SELL for i in idx)
    }
    if not sigs:
        return rows if isinstance(rows, list) else list(rows)

    evidence = rows if evidence_rows is None else evidence_rows
    ev: dict[tuple[Any, ...], list[Mapping[str, Any]]] = {}
    for r in evidence:
        sig = _signature(r)
        if sig in sigs:
            ev.setdefault((*sig, r["txn_code"]), []).append(r)  # type: ignore[misc]
    # the joint-report union-find, built only when two legs come from different filings
    links = None

    tags: dict[int, dict[str, Any]] = {}
    for sig, idx in sigs.items():
        legs: list[_Leg] = []
        for i in idx:
            row = rows[i]
            accs = {str(row.get("accession"))} | {str(a) for a in row.get(JOINT_ACCESSIONS) or ()}
            group = [
                r
                for r in ev.get((*sig, row["txn_code"]), ())
                if r is not row and str(r.get("accession")) in accs
            ]
            legs.append(_Leg(i, row, group))
        n = len(legs)
        kinds: dict[tuple[int, int], tuple[str, ...]] = {}
        for a in range(n):
            for b in range(a + 1, n):
                la, lb = legs[a], legs[b]
                found: list[str] = []
                if set(la.owners) & set(lb.owners):
                    found.append(SAME_FILER)
                if la.accessions != lb.accessions:
                    if links is None:
                        links = filing_links(evidence)
                    ra = {links.find(x) for x in la.accessions - lb.accessions}
                    rb = {links.find(x) for x in lb.accessions - la.accessions}
                    if ra & rb:
                        found.append(SHARED_HOLDING)
                if _names_any(la.natures, lb.owners) or _names_any(lb.natures, la.owners):
                    found.append(NAMED_HOLDING)
                if found:
                    kinds[(a, b)] = kinds[(b, a)] = tuple(found)
        if not kinds:
            continue
        # pair each buy with ONE sale it is linked to — a maximum matching, direct legs first
        order = sorted(range(n), key=lambda x: (not legs[x].direct, x))
        buys = [x for x in order if legs[x].code == BUY]
        sells = [x for x in order if legs[x].code == SELL]
        sale_of: dict[int, int] = {}  # sale leg -> its buy leg

        def assign(buy: int, seen: set[int]) -> bool:
            for s in sells:
                if (buy, s) not in kinds or s in seen:
                    continue
                seen.add(s)
                if s not in sale_of or assign(sale_of[s], seen):
                    sale_of[s] = buy
                    return True
            return False

        for b in buys:
            assign(b, set())
        if not sale_of:
            continue
        partner = {**sale_of, **{b: s for s, b in sale_of.items()}}
        for x, y in partner.items():
            tags[legs[x].i] = {
                "with": tuple(dict.fromkeys(legs[y].owners.values())),
                "evidence": tuple(k for k in _EVIDENCE_ORDER if k in kinds[(x, y)]),
                "co_report": False,
            }
        # an INDIRECT leftover linked by a HOLDING to a paired leg: those shares reported again
        for x in range(n):
            if x in partner or legs[x].row.get("direct_indirect") != INDIRECT:
                continue
            via = [
                y
                for y in partner
                if (x, y) in kinds
                and (SHARED_HOLDING in kinds[(x, y)] or NAMED_HOLDING in kinds[(x, y)])
            ]
            if not via:
                continue
            others = [partner[y] if legs[y].code == legs[x].code else y for y in via]
            tags[legs[x].i] = {
                "with": tuple(dict.fromkeys(nm for y in others for nm in legs[y].owners.values())),
                "evidence": tuple(
                    k for k in _EVIDENCE_ORDER if any(k in kinds[(x, y)] for y in via)
                ),
                "co_report": True,
            }
    if not tags:
        return rows if isinstance(rows, list) else list(rows)
    return [{**r, INTERNAL_TRANSFER: tags[i]} if i in tags else r for i, r in enumerate(rows)]
