"""The Admin "Insider amendments" cards in PLAIN ENGLISH (operator ask, 2026-10-24) — the PURE layer that turns one
Form 4/A's stored state into what a card says: what the filer did (who, bought / sold, how many shares, at what
price, on what date, the dollar total), what each button does to the rows a call reads, and whether ANY answer
can change a call at all ("This changes no call.").

It explains; it never decides. Nothing here writes, and nothing changes what a decision does: each button's
effect is the RESOLVER's own answer (``domain.insider_amendments.resolve_insider_rows``) to "the read now" vs "the
read after this decision", over this amendment's stored rows, link and decisions plus ONE hypothetical decision
(the button's) — so the line a card prints is exactly what the click will do, never a paraphrase of it. Whether a
changed row can move a call is judged by the same review line that decides what is owed (``ReviewLine`` — a row a
live call or an edition page reads), so "This changes no call." and "owed" can never disagree.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from domain.insider_amendments import (
    ACTION_APPLY,
    ACTION_KEEP,
    ACTION_LINK,
    ACTION_REVOKE,
    AMBIGUOUS,
    AUTO_DISPOSITIONS,
    INERT_DISPOSITIONS,
    ROUTED_NO_PROVISIONAL,
    Effective,
    ReviewLine,
    _as_date,
    _num,
    _row_date,
    is_system_basis,
    link_to_candidate,
    resolve_insider_rows,
    signal_signature,
    standing_decisions,
)

# the card's buttons (the keys the panel looks a line up by)
ACT_APPLY = "apply"  # Use the correction
ACT_KEEP = "keep"  # Keep the original
ACT_ADD = "add"  # Add to the original
ACT_LINK = "link"  # Pick this filing (an ambiguous amendment)
ACT_UNDO = "undo"  # Undo the operator's decision (the verdict first, else the link)
ACT_UNDO_PICK = "undo_pick"  # Undo the system's pick (rule 2b) — the amendment is ambiguous again


@dataclass(frozen=True)
class TradeGroup:
    """Every row of ONE transaction code in a filing, summed: how many trades, shares, the price range, the date
    range and the dollar total (each row's ``usd``, else shares x price; ``None`` when no row carries either).
    """

    code: str | None
    trades: int
    shares: float | None
    price_low: float | None
    price_high: float | None
    first_date: date | None
    last_date: date | None
    usd: float | None


def _usd(row: Mapping[str, Any]) -> float | None:
    usd = _num(row.get("usd"))
    if usd is not None:
        return usd
    shares, price = _num(row.get("shares")), _num(row.get("price"))
    return None if shares is None or price is None else shares * price


def trade_groups(rows: Iterable[Mapping[str, Any]]) -> list[TradeGroup]:
    """The rows grouped by transaction code — the buys (``P``) and sales (``S``) first, then the other codes in
    code order."""
    by: dict[str | None, list[Mapping[str, Any]]] = {}
    for r in rows:
        by.setdefault(r.get("txn_code"), []).append(r)

    def order(code: str | None) -> tuple[int, str]:
        return ({"P": 0, "S": 1}.get(code or "", 2), code or "")

    out: list[TradeGroup] = []
    for code in sorted(by, key=order):
        grp = by[code]
        shares = [s for s in (_num(r.get("shares")) for r in grp) if s is not None]
        prices = [p for p in (_num(r.get("price")) for r in grp) if p is not None]
        dates = [d for d in (_row_date(r) for r in grp) if d is not None]
        usds = [u for u in (_usd(r) for r in grp) if u is not None]
        out.append(
            TradeGroup(
                code=code,
                trades=len(grp),
                shares=sum(shares) if shares else None,
                price_low=min(prices) if prices else None,
                price_high=max(prices) if prices else None,
                first_date=min(dates) if dates else None,
                last_date=max(dates) if dates else None,
                usd=sum(usds) if usds else None,
            )
        )
    return out


def signal_usd(rows: Iterable[Mapping[str, Any]], signal_codes: Iterable[str]) -> float | None:
    """The dollar total of the buys / sales (the signal codes) among ``rows``; ``None`` when there are none."""
    codes = set(signal_codes)
    usds = [_usd(r) for r in rows if r.get("txn_code") in codes]
    vals = [u for u in usds if u is not None]
    return sum(vals) if vals else None


_SEE_REMARKS = "see remarks"


def owner_role(
    rows: Iterable[Mapping[str, Any]],
    remarks: str | None,
    fallback: Iterable[Mapping[str, Any]] = (),
) -> str | None:
    """The reporting owner's role as the filing states it: the first non-blank ``insider_role`` on the 4/A's own
    rows, else on the original's (``fallback``). A role that only points at the remarks ("*** See Remarks") reads
    the remarks instead (their leading asterisks dropped); nothing is inferred."""
    for src in (rows, fallback):
        for r in src:
            role = " ".join(str(r.get("insider_role") or "").split())
            if not role:
                continue
            if _SEE_REMARKS in role.casefold():
                text = " ".join(str(remarks or "").split()).lstrip("* ").strip()
                return text or None
            return role
    return None


@dataclass(frozen=True)
class CardAction:
    """One button's effect on what a call reads: the buy / sell rows the read LOSES (``removes``) and GAINS
    (``adds``) if it is clicked, relative to the read now. ``target`` = the candidate a link names;
    ``disposition`` = how the 4/A reads against that candidate (a link only)."""

    action: str
    removes: tuple[Mapping[str, Any], ...] = ()
    adds: tuple[Mapping[str, Any], ...] = ()
    target: str | None = None
    disposition: str | None = None

    @property
    def rows(self) -> tuple[Mapping[str, Any], ...]:
        return (*self.removes, *self.adds)


def _signal_diff(
    now: Sequence[Mapping[str, Any]],
    then: Sequence[Mapping[str, Any]],
    signal_codes: Iterable[str],
) -> tuple[tuple[Mapping[str, Any], ...], tuple[Mapping[str, Any], ...]]:
    """``(the buy / sell rows in ``now`` but not ``then``, the ones in ``then`` but not ``now``)`` — compared by
    everything a call signal reads (``signal_signature``), as multisets."""
    codes = set(signal_codes)
    a = [r for r in now if r.get("txn_code") in codes]
    b = [r for r in then if r.get("txn_code") in codes]
    common = Counter(map(signal_signature, a)) & Counter(map(signal_signature, b))

    def leftover(side: list[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
        left = Counter(common)
        out: list[Mapping[str, Any]] = []
        for r in side:
            s = signal_signature(r)
            if left[s] > 0:
                left[s] -= 1
            else:
                out.append(r)
        return tuple(sorted(out, key=lambda r: (_row_date(r) or date.min, str(r.get("txn_code")))))

    return leftover(a), leftover(b)


def _stamp(link: Mapping[str, Any], decisions: Sequence[Mapping[str, Any]]) -> datetime:
    """An instant after every stored version — a hypothetical decision is the newest one."""
    seen = [link.get("recorded_at"), *(d.get("recorded_at") for d in decisions)]
    known = [t for t in seen if isinstance(t, datetime)]
    base = max(known) if known else datetime(2000, 1, 1, tzinfo=timezone.utc)
    return base + timedelta(seconds=1)


def card_actions(
    *,
    amendment: Mapping[str, Any],
    link: Mapping[str, Any],
    decisions: Sequence[Mapping[str, Any]],
    stored: Mapping[str, Sequence[Mapping[str, Any]]],
    eff: Effective,
    signal_codes: Iterable[str],
) -> list[CardAction]:
    """PURE — every button this amendment's card offers (the same rules the decision writers enforce —
    ``repositories.insider_amendment_repo.decide`` / ``link_candidate`` / ``revoke`` / ``undo_pick``), each with
    its effect on the read: the resolver run over this amendment's stored rows (``stored`` — its originals' and
    candidates' rows), its record, its newest link and its decisions, once as they stand and once with the
    button's decision appended.

    - an AMBIGUOUS amendment nobody linked: one ``link`` per candidate (picking alone changes nothing, except a
      zero-row 4/A, whose pick suppresses that filing until a verdict);
    - otherwise the operator's verdicts the state admits: ``apply`` (Use the correction — the whole original
      replaced), ``keep`` (Keep the original), ``add`` (Add to the original — a partial / consolidation / addition /
      restatement / field change), never over another operator verdict;
    - ``undo`` — the operator's standing verdict, else the operator's standing link;
    - ``undo_pick`` — the SYSTEM's pick (rule 2b) with the system's verdict under it (never under an operator
      verdict: that one's Undo comes first)."""
    codes = frozenset(signal_codes)
    acc = str(link["amendment_accession"])
    rows = [{**r, "accession": a} for a, rs in stored.items() for r in rs]
    decisions = list(decisions)
    link_d, verdict = standing_decisions(decisions).get(acc, (None, None))
    at = _stamp(link, decisions)

    def read(extra: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        return resolve_insider_rows(
            rows,
            amendments=[amendment],
            links=[link],
            decisions=[*decisions, *extra],
            asof=date.max,
        )

    now = read(())

    def hypo(n: int, **kw: Any) -> dict[str, Any]:
        base = {
            "id": f"zz-hypothetical-{n}",
            "amendment_accession": acc,
            "recorded_at": at + timedelta(microseconds=n),
            "revokes": None,
            "replaced_rows": None,
            "basis": "operator:hypothetical",
        }
        return {**base, **kw}

    def effect(action: str, extra: Sequence[Mapping[str, Any]], **kw: Any) -> CardAction:
        removes, adds = _signal_diff(now, read(extra), codes)
        return CardAction(action=action, removes=removes, adds=adds, **kw)

    out: list[CardAction] = []
    disp = eff.disposition
    if disp == AMBIGUOUS and link_d is None:
        amend_rows = list(amendment.get("rows") or [])
        for cand in eff.candidate_accessions:
            cdisp, origs = link_to_candidate(amend_rows, cand, stored.get(cand, ()), codes)
            d = hypo(
                1,
                action=ACTION_LINK,
                target_accession=cand,
                disposition=cdisp,
                original_accessions=list(origs),
            )
            out.append(effect(ACT_LINK, [d], target=cand, disposition=cdisp))
        return out
    operator_verdict = verdict is not None and not is_system_basis(verdict.get("basis"))
    if disp not in INERT_DISPOSITIONS and not operator_verdict:
        verdicts = [ACT_KEEP] if disp in AUTO_DISPOSITIONS else [ACT_APPLY, ACT_KEEP]
        if disp in ROUTED_NO_PROVISIONAL:
            verdicts.append(ACT_ADD)
        for v in verdicts:
            d = hypo(
                1,
                action=ACTION_KEEP if v == ACT_KEEP else ACTION_APPLY,
                replaced_rows=[] if v == ACT_ADD else None,
            )
            out.append(effect(v, [d]))
    if operator_verdict and verdict is not None:
        out.append(effect(ACT_UNDO, [hypo(1, action=ACTION_REVOKE, revokes=verdict["id"])]))
    elif link_d is not None and not is_system_basis(link_d.get("basis")):
        out.append(effect(ACT_UNDO, [hypo(1, action=ACTION_REVOKE, revokes=link_d["id"])]))
    if link_d is not None and is_system_basis(link_d.get("basis")) and not operator_verdict:
        extra = []
        if verdict is not None:
            extra.append(hypo(1, action=ACTION_REVOKE, revokes=verdict["id"]))
        extra.append(hypo(2, action=ACTION_REVOKE, revokes=link_d["id"]))
        out.append(effect(ACT_UNDO_PICK, extra))
    return out


def contested_rows(
    actions: Sequence[CardAction],
    *,
    eff: Effective,
    ambiguous_rows: Sequence[Mapping[str, Any]] = (),
) -> list[Mapping[str, Any]]:
    """Every buy / sell row SOME answer on the card could change: each button's rows, and — for an ambiguous
    amendment nobody linked — every buy / sell on every candidate and on the 4/A (``ambiguous_rows``: its target
    is unknown, so any of them can move once a filing is picked)."""
    out: list[Mapping[str, Any]] = [r for a in actions for r in a.rows]
    if eff.disposition == AMBIGUOUS and eff.link_decision_id is None:
        out.extend(ambiguous_rows)
    return out


def changes_no_call(
    actions: Sequence[CardAction],
    line: ReviewLine,
    *,
    eff: Effective,
    ambiguous_rows: Sequence[Mapping[str, Any]] = (),
) -> bool:
    """ "This changes no call." — True when NO answer on the card can change a row a live call or an edition page
    reads (``ReviewLine.inside`` over ``contested_rows``): the same line that decides what is owed.
    """
    return not line.inside(contested_rows(actions, eff=eff, ambiguous_rows=ambiguous_rows))


def filed_date(value: object) -> date | None:
    """A filing date from the stored JSON / a column (ISO text, a date, a datetime) — ``None`` when unknown."""
    try:
        return _as_date(value)
    except ValueError:
        return None
