from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from tempfile import TemporaryDirectory
from typing import Protocol
from uuid import UUID

import psycopg

from replay.episodes import derive_episodes
from replay.metrics import MIN_N, compute_metrics
from replay.schema import CallSnapshot, Episode, Outcome
from scoreboard.artifact import write_snapshot
from scoreboard.assemble import _censor_leading_warming
from scoreboard.schema import ReplaySnapshot, ReplayThesisHistory, ScoredEpisode

# The replay-panel snapshot: replayed history, flattened into the Scoreboard's own vocabulary and
# written as ONE JSON artifact the lean app can serve. The build_snapshot() core is PURE and
# duckdb-free (testable without the .[replay] extra); only main() imports the replay machinery,
# lazily — this module must keep importing in the lean prod image (the lean-import test pins it).
#
# The seam: the window defaults to ending at record_began - 1 (the day before the forward record's
# first call-of-record), so replay covers history and the record covers everything after — no
# double-counted arms. Pushing --end past it is allowed but LOUD (window_overlaps_record rides the
# artifact and its banner), never silent. Start defaults to end - 365d (the ~1y price depth).


class _Realized(Protocol):  # what compute_metrics' withheld leg needs — duck-typed like the scorer
    def first_close_on_or_after(self, security_id: UUID, d: date) -> tuple[date, float] | None: ...
    def last_close_through(self, security_id: UUID, through: date) -> tuple[date, float] | None: ...


@dataclass(frozen=True)
class ThesisMeta:
    """The display metadata the artifact carries per thesis (resolved at snapshot time)."""

    tenant_id: UUID | None
    name: str
    ticker: str | None
    basket_size: int


def _triggers_at_arm(snaps: list[CallSnapshot], ep: Episode):
    """The member's fired evidence on the ARM-DATE snapshot (MemberRow now carries triggers) —
    the same WHY the forward record reads from the arm-date card (invariant #6)."""
    for s in snaps:
        if s.asof == ep.arm_date:
            for m in s.members:
                if m.security_id == ep.security_id and m.tier == "armed":
                    return list(m.triggers)
    return []


def build_snapshot(
    timeline: dict[UUID, list[CallSnapshot]],
    scored: list[tuple[Episode, Outcome]],
    *,
    thesis_meta: dict[UUID, ThesisMeta],
    window_start: date,
    window_end: date,
    pin: datetime,
    generated_at: datetime,
    matured_asof: date,
    record_began: date | None,
    realized: _Realized | None = None,
    single_name_security: dict[UUID, UUID] | None = None,
) -> ReplaySnapshot:
    """Flatten a replay run into the artifact — pure (no DB, no duckdb, no clock).

    The honesty flags mirror the forward record exactly: ``censored_start`` = already armed on the
    window's FIRST replayed day (true arm date unknowable); ``matured`` = the episode's own exit_by
    elapsed by ``matured_asof`` (the snapshot's generation date — replay scores with unbounded
    forward reads, so maturity is against the data edge, not the window end); metrics judge only
    matured + non-censored episodes (the SAME eligibility rule as the live summary, so the two
    strips are comparable); the withheld metric's timeline is censor-trimmed the same way."""
    by_thesis: dict[UUID, list[tuple[Episode, Outcome]]] = {}
    for ep, out in scored:
        by_thesis.setdefault(ep.thesis_id, []).append((ep, out))

    theses: list[ReplayThesisHistory] = []
    eligible: list[Outcome] = []
    n_censored = 0
    for tid, meta in thesis_meta.items():
        snaps = timeline.get(tid, [])
        first_replayed = snaps[0].asof if snaps else None
        episodes: list[ScoredEpisode] = []
        for ep, out in by_thesis.get(tid, []):
            censored = first_replayed is not None and ep.arm_date == first_replayed
            matured = ep.exit_by is not None and ep.exit_by <= matured_asof
            if censored:
                n_censored += 1
            elif matured:
                eligible.append(out)
            episodes.append(
                ScoredEpisode(
                    episode=ep,
                    outcome=out,
                    status="open" if ep.dearm_date is None else "closed",
                    matured=matured,
                    censored_start=censored,
                    triggers_at_arm=_triggers_at_arm(snaps, ep),
                )
            )
        theses.append(
            ReplayThesisHistory(
                thesis_id=tid,
                tenant_id=meta.tenant_id,
                name=meta.name,
                ticker=meta.ticker,
                basket_size=meta.basket_size,
                episodes=episodes,
            )
        )

    metrics = compute_metrics(
        eligible,
        timeline={tid: _censor_leading_warming(s) for tid, s in timeline.items()},
        realized=realized,
        single_name_security=single_name_security,
    )
    overlaps = record_began is not None and window_end >= record_began
    banner = (
        f"REPLAYED — today's code + dials over historical facts (window {window_start} → "
        f"{window_end}, pinned {pin.date()}); NOT the record. Baskets are not versioned "
        f"(REPLAY.md known limitation). {len(eligible)} episodes eligible for metrics "
        f"(matured + non-censored; gate n<{MIN_N})."
        + (
            " WARNING: the window overlaps the forward record — arms may appear in both sections."
            if overlaps
            else ""
        )
    )
    n_episodes = sum(len(t.episodes) for t in theses)
    return ReplaySnapshot(
        generated_at=generated_at.isoformat(),
        window_start=window_start,
        window_end=window_end,
        known_at_pin=pin.isoformat(),
        record_began=record_began,
        window_overlaps_record=overlaps,
        banner=banner,
        min_n=MIN_N,
        n_theses=len(theses),
        n_episodes=n_episodes,
        n_censored=n_censored,
        n_eligible=len(eligible),
        metrics=metrics.metrics,
        theses=theses,
    )


