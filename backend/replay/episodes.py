from __future__ import annotations

from datetime import date
from uuid import UUID

from domain.enums import State
from replay.schema import CallSnapshot, Episode, MemberRow

# Derive arm EPISODES from a per-thesis call timeline. Pure (no prices, no pit) — the scoring unit that
# collapses sticky Armed runs into one decision per (thesis, member, arm_date). See docs/REPLAY.md.


def _armed(snap: CallSnapshot) -> dict[UUID, MemberRow]:
    return {m.security_id: m for m in snap.members if m.tier == "armed"}


def _warm_date(snaps: list[CallSnapshot], arm_idx: int) -> date | None:
    """The earliest as-of of the contiguous WARMING-with-conviction run immediately preceding the arm —
    the thesis's "early" date for the edge-preservation metric. ``None`` if the arm had no visible warm-up
    (conviction + confirmation co-fired)."""
    wd: date | None = None
    i = arm_idx - 1
    while i >= 0 and snaps[i].state is State.WARMING and snaps[i].conviction_grade is not None:
        wd = snaps[i].asof
        i -= 1
    return wd


def _close_reason(dearm: CallSnapshot | None, exit_by: date | None, arm_until: date | None) -> str:
    """Classify why the arm ended (member-centric, from the de-arm snapshot + the clocks set at arm).
    ``managing`` is expected-zero in pure replay (no operator fills in historical facts)."""
    if dearm is None:
        return "window_end"
    if dearm.state is State.MANAGING:
        return "managing"
    if exit_by is not None and dearm.asof > exit_by:
        return "conviction_aged_out"
    if arm_until is not None and dearm.asof > arm_until:
        return "arm_until_lapsed"
    return "dearmed_other"


def _episode(snaps, sid, start_i, last_i, dearm_i, armed_sets) -> Episode:
    open_snap = snaps[start_i]
    m = armed_sets[start_i][sid]  # the member's row captured AT the arm
    dearm = snaps[dearm_i] if dearm_i is not None else None
    return Episode(
        thesis_id=open_snap.thesis_id,
        security_id=sid,
        is_headline=open_snap.armed_security_id == sid,
        arm_date=open_snap.asof,
        last_armed_date=snaps[last_i].asof,
        dearm_date=dearm.asof if dearm is not None else None,
        close_reason=_close_reason(dearm, m.exit_by, m.arm_until),
        warm_date=_warm_date(snaps, start_i),
        verdict=m.verdict,
        entry_grade=m.entry_grade,
        conviction_grade=m.conviction_grade,
        confidence=m.confidence,
        theme_armed=m.theme_armed,
        exit_by=m.exit_by,
        arm_until=m.arm_until,
    )


def derive_episodes(snapshots: list[CallSnapshot]) -> list[Episode]:
    """One thesis's call timeline -> its arm episodes (per member). A run opens when a member enters
    ``armed_members`` and closes when it leaves (or the window ends); a re-arm is a NEW episode."""
    snaps = sorted(snapshots, key=lambda s: s.asof)
    if not snaps:
        return []
    armed_sets = [_armed(s) for s in snaps]
    member_ids = sorted({sid for a in armed_sets for sid in a}, key=lambda u: u.int)
    episodes: list[Episode] = []
    for sid in member_ids:
        start_i: int | None = None
        for i, aset in enumerate(armed_sets):
            if sid in aset and start_i is None:
                start_i = i
            elif sid not in aset and start_i is not None:
                episodes.append(_episode(snaps, sid, start_i, i - 1, i, armed_sets))
                start_i = None
        if start_i is not None:  # still armed at the window end
            episodes.append(_episode(snaps, sid, start_i, len(snaps) - 1, None, armed_sets))
    return episodes


def episodes_for(timeline: dict[UUID, list[CallSnapshot]]) -> list[Episode]:
    """All arm episodes across a multi-thesis replay timeline (``replay_all``'s return)."""
    out: list[Episode] = []
    for snaps in timeline.values():
        out.extend(derive_episodes(snaps))
    return out
