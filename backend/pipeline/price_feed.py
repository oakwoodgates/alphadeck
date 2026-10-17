"""T3 + Q8 — the nightly pass's price-feed EXPECTATIONS: which unpriceable rows the operator has
ACKNOWLEDGED, and which price-leg 404s are therefore EXPECTED rather than errors.

THE PROBLEM T3 CLOSES (MEASURED on prod 2026-10-16). Six names fail their price leg every night with a
vendor 404 — HLLK, GTLS, XOMA, CPRX, AVNS and WONDF. Four of them (GTLS / XOMA / CPRX / AVNS) are names the
T1 classifier already reads as CLOSED (a delisting form filed near the last bar, nothing listed under another
symbol): the vendor dropped a delisted ticker, which is exactly what should happen. But a 404 is a
``NameResult`` error, an error counts toward ``ingest_errors``, and ``ingest_fresh = ingest_errors == 0``
— so every row of those names' theses was stamped ``ingest_fresh=false`` every night and the flag carried no
signal at all.

THE RULE (operator Q9 = yes, generalized by Q8). A price-leg **404 Not Found** (``NameResult.price_not_found``
— a FACT the ingest recorded, never a judgment) on a name whose stopped tape is

- (a) CLOSED by T1 (``StaleTape.closed``), or
- (b) ACKNOWLEDGED unpriceable by the operator (``StaleTape.acknowledged`` — an active ``price_feed_ack``),

is EXPECTED: it is removed from the name's ``error`` / ``leg_errors`` (so it is not a name error, does not
count toward ``ingest_errors``, does not make ``ingest_fresh`` false, and does not page), and recorded as an
``ExpectedPriceMiss`` on the thesis result — on the artifact and as ONE quiet line in the printed report, so
the excused 404 stays VISIBLE (#9). The names themselves stay in the quiet closed / acknowledged blocks.

What stays LOUD, on purpose: a RENAMED name that was not adopted (HLLK — the fix is one Adopt click away),
an UNPRICEABLE name that was not acknowledged (WONDF until the operator decides), any non-404 price failure
(a timeout, a 5xx, a parse error) whatever the classification, and every other leg's error on the same name.

WHY THE FETCH STILL HAPPENS (the "skip it entirely?" question, reasoned through). Skipping the price fetch
for a closed or acknowledged name would save one vendor request per name per night (eight today) and is NOT
safe: the classification is DERIVED from the tape (a closed tape is a stale tape with a delisting form near
its last bar; an unpriceable one has no bar at all), so a name whose fetch is skipped can never produce the
bar that would declassify it — "closed" would become self-perpetuating. A relisted name, a ticker the vendor
starts serving again, a WONDF the vendor begins to price: each is noticed ONLY because the fetch still runs
(bars land, the edge moves, the row stops being stale, the excuse no longer applies). So the cheap fallback
is the design: fetch every night, and excuse only a 404 on a name the CURRENT pass classified.

Pure: no DB, no network, no clock. ``pipeline.daily`` reads the acknowledged set (``price_feed_repo``) and
calls these between the tape classification and the recording gate.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING
from uuid import UUID

from domain.feed_kinds import PRICE, feed_kind
from pipeline.name_errors import MAX_ERROR_CHARS

if TYPE_CHECKING:  # keep the layering one-way (no runtime import of the ingest unit)
    from pipeline.ingest_thesis import NameResult
    from pipeline.tape_health import StaleTape

# The two reasons a price 404 is expected (wire strings — they ride the artifact; never reformat them)
EXPECTED_CLOSED = "closed"
EXPECTED_ACKNOWLEDGED = "acknowledged"


@dataclass(frozen=True)
class ExpectedPriceMiss:
    """One name whose price-leg 404 was EXPECTED tonight: ``reason`` is ``closed`` (T1 classified the tape
    closed) or ``acknowledged`` (the operator acknowledged it unpriceable); ``error`` is the vendor message
    that was excused (clipped like a name error), kept so the excuse is auditable."""

    security_id: UUID
    ticker: str | None
    reason: str
    error: str

    def to_json(self) -> dict:
        return {
            "security_id": str(self.security_id),
            "ticker": self.ticker,
            "reason": self.reason,
            "error": self.error,
        }


def _clip(s: str) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= MAX_ERROR_CHARS else s[: MAX_ERROR_CHARS - 1] + "…"


def mark_acknowledged(
    rows: Iterable[StaleTape], acknowledged: Collection[UUID]
) -> tuple[StaleTape, ...]:
    """Q8 — mark each UNPRICEABLE price row whose security the operator acknowledged
    (``acknowledged`` = the active ``price_feed_ack`` security ids). Only an unpriceable row can be marked:
    an acknowledgment never silences a closed, renamed or plain feed-gap row (a name that HAD bars and
    stopped is a feed gap, whatever was acknowledged when it had none). Order preserved."""
    ack = set(acknowledged)
    return tuple(
        replace(s, acknowledged=True) if s.unpriceable and s.security_id in ack else s for s in rows
    )


def expected_miss_reasons(rows: Iterable[StaleTape]) -> dict[UUID, str]:
    """``security_id -> reason`` for every PRICE row whose 404 is expected tonight: CLOSED (T1) or
    ACKNOWLEDGED unpriceable (Q8). A renamed or a plain feed-gap row is never here — its 404 is the news.
    """
    out: dict[UUID, str] = {}
    for s in rows:
        if feed_kind(s.kind).key != PRICE.key:
            continue
        if s.closed:
            out.setdefault(s.security_id, EXPECTED_CLOSED)
        elif s.acknowledged:
            out.setdefault(s.security_id, EXPECTED_ACKNOWLEDGED)
    return out


def excuse_expected_misses(
    names: Sequence[NameResult], reasons: Mapping[UUID, str]
) -> tuple[list[NameResult], tuple[ExpectedPriceMiss, ...]]:
    """T3 — strip the EXPECTED price 404s out of ``names`` (one thesis's ingest results).

    A name is excused only when ALL hold: its security is in ``reasons``, its price leg FAILED, and that
    failure was a vendor 404 (``price_not_found``). Then its ``price`` entry leaves ``leg_errors`` and
    ``error`` is rebuilt from the remaining legs EXACTLY as the ingest builds it (``"<leg>: <message>"``
    joined by ``"; "``) — ``None`` when the price leg was the only failure. Every other leg's error, and any
    non-404 price failure, is left untouched. Returns the rewritten list (input order) and the
    ``ExpectedPriceMiss`` rows, ONE per security (a name walked twice — a value-chain duplicate — is one
    excused miss). Pure: the inputs are never mutated."""
    out: list[NameResult] = []
    misses: dict[UUID, ExpectedPriceMiss] = {}
    for r in names:
        reason = reasons.get(r.security_id)
        price_msgs = [m for leg, m in (r.leg_errors or ()) if leg == "price"]
        if reason is None or not getattr(r, "price_not_found", False) or not price_msgs:
            out.append(r)
            continue
        rest = tuple((leg, m) for leg, m in r.leg_errors if leg != "price")
        out.append(
            replace(
                r,
                error="; ".join(f"{leg}: {m}" for leg, m in rest) or None,
                leg_errors=rest,
            )
        )
        misses.setdefault(
            r.security_id,
            ExpectedPriceMiss(r.security_id, r.ticker, reason, _clip(price_msgs[0])),
        )
    return out, tuple(misses.values())
