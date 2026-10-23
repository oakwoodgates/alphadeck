"""THE LIVE-EDGE SELF-CHECK — the engine at the latest recorded night vs that night's call of record.

The calls log is the answer key at its LIVE EDGE only. On the night the cron records, the record clock and the
public clock coincide (everything public by then and held by then is the same set — ``db.public_clock``), so
the engine's card for that night, on today's rules, must equal the card the cron recorded for it — canonical
full-card equality (``calls_repo._canonical``, the comparison ``record_if_changed`` itself uses). History
before the edge is judged on the PUBLIC clock (the operator's 2026-10-21 rule); this module never replays a
recorded row on the record clock.

A mismatch is reported, never hidden, with what can explain it: the row was recorded under another policy
(``config_hash``) or by another image (``code_sha``) than the engine running now — on a deploy day a
difference is expected, not drift — or a fact that became public by the end of that night was stored after
the cron's pass. The calls log is only ever READ here.

MONEY IS COMPARED TO THE CENT (S5). A provenance money total (``total_usd``, ``planned_screened_usd``, any key
``usd`` / ``*_usd``) is a float SUM rounded to cents by the detector (``round(sum, 2)``). Summing the same
transactions in another order moves the last bits, and a sum sitting on a half cent then rounds to a value one
cent away — MEASURED on the Edition 1 publish (2026-10-23): AI Memory & Storage read ``risk_signals`` as its
only difference, COHR's insider-sell ``total_usd`` 2371494.57 in the engine's card against 2371494.58 in the
recorded one, on every source of that risk (the 10-21 page against that night's recorded row, read on a dev
copy of prod). So when the canonical cards differ, they are compared again with money values equal within one
cent (``cards_match``); every other value still compares exactly as ``_canonical`` does. This is the
COMPARISON only: what the cron records, ``calls_repo._canonical`` and every CallCard field are untouched.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any
from uuid import UUID

import psycopg

from domain.call import CallCard
from domain.config import DEFAULT_CONFIG, CallConfig, config_hash
from repositories.calls_repo import _canonical
from repositories.mappers import row_to_call


@dataclass(frozen=True)
class EdgeResult:
    thesis_id: UUID
    asof: date
    outcome: str  # "match" | "mismatch" | "no_page" (a recorded row, no engine card)
    call_id: UUID | None = None
    recorded_at: datetime | None = None
    same_policy: bool | None = None
    same_image: bool | None = None  # None when either side has no image sha (a dev venv)
    differs: list[str] = field(default_factory=list)
    # a match that needed the one-cent money tolerance (the module header) — said, never silent
    money_within_cent: bool = False

    def as_json(self) -> dict[str, Any]:
        d = asdict(self)
        return json.loads(json.dumps(d, default=str))


# --- the comparison: canonical equality, then money to the cent ------------------------------------------

MONEY_TOLERANCE_USD = 0.01
_EPS = 1e-6  # float slack on the tolerance itself (0.58 - 0.57 is 0.01000000000002 in IEEE)


def is_money_key(key: str | None) -> bool:
    """A provenance money value: the key ``usd`` or any ``*_usd`` (``total_usd``, ``planned_screened_usd``)."""
    return key is not None and (key == "usd" or key.endswith("_usd"))


def _is_num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _masked(x: Any, key: str | None = None) -> Any:
    """``x`` with every money value blanked and other floats rounded like ``_canonical`` — the ORDER key for
    lists, so two lists whose elements differ only in a cent sort the same way on both sides."""
    if isinstance(x, dict):
        return {k: _masked(v, k) for k, v in sorted(x.items())}
    if isinstance(x, list):
        return sorted(
            (_masked(e) for e in x), key=lambda e: json.dumps(e, sort_keys=True, default=str)
        )
    if _is_num(x) and is_money_key(key):
        return None
    if isinstance(x, float):
        return round(x, 9)
    return x


def _order_key(x: Any) -> tuple[str, str]:
    # masked first (the cent-blind order), then the exact form (a deterministic tie-break)
    return (
        json.dumps(_masked(x), sort_keys=True, default=str),
        json.dumps(x, sort_keys=True, default=str),
    )


def equal_within_cents(a: Any, b: Any, key: str | None = None) -> bool:
    """Structural equality with ``_canonical``'s rules (dict keys, lists order-independent, floats to 9
    places) except that a MONEY value (``is_money_key``) equals one within ``MONEY_TOLERANCE_USD``.
    """
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(equal_within_cents(a[k], b[k], k) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return False
        return all(
            equal_within_cents(x, y)
            for x, y in zip(sorted(a, key=_order_key), sorted(b, key=_order_key))
        )
    if _is_num(a) and _is_num(b):
        if is_money_key(key):
            return abs(float(a) - float(b)) <= MONEY_TOLERANCE_USD + _EPS
        return round(float(a), 9) == round(float(b), 9)
    return a == b


def cards_match(mine: CallCard, theirs: CallCard) -> tuple[bool, bool]:
    """``(match, via_money_tolerance)``: canonical full-card equality (``calls_repo._canonical``, the compare
    ``record_if_changed`` uses), else equality with money values within one cent."""
    if _canonical(mine) == _canonical(theirs):
        return True, False
    within = equal_within_cents(mine.model_dump(mode="json"), theirs.model_dump(mode="json"))
    return within, within


def recorded_rows_at(
    conn: psycopg.Connection, night: date, *, known_by: datetime
) -> dict[UUID, dict[str, Any]]:
    """The call of record per thesis at ``night``: the latest HONEST (non-reconstructed) row recorded by the
    cut — the row the Scoreboard reads for that night (the latest append wins)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (thesis_id) * FROM calls "
            "WHERE asof = %s AND NOT reconstructed AND recorded_at <= %s "
            "ORDER BY thesis_id, recorded_at DESC, seq DESC",
            (night, known_by),
        )
        return {r["thesis_id"]: r for r in cur.fetchall()}


