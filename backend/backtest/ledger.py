"""The run's own episode LEDGER — the per-thesis drill-down, in the Scoreboard's exact vocabulary.

A pooled report answers "does the algorithm behave sensibly"; it deliberately carries no thesis
identifier at all. The question it cannot answer is "show me the rows behind that number", and that
question has to be answerable or the pooled view is unfalsifiable. So a run also writes a LEDGER: the
same episodes, grouped by thesis, scored, with the WHY at the arm.

**It reuses the Scoreboard's models rather than inventing parallel ones.** ``ReplaySnapshot`` /
``ReplayThesisHistory`` / ``ScoredEpisode`` already describe exactly this — replayed episodes with the
record-honesty flags — and ``scoreboard.replay_snapshot.build_snapshot`` already composes them (the
censoring rule, the maturity rule, the triggers-at-arm join). Re-deriving those rules here is precisely
how two surfaces come to disagree about which episodes are eligible, so this module calls that function
and adds only what a backtest has and a replay snapshot does not: its own banner, and the security
identity resolved AT RUN TIME.

**Identity is baked in, not resolved at serve time.** The Scoreboard's replay route joins its artifact
to today's ``security_master`` for tickers. A backtest run is immutable and addressable — a run id in a
PR description means one set of numbers forever — so the names it reports are resolved once, by the
writer that holds the DB connection, and travel with the artifact. The serving route then needs no
database at all, which is what keeps it a pure artifact reader.

**This is a DRILL-DOWN, never a ranking.** Grouping by thesis is how a reader checks a pooled number
against its rows. Ordering theses by outcome would turn the same grouping into a leaderboard, which
tests the IDEA rather than the timing and is an invariant #4 violation. The rows are therefore ordered
by name, the banner says so, and a test pins it.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, Field

from domain.config import short_hash
from replay.schema import CallSnapshot, Episode, Outcome
from scoreboard.replay_snapshot import ThesisMeta, build_snapshot
from scoreboard.schema import ReplaySnapshot

LEDGER_NAME = "ledger.json"


class _Realized(Protocol):  # what compute_metrics' withheld leg needs — duck-typed like the scorer
    def first_close_on_or_after(self, security_id: UUID, d: date) -> tuple[date, float] | None: ...
    def last_close_through(self, security_id: UUID, through: date) -> tuple[date, float] | None: ...


class SecurityRef(BaseModel):
    """One name, as the run resolved it. Every field is nullable: a security with no master row keeps
    its id and renders as "—" rather than disappearing from the ledger (#9 — a dropped name is a
    system failure, and that applies to a research surface too)."""

    ticker: str | None = None
    cik: str | None = None
    name: str | None = None


class BacktestLedger(BaseModel):
    """The ledger artifact: the Scoreboard's replay snapshot plus the identity to render it."""

    kind: str = "backtest_ledger"
    snapshot: ReplaySnapshot
    # keyed by security_id as a STRING — JSON has no UUID key type, and round-tripping through the
    # artifact must not depend on a parser being clever about it
    securities: dict[str, SecurityRef] = Field(default_factory=dict)


def compose_banner(
    *,
    window_start: date,
    window_end: date,
    pin: datetime,
    clock: str,
    config_hash: str,
    dials_moved: list[str],
    n_eligible: int,
    min_n: int,
    roster_note: str | None,
) -> str:
    """The ledger's own sentence, BACKEND-authored and rendered verbatim.

    It does not reuse the replay panel's banner, which opens "today's code + dials over historical
    facts" — true of that panel, false of any run that moved a dial. A run states its OWN dials, its own
    clock, and that the grouping below is a drill-down rather than a ranking."""
    moved = ", ".join(dials_moved) if dials_moved else "none (the production dials)"
    clock_clause = (
        "facts enter when they became PUBLIC"
        if clock == "public"
        else "facts enter when this system recorded them"
    )
    roster_clause = (
        f" {roster_note[0].upper()}{roster_note[1:]}."
        if roster_note
        else " Rosters are point-in-time."
    )
    return (
        f"RUN LEDGER — this run's episodes, grouped by thesis. A RECOMPUTE over historical facts under "
        f"THIS run's dials (window {window_start} → {window_end}, pinned {pin.date()}, clock "
        f"{clock}: {clock_clause}); never the record. Policy {short_hash(config_hash)} · dials moved: "
        f"{moved}."
        + roster_clause
        + f" {n_eligible} episodes eligible for the metrics here (matured + "
        f"non-censored; gate n<{min_n}) — a different, smaller set than the pooled panel scores, so the "
        f"two are never read as one number. Grouped by thesis to check the pooled view against its own "
        f"rows: a drill-down, in name order, NOT a ranking."
    )


def build_ledger(
    timeline: dict[UUID, list[CallSnapshot]],
    scored: list[tuple[Episode, Outcome]],
    *,
    thesis_meta: dict[UUID, ThesisMeta],
    securities: dict[UUID, SecurityRef],
    window_start: date,
    window_end: date,
    pin: datetime,
    generated_at: datetime,
    matured_asof: date,
    clock: str,
    config_hash: str,
    code_sha: str | None,
    dials_moved: list[str],
    realized: _Realized | None = None,
    single_name_security: dict[UUID, UUID] | None = None,
    roster_fallback_theses: int = 0,
    roster_source_note: str | None = None,
) -> BacktestLedger:
    """Flatten this run into the ledger artifact. PURE — no DB, no duckdb beyond the ``realized``
    reader the caller already holds, and no clock of its own."""
    # NAME ORDER, fixed here rather than left to the caller's dict: `build_snapshot` renders the
    # theses in the order it is handed them, and the one ordering a drill-down must never carry is one
    # derived from the outcome. Name order is arbitrary with respect to performance, which is the point.
    ordered = dict(sorted(thesis_meta.items(), key=lambda kv: (kv[1].name, str(kv[0]))))
    snap = build_snapshot(
        timeline,
        scored,
        thesis_meta=ordered,
        window_start=window_start,
        window_end=window_end,
        pin=pin,
        generated_at=generated_at,
        matured_asof=matured_asof,
        # A backtest window overlapping the forward record is the NORMAL case here (a run sweeps the
        # same history the record covers), so the replay panel's overlap warning would fire on every
        # run and stop meaning anything (#7). The backtest's separation from the record is structural
        # instead: a different store, a different endpoint, and no simulated row in `calls`.
        record_began=None,
        realized=realized,
        single_name_security=single_name_security,
        roster_fallback_theses=roster_fallback_theses,
        roster_source_note=roster_source_note,
        config_hash=config_hash,
        code_sha=code_sha,
    )
    snap = snap.model_copy(
        update={
            "banner": compose_banner(
                window_start=window_start,
                window_end=window_end,
                pin=pin,
                clock=clock,
                config_hash=config_hash,
                dials_moved=dials_moved,
                n_eligible=snap.n_eligible,
                min_n=snap.min_n,
                roster_note=roster_source_note,
            )
        }
    )
    return BacktestLedger(
        snapshot=snap,
        securities={
            str(sid): ref for sid, ref in sorted(securities.items(), key=lambda kv: str(kv[0]))
        },
    )