def _record_began(conn: psycopg.Connection) -> date | None:
    with conn.cursor() as cur:
        cur.execute("SELECT min(asof) AS began FROM calls")
        row = cur.fetchone()
        return row["began"] if row else None


def main() -> None:
    p = argparse.ArgumentParser(
        description="Write the Scoreboard's replay-panel artifact (replayed history, NOT the "
        "record). Runs replay — needs the .[replay] extra (the dev venv), not the prod image."
    )
    p.add_argument("--start", help="window start YYYY-MM-DD (default: end - 365d)")
    p.add_argument(
        "--end",
        help="window end YYYY-MM-DD (default: the day before the forward record began; "
        "pushing past it is allowed but marks the artifact window_overlaps_record)",
    )
    args = p.parse_args()

    # replay machinery imported HERE, not at module top — the lean image imports this module fine
    from db.session import connect
    from replay.export import export_snapshot
    from replay.harness import replay_all
    from replay.pit import connect_mirror
    from replay.scoring import RealizedPrices, score_episodes
    from repositories import thesis_repo

    conn = connect()
    try:
        record_began = _record_began(conn)
        end = (
            date.fromisoformat(args.end)
            if args.end
            else (record_began - timedelta(days=1) if record_began else date.today())
        )
        start = date.fromisoformat(args.start) if args.start else end - timedelta(days=365)
        pin = datetime.now(timezone.utc)

        theses = thesis_repo.list_all(conn)  # archived EXCLUDED (the locked default)
        meta = {
            t.id: ThesisMeta(
                tenant_id=t.tenant_id,
                name=t.name,
                ticker=t.ticker,
                basket_size=len(t.basket),
            )
            for t in theses
        }
        single_name = {
            t.id: [m.security_id for m in t.basket if m.security_id is not None][0]
            for t in theses
            if len([m for m in t.basket if m.security_id is not None]) == 1
        }

        with TemporaryDirectory(prefix="sb_replay_mirror_") as tmp:
            export_snapshot(conn, tmp)
            con = connect_mirror(tmp)
            try:
                timeline = replay_all(conn, con, start=start, end=end, known_at=pin)
                episodes = [ep for snaps in timeline.values() for ep in derive_episodes(snaps)]
                realized = RealizedPrices(con)
                scored = list(zip(episodes, score_episodes(episodes, realized), strict=True))
                snap = build_snapshot(
                    timeline,
                    scored,
                    thesis_meta=meta,
                    window_start=start,
                    window_end=end,
                    pin=pin,
                    generated_at=pin,
                    matured_asof=date.today(),
                    record_began=record_began,
                    realized=realized,
                    single_name_security=single_name,
                )
            finally:
                con.close()
        path = write_snapshot(snap)
        print(
            f"wrote {path}\nwindow {snap.window_start} -> {snap.window_end} | "
            f"theses {snap.n_theses} | episodes {snap.n_episodes} "
            f"(censored {snap.n_censored}, eligible {snap.n_eligible})"
            + (
                "\nWARNING: window overlaps the forward record"
                if snap.window_overlaps_record
                else ""
            )
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
