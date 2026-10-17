"""L1 — the late-row reading: a call-of-record recorded AFTER its night closed never defines an episode.

THE RULE (operator decision Q5 = A, 2026-10-16). A **late** row is a non-reconstructed ``calls`` row whose
``recorded_at`` is at or after the END of its night's on-time window — the next weekday's 06:00 ET EDGAR open
(``pipeline.night_coverage.is_late``, the helper M1/M2 already share, so "late" has ONE definition). Such a
row can have read a filing SEC accepted after the night it claims to describe: MEASURED on 2026-10-12, the
22:30 pass died after 4 theses, and the next day's boot catch-up recorded 9 theses ~18 h late (10-13
17:04-17:13), three of them citing Form 4s SEC accepted on 10-13; for 4 theses the late row superseded an
honest on-time row in the per-as-of dedup (AI Memory & Storage: seq 1334 at 22:42 -> seq 1367 at 17:04).

Under the policy (``Settings.scoreboard_exclude_late``, default on; ``GET /scoreboard?include_late=``
overrides it per request) every Scoreboard record read drops late rows BEFORE the per-as-of dedup — at the
same place and the same way as the reconstructed (0042) and annotated (D3b) filters, across all four
winning-row peer reads (timeline, ingest health, run identity, annotations). So an on-time row for the same
night WINS over a later late one, and a night whose only rows are late reads exactly like a cron gap (the
record last spoke on the prior honest row). Nothing is deleted or marked: lateness is DERIVED on read from
``recorded_at`` (no migration), so flipping the policy re-scores the record exactly as before L1.

This module is the judge + the reads it needs: the ids to drop for one thesis (``late_call_ids``), the
banner's per-(thesis, night) list (``late_nights``), and the Admin readout's LATE-ONLY subset of that list
(``late_only_nights`` — the banner's GAP nights: a late row and nothing this reading scores, which Admin's
coverage still counts as recorded). ``calls_repo`` stays value-free (it drops the ids it is given); the
market timezone is deploy config, read here.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date, tzinfo
from uuid import UUID

import psycopg

from domain.market_time import market_tz
from domain.settings import get_settings
from pipeline.night_coverage import is_late, next_edgar_open
from repositories import calls_repo
from scoreboard.schema import LateNight


def resolve_exclude_late(exclude_late: bool | None = None) -> bool:
    """The Scoreboard's late-night read policy for ONE read (L1): an explicit bool wins (the request's
    ``include_late`` override, inverted by the router), ``None`` = the configured
    ``Settings.scoreboard_exclude_late``. The ``resolve_exclude_annotated`` twin — one resolution per
    request, threaded down, so every read in that request applies the same policy."""
    if exclude_late is None:
        return get_settings().scoreboard_exclude_late
    return exclude_late


def _late(stamp: Mapping, tz: tzinfo) -> bool:
    """One ``calls_repo.call_stamps`` row judged: non-reconstructed AND recorded at/after its night's
    on-time window closed. A reconstructed row is never "late" — it is set aside on its own marker.
    """
    return not stamp["reconstructed"] and is_late(stamp["recorded_at"], stamp["asof"], tz=tz)


def late_call_ids(
    conn: psycopg.Connection, thesis_id: UUID, *, tz: tzinfo | None = None
) -> frozenset[UUID]:
    """The ids of one thesis's LATE rows — what its record reads drop under the late-excluding reading.
    One read of the thesis's row stamps (never the cards). ``tz`` defaults to the market timezone.
    """
    tz = tz or market_tz()
    return frozenset(
        s["id"] for s in calls_repo.call_stamps(conn, thesis_id=thesis_id) if _late(s, tz)
    )


def judge_late_nights(stamps: Iterable[Mapping], *, tz: tzinfo) -> list[LateNight]:
    """PURE: every (thesis, as-of) holding at least one late row, from ``call_stamps``-shaped rows, ordered
    by as-of then thesis name. ``earlier_recorded_at`` = the stamp of the night's winning row under the
    late-excluding reading (the highest-``seq`` non-late, non-reconstructed row) — ``None`` when the night
    has none and reads like a cron gap."""
    by_night: dict[tuple[UUID, date], list[Mapping]] = {}
    for s in stamps:
        by_night.setdefault((s["thesis_id"], s["asof"]), []).append(s)
    out: list[LateNight] = []
    for (thesis_id, asof), rows in by_night.items():
        late = [r for r in rows if _late(r, tz)]
        if not late:
            continue
        kept = [r for r in rows if not r["reconstructed"] and not _late(r, tz)]
        winner = max(kept, key=lambda r: r["seq"]) if kept else None
        out.append(
            LateNight(
                thesis_id=thesis_id,
                thesis_name=late[0]["thesis_name"],
                asof=asof,
                recorded_at=min(r["recorded_at"] for r in late),
                edgar_open=next_edgar_open(asof, tz),
                n_rows=len(late),
                earlier_recorded_at=winner["recorded_at"] if winner else None,
            )
        )
    out.sort(key=lambda n: (n.asof, n.thesis_name, str(n.thesis_id)))
    return out


def late_nights(
    conn: psycopg.Connection, *, upto: date, tz: tzinfo | None = None
) -> list[LateNight]:
    """The banner's list: every (thesis, as-of <= ``upto``) holding a late row, ledger-wide (every thesis,
    every tenant — the ``reconstructed_asofs`` / ``active_nights`` scope), listed under BOTH readings.
    Capped at ``upto`` so a scrubbed-back view names only nights it can see. Read-only."""
    return judge_late_nights(calls_repo.call_stamps(conn, upto=upto), tz=tz or market_tz())


def judge_late_only_nights(stamps: Iterable[Mapping], *, tz: tzinfo) -> list[LateNight]:
    """PURE: the LATE-ONLY nights — the entries of ``judge_late_nights`` (same entries, same order) that read
    as a GAP under the late-excluding reading: ``earlier_recorded_at is None``, i.e. the night holds a late
    row and NO row this reading would score (no non-late, non-reconstructed row). L1's own gap marker, so
    "late" and "a gap on the Scoreboard" each keep one definition.

    The Admin readout's list (operator decision 2026-10-17). Admin's per-(thesis, night) coverage counts a
    late row as COVERING the night (the pass did record it), while the Scoreboard sets that row aside — so
    with nothing else to score, Admin reads "covered" where the Scoreboard shows a gap. Exactly those nights:
    - late only → listed;
    - late + reconstructed → listed (a reconstruction is never scored either, so both are set aside — a
      gap). A reconstructed-only night has no late row and is never listed (it is its own marker,
      ``reconstructed_nights``);
    - late + on-time, or late + a DAYTIME (pre-``RUN_AT``) row → NOT listed: the Scoreboard scores that
      honest row instead, so there is no gap to name (honest loudness)."""
    return [n for n in judge_late_nights(stamps, tz=tz) if n.earlier_recorded_at is None]


def late_only_nights(
    conn: psycopg.Connection, *, asofs: Sequence[date], tz: tzinfo | None = None
) -> list[LateNight]:
    """``judge_late_only_nights`` over every thesis's rows for the nights in ``asofs`` (the Admin coverage
    window; all tenants, every thesis — the coverage read's scope). One read of the row stamps, never the
    cards; ``[]`` for no nights. Read-only; the caller owns the connection."""
    if not asofs:
        return []
    return judge_late_only_nights(calls_repo.call_stamps(conn, asofs=asofs), tz=tz or market_tz())
