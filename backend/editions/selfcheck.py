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

    def as_json(self) -> dict[str, Any]:
        d = asdict(self)
        return json.loads(json.dumps(d, default=str))


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
    """The top-level card fields that differ (canonicalized), so a mismatch says WHERE it is."""
    a, b = mine.model_dump(mode="json"), theirs.model_dump(mode="json")
    out = []
    for k in sorted(set(a) | set(b)):
        if json.dumps(a.get(k), sort_keys=True, default=str) != json.dumps(
            b.get(k), sort_keys=True, default=str
        ):
            # lists compare order-independently, like ``_canonical``
            if isinstance(a.get(k), list) and isinstance(b.get(k), list):
                ka = sorted(json.dumps(x, sort_keys=True, default=str) for x in a[k])
                kb = sorted(json.dumps(x, sort_keys=True, default=str) for x in b[k])
                if ka == kb:
                    continue
            out.append(k)
    return out


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
        same = _canonical(mine) == _canonical(recorded)
        out.append(
            EdgeResult(
                **base,
                outcome="match" if same else "mismatch",
                differs=[] if same else card_differences(mine, recorded),
            )
        )
    return out