def card_differences(mine: CallCard, theirs: CallCard) -> list[str]:
    """The top-level card fields that differ (canonicalized, money to the cent), so a mismatch says WHERE it
    is."""
    a, b = mine.model_dump(mode="json"), theirs.model_dump(mode="json")
    return [k for k in sorted(set(a) | set(b)) if not equal_within_cents(a.get(k), b.get(k), k)]


def live_edge(
    conn: psycopg.Connection,
    cards: dict[UUID, list[CallCard]],
    *,
    known_by: datetime,
    night: date,
    cfg: CallConfig = DEFAULT_CONFIG,
    code_sha: str | None = None,
) -> list[EdgeResult]:
    """Compare the engine's card at ``night`` (the latest recorded night) with each thesis's recorded card
    for it. ``code_sha`` = the running image's (``Settings.image_sha``); ``None`` leaves ``same_image``
    unknown."""
    want = config_hash(cfg)
    rows = recorded_rows_at(conn, night, known_by=known_by)
    out: list[EdgeResult] = []
    for tid, row in sorted(rows.items(), key=lambda kv: str(kv[0])):
        mine = next((c for c in cards.get(tid, ()) if c.asof == night), None)
        base = {
            "thesis_id": tid,
            "asof": night,
            "call_id": row["id"],
            "recorded_at": row["recorded_at"],
            "same_policy": (row.get("config_hash") == want) if row.get("config_hash") else None,
            "same_image": (
                (row["code_sha"] == code_sha) if (code_sha and row.get("code_sha")) else None
            ),
        }
        if mine is None:
            out.append(EdgeResult(**base, outcome="no_page"))
            continue
        recorded = row_to_call(row)
        same, within = cards_match(mine, recorded)
        out.append(
            EdgeResult(
                **base,
                outcome="match" if same else "mismatch",
                differs=[] if same else card_differences(mine, recorded),
                money_within_cent=within,
            )
        )
    return out
