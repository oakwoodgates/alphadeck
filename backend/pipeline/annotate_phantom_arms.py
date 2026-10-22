"""``pipeline.annotate_phantom_arms`` — D3b: find the recorded nights D3a's dating fix would have UN-ARMED
(phantom arms), and annotate them — REVERSIBLY — so the Scoreboard can set them aside.

THE BUG D3a FIXED. Every 10-Q re-reports the year-ago quarter as its prior-year comparative, and the
fundamentals ingest versions that comparative at the LATER filing. Before D3a (#390) the latest-version
pick handed ``revenue_acceleration`` / ``share_creep`` that later date, so a year-old revenue inflection
re-fired as fresh for another 180 days. Every honest row the cron recorded before D3a deploys was assembled
under that dating, and some of them are arms that rested only on such a re-dated quarter.

THE OPERATOR'S RULE (Q2, 2026-10-16): annotate them and exclude them from scoring, REVERSIBLY. Nothing in
``calls`` is deleted or rewritten (it is immutable — the ``no_update`` trigger). This CLI appends rows to
``call_annotation`` (migration 0045); the Scoreboard drops rows carrying an active ``exclude`` under a READ
policy (``Settings.scoreboard_exclude_annotated`` / ``?include_annotated=``) — flip the setting, or revoke the
annotations (``--revoke`` / ``--revoke-all``), and the record scores exactly as before.

THE ISOLATION REQUIREMENT — annotate ONLY what D3a changes. Code and config have drifted since most rows
were recorded; that drift must never be blamed on D3a. So every honest (``NOT reconstructed``) row is
re-assembled TWICE with the CURRENT code at the row's own as-of, on the PUBLIC clock (``asof = row.asof``,
``clock="public"``, ``known_at`` = the end of that market day — every fact counted from when it became public;
operator decision 2026-10-21, ``docs/INVARIANTS.md`` §4: call effects are measured on the public clock, never by
replaying what our database held at the row's ``recorded_at``. The applied annotations of 2026-10-17 were judged
on that record-clock replay; a (call, action) already judged is never judged again, so a re-run judges only the
rows not yet annotated, on the public clock):

  (i)  D3a OFF — the fundamentals rows re-dated to the pre-D3a reading (``knowable_from := valid_from``,
       ``knowable_accession := accession`` of the picked version — exactly what the detectors read before
       #390; the pick itself never changed). ``PreD3aDatingView`` below, DIAGNOSTIC ONLY.
  (ii) D3a ON — the current code as it ships.

Both read through the SAME funnel the live call uses (``pipeline.call_for_thesis.load_call_inputs`` +
``pipeline.core.assemble_from_pit`` — ``call_for_thesis(record=False)`` is literally those two calls), over
ONE prefetched point-in-time view, so the second assembly costs no further DB reads (the PIT memo). The
only difference between (i) and (ii) is the two annotation keys on the fundamentals rows.

THE CLASSIFICATION (``classify``):
- ``drift``     — (i) does NOT reproduce the recorded row: state, verdict, or the armed-member set differ.
                  Something other than D3a changed this row's call; it is REPORTED, never annotated as D3a's.
- ``exclude``   — (i) reproduces the recorded row, the row is ARMED, and (ii) is not armed: a phantom arm.
- ``note``      — (i) reproduces the recorded row, and (ii) differs from (i) in state, verdict, headline, or
                  armed-member set: the call stands (it stays scored) but D3a changes its composition.
- ``unchanged`` — (i) reproduces the row and (ii) equals (i): D3a does not touch this night.

The armed-member set is part of "reproduces" (a deliberate tightening of the state + verdict test): an arm
can only be blamed on D3a when the D3a-off re-assembly arms the SAME names the row armed.

WHAT THE BASKET IS. ``basket_member`` is full-replace; ``load_call_inputs`` reads the roster through
``thesis_repo.get_asof(known_at)`` — the latest ``basket_snapshot`` taken by the end of the row's as-of day (an
operator decision counts from when it was made), else the LIVE roster (an as-of before migration 0043 seeded the
snapshots on 2026-09-15). Each result says which (``roster``), and a live-fallback reproduction is only as good
as that roster — which is exactly what the reproduction test checks.

SAFETY (the ``pipeline.backfill`` / ``repair_reconstructed_precreation`` shape):
- **Dry-run is the DEFAULT**: it classifies, prints every candidate (and every drift row), and writes NOTHING —
  its connection is READ ONLY, so it physically cannot. ``--report-json PATH`` writes the full evidence to a
  file of the operator's choosing; nothing else is written. A dry run takes no lock (it writes nothing).
- ``--apply`` appends the exclude/note annotations in ONE transaction, after checking the inserted count, and
  writes a provenance JSON under ``data/annotations/``. It holds the call-of-record advisory lock
  (``pipeline/run_lock.py``) for the whole run — exit 75 if a daily pass or backfill holds it.
- **Idempotent**: a (call, action) this rule FAMILY already judged — active or revoked — is never judged again,
  so a re-run appends nothing and a revoke the operator made sticks across re-runs and image versions.
- Run ``--apply`` against prod only after a backup, from the deployed image (``rule`` records its sha).

    python -m pipeline.annotate_phantom_arms                                  # dry-run, every thesis
    python -m pipeline.annotate_phantom_arms --thesis <uuid> --report-json /tmp/d3b.json
    python -m pipeline.annotate_phantom_arms --apply                          # append the annotations
    python -m pipeline.annotate_phantom_arms --revoke <annotation-id> --reason "..." [--apply]
    python -m pipeline.annotate_phantom_arms --revoke-all --reason "..." [--apply]
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg

from db.bitemporal import KNOWABLE_FROM, KNOWABLE_SOURCE
from db.session import connect
from domain.call import CallCard
from domain.config import DEFAULT_CONFIG, CallConfig, config_hash
from domain.enums import State
from domain.market_time import known_at_for_asof
from domain.settings import get_settings
from domain.signal import SignalEvent
from pipeline.call_for_thesis import load_call_inputs
from pipeline.core import assemble_from_pit
from pipeline.run_lock import EXIT_ALREADY_RUNNING, PassAlreadyRunning, acquire_pass_lock
from repositories import call_annotations_repo, thesis_repo
from signals import registered_detectors
from signals.base import PointInTimeData
from signals.registry import Detector

_log = logging.getLogger("alphadeck.annotate_phantom_arms")

RULE_FAMILY = "d3a-first-knowability"
REASON = "stale_comparative_revenue"
REVOKE_RULE_FAMILY = "operator-revoke"

# Provenance artifacts live beside the backfill's (the repo's gitignored data/ == the container's /data).
_DEFAULT_LOG_DIR = Path(__file__).resolve().parents[2] / "data" / "annotations"


# --- (i) the D3a-OFF view — DIAGNOSTIC ONLY ------------------------------------------------------------


class PreD3aDatingView:
    """DIAGNOSTIC ONLY — never reachable from the cron, the API, or any call-of-record writer.

    A read-through view over a live ``PointInTimeData`` that hands the fundamentals rows back with D3a's
    first-knowability annotation REPLACED by the pre-D3a reading: ``knowable_from`` := the picked version's
    ``valid_from``, ``knowable_accession`` := its ``accession`` — byte-for-byte what ``revenue_acceleration``
    and ``share_creep`` read before #390 (``filed = valid_from``, ``accession = accession``). The pick itself
    is unchanged (D3a never changed which version wins), and every other accessor delegates untouched, so
    the D3a-off and D3a-on assemblies differ in exactly those two keys.

    It lives HERE, in the diagnostic CLI, on purpose: no production module imports this one (pinned by
    ``tests/pipeline/test_annotate_phantom_arms.py``'s import scan), there is no ``CallConfig`` dial and no
    flag on the live funnel, so nothing on the serve / cron / backfill path can switch D3a off. Rows are
    COPIED, never mutated — the shared PIT memo stays the D3a-on truth for assembly (ii).

    Kept deliberately narrow: D3a's tie-break (d) lives in the detector and is NOT reverted — it changes
    only which period a same-date inflection CITES (the label + provenance), never an event date, grade or
    score, so it cannot move a state, verdict, or armed set.
    """

    def __init__(self, pit: PointInTimeData) -> None:
        self._pit = pit

    def __getattr__(self, name: str) -> Any:
        return getattr(self._pit, name)

    def fundamentals_facts(self, security_id: UUID) -> list[dict[str, Any]]:
        return [
            {**r, KNOWABLE_FROM: r["valid_from"], KNOWABLE_SOURCE: r.get("accession")}
            for r in self._pit.fundamentals_facts(security_id)
        ]


# --- the shapes compared ------------------------------------------------------------------------------


@dataclass(frozen=True)
class CallShape:
    """What the classification compares: state, verdict, the headline (``armed_security_id``) and the
    armed-member set."""

    state: str
    verdict: str
    headline: str | None
    armed: frozenset[str]

    @classmethod
    def of_card(cls, card: CallCard) -> CallShape:
        return cls(
            state=card.state.value,
            verdict=card.verdict.value,
            headline=str(card.armed_security_id) if card.armed_security_id else None,
            armed=frozenset(str(m.security_id) for m in card.armed_members),
        )

    def as_json(self, tickers: dict[str, str]) -> dict[str, Any]:
        return {
            "state": self.state,
            "verdict": self.verdict,
            "headline": tickers.get(self.headline, self.headline) if self.headline else None,
            "armed": sorted(tickers.get(s, s) for s in self.armed),
        }


def classify(recorded: CallShape, pre: CallShape, post: CallShape) -> tuple[str, str]:
    """PURE — ``(classification, why)`` for one row from its recorded shape, the D3a-off re-assembly
    (``pre``) and the D3a-on one (``post``). See the module docstring for the four outcomes."""
    if (pre.state, pre.verdict) != (recorded.state, recorded.verdict):
        return (
            "drift",
            f"the D3a-off re-assembly reads {pre.state}/{pre.verdict}, the row recorded "
            f"{recorded.state}/{recorded.verdict} — other drift, not D3a",
        )
    if pre.armed != recorded.armed:
        return (
            "drift",
            f"the D3a-off re-assembly reproduces {recorded.state}/{recorded.verdict} but arms "
            f"{len(pre.armed)} name(s) vs the row's {len(recorded.armed)} "
            f"({len(pre.armed - recorded.armed)} added, {len(recorded.armed - pre.armed)} missing) — "
            "other drift, not D3a",
        )
    if recorded.state == State.ARMED.value and post.state != State.ARMED.value:
        return (
            "exclude",
            f"phantom arm — with first-knowability dating the night reads {post.state}/{post.verdict}",
        )
    if post != pre:
        return "note", "the arm/call stands but D3a changes its composition"
    return "unchanged", ""


def _change_summary(pre: CallShape, post: CallShape, tickers: dict[str, str]) -> str:
    """Backend-authored one-liner for a note's caption: what D3a changes on this night."""

    def t(s: str | None) -> str:
        return tickers.get(s, s[:8]) if s else "none"

    parts: list[str] = []
    if pre.state != post.state:
        parts.append(f"state {pre.state} → {post.state}")
    if pre.verdict != post.verdict:
        parts.append(f"verdict {pre.verdict} → {post.verdict}")
    if pre.headline != post.headline:
        parts.append(f"headline {t(pre.headline)} → {t(post.headline)}")
    gone = sorted(pre.armed - post.armed, key=t)  # by ticker, so the caption reads in order
    new = sorted(post.armed - pre.armed, key=t)
    if gone or new:
        parts.append("armed " + " ".join([f"−{t(s)}" for s in gone] + [f"+{t(s)}" for s in new]))
    return "D3a: " + "; ".join(parts) if parts else "D3a: unchanged"


# --- the stale-trigger evidence -------------------------------------------------------------------------


def fundamentals_detectors(cfg: CallConfig) -> list[Detector]:
    """The registered detectors that read ``fact_fundamentals`` — REGISTRY-DERIVED from each detector's
    declared read horizons (``signals/horizons.py``; ``tests/signals/test_horizons.py`` fails any detector
    that reads a table it did not declare), so a new fundamentals reader joins this evidence automatically.
    """
    return [d for d in registered_detectors() if "fact_fundamentals" in d.horizons(cfg)]


def _event_json(event: SignalEvent | None) -> dict[str, Any] | None:
    """An event's evidence: its date, and the ANCHOR filing it cites — the provenance entry whose
    ``filed`` is the event date (``share_creep`` cites a whole window of points and anchors on the
    newest), else the last entry (``revenue_acceleration`` cites exactly one)."""
    if event is None:
        return None
    srcs = event.provenance
    src = next((p for p in reversed(srcs) if p.detail.get("filed") == event.asof.isoformat()), None)
    if src is None and srcs:
        src = srcs[-1]
    detail = src.detail if src is not None else {}
    return {
        "event_date": event.asof.isoformat(),
        "cited_filing": src.ref if src is not None else None,
        "metric": detail.get("metric"),
        "period_end": detail.get("period_end"),
        "label": event.label,
    }


def stale_events(
    pit: PointInTimeData,
    view: PreD3aDatingView,
    security_ids: Sequence[UUID],
    asof: date,
    cfg: CallConfig,
    tickers: dict[str, str],
) -> list[dict[str, Any]]:
    """Every fundamentals-detector event that DIFFERS between the D3a-off and D3a-on views, per member —
    the evidence behind a phantom arm: the security, the detector, the event the pre-D3a dating produced
    (its date, the filing it cited, the period), what D3a produces instead (``None`` = no event), and the
    period's FIRST filing (the D3a annotation the stale event ignored). Reads only memoized rows."""
    out: list[dict[str, Any]] = []
    for det in fundamentals_detectors(cfg):
        for sid in security_ids:
            pre_e, post_e = det(view, sid, asof, cfg), det(pit, sid, asof, cfg)
            if pre_e == post_e:
                continue
            pre_j = _event_json(pre_e)
            first: dict[str, Any] | None = None
            period, metric = (pre_j or {}).get("period_end"), (pre_j or {}).get("metric")
            if period is not None and metric is not None:
                for r in pit.fundamentals_facts(sid):
                    if str(r.get("period_end")) == str(period) and r.get("metric_key") == metric:
                        first = {
                            "first_knowable": r[KNOWABLE_FROM].isoformat(),
                            "first_filing": r[KNOWABLE_SOURCE],
                            "picked_version_filed": r["valid_from"].isoformat(),
                            "picked_version_filing": r.get("accession"),
                        }
                        break
            out.append(
                {
                    "security_id": str(sid),
                    "ticker": tickers.get(str(sid)),
                    "detector": det.name,
                    "pre_d3a": pre_j,
                    "d3a": _event_json(post_e),
                    "first_knowable": first,
                }
            )
    return out


# --- the per-row run ----------------------------------------------------------------------------------


@dataclass
class RowResult:
    """One honest ``calls`` row, re-assembled twice and classified. ``roster`` names the basket the
    re-assemblies ran on; ``config_matches`` whether the row's stamped ``config_hash`` equals today's
    (None = the row predates the stamp)."""

    call_id: UUID
    seq: int
    thesis_id: UUID
    thesis_name: str
    asof: date
    recorded_at: datetime
    run_kind: str | None
    code_sha: str | None
    config_matches: bool | None
    recorded: CallShape
    roster: str = ""
    # the public-clock cap the row was re-assembled at (the end of its as-of's market day)
    known_at: datetime | None = None
    pre: CallShape | None = None
    post: CallShape | None = None
    classification: str = "error"
    why: str = ""
    summary: str = ""
    stale: list[dict[str, Any]] = field(default_factory=list)
    affected: list[str] = field(default_factory=list)
    error: str | None = None

    def detail(self, tickers: dict[str, str], *, rule: str) -> dict[str, Any]:
        """The annotation's evidence (``call_annotation.detail``) — also the provenance JSON's row."""
        return {
            "rule": rule,
            "classification": self.classification,
            "why": self.why,
            "summary": self.summary,
            "call_seq": self.seq,
            "recorded_at": self.recorded_at.isoformat(),
            "run_kind": self.run_kind,
            "code_sha": self.code_sha,
            "config_matches_current": self.config_matches,
            "clock": "public",
            "known_at": self.known_at.isoformat() if self.known_at else None,
            "roster": self.roster,
            "recorded": self.recorded.as_json(tickers),
            "d3a_off": self.pre.as_json(tickers) if self.pre else None,
            "d3a_on": self.post.as_json(tickers) if self.post else None,
            "affected_security_ids": self.affected,
            "stale_triggers": self.stale,
        }


def _honest_rows(
    conn: psycopg.Connection, thesis_id: UUID | None, asof_from: date | None
) -> list[dict[str, Any]]:
    """Every honest (``NOT reconstructed``) row, thesis by thesis, oldest first — WITHOUT the card body
    (~100 KB each): just what the comparison needs, extracted in SQL (the armed set + the headline).
    """
    where = ["NOT c.reconstructed"]
    params: dict[str, Any] = {}
    if thesis_id is not None:
        where.append("c.thesis_id = %(tid)s")
        params["tid"] = thesis_id
    if asof_from is not None:
        where.append("c.asof >= %(from)s")
        params["from"] = asof_from
    with conn.cursor() as cur:
        cur.execute(
            "SELECT c.id, c.seq, c.thesis_id, t.name AS thesis_name, c.asof, c.state, c.verdict, "
            "c.recorded_at, c.run_kind, c.code_sha, c.config_hash, "
            "c.card->>'armed_security_id' AS headline, "
            "jsonb_path_query_array(c.card, '$.armed_members[*].security_id') AS armed "
            "FROM calls c JOIN thesis t ON t.id = c.thesis_id "
            "WHERE " + " AND ".join(where) + " ORDER BY t.name, c.asof, c.seq",
            params,
        )
        return cur.fetchall()


def _tickers(conn: psycopg.Connection, sids: set[str]) -> dict[str, str]:
    """security id (str) -> ticker, for the printed report and the evidence. Display-only."""
    if not sids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id::text AS id, ticker FROM security_master WHERE id = ANY(%s::uuid[])",
            (sorted(sids),),
        )
        return {r["id"]: r["ticker"] for r in cur.fetchall() if r["ticker"]}


def classify_row(
    conn: psycopg.Connection, row: dict[str, Any], *, cfg: CallConfig, current_hash: str
) -> RowResult:
    """Re-assemble one row twice (D3a off, D3a on) at its own as-of ON THE PUBLIC CLOCK and classify it.
    Never raises: a failure is captured on the result (``classification='error'``) — and is never an
    annotation. Ends its read transaction either way."""
    recorded = CallShape(
        state=row["state"],
        verdict=row["verdict"],
        headline=row["headline"],
        armed=frozenset(str(x) for x in (row["armed"] or [])),
    )
    res = RowResult(
        call_id=row["id"],
        seq=row["seq"],
        thesis_id=row["thesis_id"],
        thesis_name=row["thesis_name"],
        asof=row["asof"],
        recorded_at=row["recorded_at"],
        run_kind=row["run_kind"],
        code_sha=row["code_sha"],
        config_matches=(row["config_hash"] == current_hash) if row["config_hash"] else None,
        recorded=recorded,
    )
    try:
        known_at = res.known_at = known_at_for_asof(res.asof)
        thesis, pit = load_call_inputs(
            conn, res.thesis_id, res.asof, known_at=known_at, cfg=cfg, clock="public"
        )
        taken = thesis_repo.snapshot_taken_at_asof(conn, res.thesis_id, known_at)
        n_members = sum(1 for m in thesis.basket if m.security_id is not None)
        res.roster = (
            f"snapshot taken {taken.astimezone(timezone.utc).isoformat()} ({n_members} resolved names)"
            if taken is not None
            else f"LIVE roster — no snapshot by the as-of ({n_members} resolved names)"
        )
        view = PreD3aDatingView(pit)
        res.post = CallShape.of_card(assemble_from_pit(pit, thesis, res.asof, cfg))
        res.pre = CallShape.of_card(assemble_from_pit(view, thesis, res.asof, cfg))  # type: ignore[arg-type]
        res.classification, res.why = classify(recorded, res.pre, res.post)
        if res.classification in ("exclude", "note"):
            sids = [m.security_id for m in thesis.basket if m.security_id is not None]
            names = (
                {str(s) for s in sids}
                | set(recorded.armed)
                | res.pre.armed
                | res.post.armed
                | {x for x in (res.pre.headline, res.post.headline) if x}
            )
            tickers = _tickers(conn, names)
            if res.classification == "exclude":
                affected = set(recorded.armed)
            else:
                affected = (res.pre.armed ^ res.post.armed) | (
                    {x for x in (res.pre.headline, res.post.headline) if x}
                    if res.pre.headline != res.post.headline
                    else set()
                )
                if res.pre.state != res.post.state or res.pre.verdict != res.post.verdict:
                    affected |= res.pre.armed | res.post.armed
            res.affected = sorted(affected)
            stale = stale_events(pit, view, sids, res.asof, cfg, tickers)
            # the evidence that matters: stale events on the names whose call D3a changes
            res.stale = [s for s in stale if s["security_id"] in affected] or stale
            res.summary = _change_summary(res.pre, res.post, tickers)
    except Exception as exc:  # noqa: BLE001 — one row's failure never aborts the classification
        res.classification = "error"
        res.error = f"{type(exc).__name__}: {exc}"
    finally:
        conn.rollback()  # a read-only transaction per row: end it, hold no snapshot across rows
    return res


def classify_rows(
    conn: psycopg.Connection,
    *,
    thesis_id: UUID | None = None,
    asof_from: date | None = None,
    cfg: CallConfig = DEFAULT_CONFIG,
    progress: Callable[[int, int, RowResult], None] | None = None,
) -> list[RowResult]:
    """Classify every honest row (optionally one thesis / from one as-of on). Read-only."""
    rows = _honest_rows(conn, thesis_id, asof_from)
    conn.rollback()
    current = config_hash(cfg)
    out: list[RowResult] = []
    for i, row in enumerate(rows, start=1):
        res = classify_row(conn, row, cfg=cfg, current_hash=current)
        out.append(res)
        if progress is not None:
            progress(i, len(rows), res)
    return out


# --- apply / revoke -------------------------------------------------------------------------------------


def rule_identity() -> str:
    """``d3a-first-knowability@<image sha>`` — the code identity the annotation records. ``unknown`` when
    the image carries no sha (never fabricated — the 0044 rule)."""
    return f"{RULE_FAMILY}@{get_settings().image_sha or 'unknown'}"


@dataclass
class ApplyOutcome:
    appended: list[tuple[UUID, str, UUID]]  # (annotation id, action, call id)
    skipped_existing: list[tuple[UUID, str]]  # (call id, action) the family already judged
    stale_existing: list[
        tuple[UUID, str, str]
    ]  # (annotation id, action, current class) — reported only


def apply_annotations(
    conn: psycopg.Connection, results: list[RowResult], *, rule: str
) -> ApplyOutcome:
    """Append one annotation per exclude/note result in ONE transaction (the caller commits) — skipping
    any (call, action) this rule FAMILY already judged, active or revoked. Also reports (never revokes)
    an ACTIVE annotation of the family whose row no longer classifies that way today."""
    family = call_annotations_repo.rule_family(rule)
    # active OR revoked: a (call, action) the family ever judged is never judged again (a revoke sticks)
    judged = {
        (a.call_id, a.action)
        for a in call_annotations_repo.all_for_rule_family(conn, family)
        if a.action != "revoke"
    }
    by_call = {r.call_id: r for r in results}
    out = ApplyOutcome(appended=[], skipped_existing=[], stale_existing=[])
    for a in call_annotations_repo.active_for_rule_family(conn, family):
        now = by_call.get(a.call_id)
        if now is not None and now.classification != a.action:
            out.stale_existing.append((a.id, a.action, now.classification))
    for r in results:
        if r.classification not in ("exclude", "note"):
            continue
        if (r.call_id, r.classification) in judged:
            out.skipped_existing.append((r.call_id, r.classification))
            continue
        names = (
            set(r.recorded.armed)
            | (r.pre.armed if r.pre else set())
            | (r.post.armed if r.post else set())
        )
        tickers = _tickers(conn, names)
        aid = call_annotations_repo.append_annotation(
            conn,
            r.call_id,
            action=r.classification,
            reason=REASON,
            rule=rule,
            detail=r.detail(tickers, rule=rule),
        )
        out.appended.append((aid, r.classification, r.call_id))
    return out


def _annotation_table_exists(conn: psycopg.Connection) -> bool:
    """Is migration 0045 applied here? Checked BEFORE a real run classifies (minutes of work) — never
    discover a missing table at the write."""
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('call_annotation') IS NOT NULL AS ok")
        ok = cur.fetchone()["ok"]
    conn.rollback()
    return ok


def _count_ids(conn: psycopg.Connection, ids: list[UUID]) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM call_annotation WHERE id = ANY(%s)", (ids,))
        return cur.fetchone()["n"]


# --- provenance + report --------------------------------------------------------------------------------


def build_payload(
    results: list[RowResult],
    *,
    rule: str,
    dry_run: bool,
    started_at: datetime,
    finished_at: datetime,
    outcome: ApplyOutcome | None,
    tickers: dict[str, str],
) -> dict[str, Any]:
    """The provenance / report payload — PURE (no I/O)."""
    per: dict[str, Counter] = {}
    for r in results:
        per.setdefault(r.thesis_name, Counter())[r.classification] += 1
    return {
        "kind": "annotate_phantom_arms",
        "rule": rule,
        "dry_run": dry_run,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_s": round((finished_at - started_at).total_seconds(), 3),
        "summary": {
            "rows": len(results),
            **{
                k: sum(c[k] for c in per.values())
                for k in ("exclude", "note", "drift", "unchanged", "error")
            },
        },
        "per_thesis": {name: dict(c) for name, c in sorted(per.items())},
        "appended": (
            [
                {"annotation_id": str(a), "action": act, "call_id": str(c)}
                for a, act, c in outcome.appended
            ]
            if outcome
            else []
        ),
        "skipped_existing": (
            [{"call_id": str(c), "action": act} for c, act in outcome.skipped_existing]
            if outcome
            else []
        ),
        "stale_existing": (
            [
                {"annotation_id": str(a), "action": act, "now": now}
                for a, act, now in outcome.stale_existing
            ]
            if outcome
            else []
        ),
        "rows": [
            {
                "call_id": str(r.call_id),
                "thesis_id": str(r.thesis_id),
                "thesis": r.thesis_name,
                "asof": r.asof.isoformat(),
                "error": r.error,
                **r.detail(tickers, rule=rule),
            }
            for r in results
        ],
    }


def write_payload(payload: dict[str, Any], path: Path) -> Path | None:
    """Write the payload as JSON (fail-open: a provenance fault is logged, never a failed run)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return path
    except Exception:  # noqa: BLE001 — fail-open by contract
        _log.exception("annotation provenance write failed (fail-open)")
        return None


def _line(r: RowResult, tickers: dict[str, str]) -> str:
    rec = r.recorded.as_json(tickers)
    base = (
        f"  {r.thesis_name} · asof {r.asof} · seq {r.seq} · recorded {r.recorded_at.isoformat()} · "
        f"{rec['state']}/{rec['verdict']} · armed {','.join(rec['armed']) or '—'}"
    )
    if r.classification == "error":
        return f"{base}\n      ERROR: {r.error}"
    pre = r.pre.as_json(tickers) if r.pre else {}
    post = r.post.as_json(tickers) if r.post else {}
    out = [
        base,
        f"      {r.classification.upper()}: {r.why}",
        f"      D3a off: {pre.get('state')}/{pre.get('verdict')} armed "
        f"{','.join(pre.get('armed', [])) or '—'} · D3a on: {post.get('state')}/{post.get('verdict')} "
        f"armed {','.join(post.get('armed', [])) or '—'}",
        f"      roster: {r.roster} · config matches today: {r.config_matches} · "
        f"code {(r.code_sha or 'NULL')[:7]} · run {r.run_kind or 'NULL'}",
    ]
    for s in r.stale[:6]:
        pre_e, first = s.get("pre_d3a") or {}, s.get("first_knowable") or {}
        out.append(
            f"      stale: {s.get('ticker') or s['security_id'][:8]} {s['detector']} period "
            f"{pre_e.get('period_end')} dated {pre_e.get('event_date')} citing {pre_e.get('cited_filing')}"
            f" · first filed {first.get('first_knowable')} ({first.get('first_filing')})"
            f" · D3a: {'no event' if s.get('d3a') is None else s['d3a'].get('event_date')}"
        )
    if len(r.stale) > 6:
        out.append(f"      … {len(r.stale) - 6} more stale event(s) in the evidence")
    return "\n".join(out)


def report(results: list[RowResult], tickers: dict[str, str]) -> None:
    """Print every candidate (exclude / note), every drift row and every error, then the per-thesis
    summary. Unchanged rows are counted, not listed."""
    for title, cls in (
        ("EXCLUDE (phantom arms)", "exclude"),
        ("NOTE", "note"),
        ("DRIFT (not D3a's — never annotated)", "drift"),
        ("ERROR", "error"),
    ):
        rows = [r for r in results if r.classification == cls]
        print(f"\n=== {title}: {len(rows)} ===")
        for r in rows:
            print(_line(r, tickers))
    per: dict[str, Counter] = {}
    for r in results:
        per.setdefault(r.thesis_name, Counter())[r.classification] += 1
    print("\n=== per thesis ===")
    print(
        f"  {'thesis':<38} {'rows':>5} {'excl':>5} {'note':>5} {'drift':>6} {'same':>5} {'err':>4}"
    )
    for name, c in sorted(per.items()):
        print(
            f"  {name[:38]:<38} {sum(c.values()):>5} {c['exclude']:>5} {c['note']:>5} "
            f"{c['drift']:>6} {c['unchanged']:>5} {c['error']:>4}"
        )
    tot = Counter(r.classification for r in results)
    live = sum(1 for r in results if r.roster.startswith("LIVE"))
    print(
        f"\nTOTAL: {len(results)} honest rows · {tot['exclude']} exclude · {tot['note']} note · "
        f"{tot['drift']} drift · {tot['unchanged']} unchanged · {tot['error']} error · "
        f"{live} re-assembled on the LIVE roster (no snapshot by the as-of)"
    )


# --- the CLI --------------------------------------------------------------------------------------------


def _run_revoke(args: argparse.Namespace) -> None:
    if not args.reason:
        raise SystemExit("--revoke / --revoke-all need --reason (the why is part of the record)")
    rule = f"{REVOKE_RULE_FAMILY}@{get_settings().image_sha or 'unknown'}"
    lock = acquire_pass_lock(holder="annotate") if args.apply else None
    try:
        conn = connect()
        try:
            conn.read_only = not args.apply
            if args.revoke_all:
                ids = [
                    a.id for a in call_annotations_repo.active_for_rule_family(conn, RULE_FAMILY)
                ]
            else:
                ids = [UUID(x) for x in args.revoke]
            print(
                f"revoke: {len(ids)} annotation(s){'' if args.apply else ' · DRY-RUN (nothing written)'}"
            )
            for i in ids:
                print(f"  {i}")
            if not args.apply:
                conn.rollback()
                return
            new = [
                call_annotations_repo.revoke(conn, i, reason=args.reason, rule=rule) for i in ids
            ]
            if _count_ids(conn, new) != len(ids):
                conn.rollback()
                raise SystemExit("ABORTED: revoke count mismatch — ROLLED BACK, nothing written")
            conn.commit()
            print(f"SUMMARY: revoked={len(new)}")
        finally:
            conn.close()
    finally:
        if lock is not None:
            lock.release()


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="D3b: classify every honest call-of-record row by what D3a's first-knowability dating "
        "changes (exclude = phantom arm, note = composition changed, drift = not D3a's) and, with --apply, "
        "append REVERSIBLE annotations (call_annotation). Dry-run by default."
    )
    p.add_argument(
        "--apply", action="store_true", help="write (default: dry-run, read-only, print only)"
    )
    p.add_argument("--thesis", default=None, help="one thesis id (uuid); default = every thesis")
    p.add_argument("--asof-from", default=None, help="only rows with asof >= YYYY-MM-DD")
    p.add_argument("--report-json", default=None, help="write the full evidence JSON to this path")
    p.add_argument("--revoke", nargs="+", default=None, help="revoke these annotation id(s)")
    p.add_argument(
        "--revoke-all", action="store_true", help=f"revoke every active {RULE_FAMILY} annotation"
    )
    p.add_argument("--reason", default=None, help="the why, required for a revoke")
    p.add_argument("--quiet", action="store_true", help="no per-row progress lines")
    args = p.parse_args(argv)

    try:
        if args.revoke or args.revoke_all:
            _run_revoke(args)
            return
        thesis_id = UUID(args.thesis) if args.thesis else None
        asof_from = date.fromisoformat(args.asof_from) if args.asof_from else None
    except PassAlreadyRunning as exc:
        print(
            f"annotate: ALREADY RUNNING — {exc}. Nothing was written (exit {EXIT_ALREADY_RUNNING})"
        )
        raise SystemExit(EXIT_ALREADY_RUNNING) from None
    except ValueError as exc:
        p.error(str(exc))

    rule = rule_identity()
    mode = "APPLY" if args.apply else "DRY-RUN (read-only connection; nothing written)"
    print(f"annotate_phantom_arms · rule={rule} · {mode}")
    started_at = datetime.now(timezone.utc)
    t0 = time.monotonic()

    def progress(i: int, n: int, r: RowResult) -> None:
        if not args.quiet and (i % 25 == 0 or i == n or r.classification in ("exclude", "note")):
            print(
                f"  [{i}/{n} · {time.monotonic() - t0:.0f}s] {r.thesis_name} {r.asof} seq {r.seq}: "
                f"{r.classification}",
                flush=True,
            )

    try:
        lock = acquire_pass_lock(holder="annotate") if args.apply else None
    except PassAlreadyRunning as exc:
        print(
            f"annotate: ALREADY RUNNING — {exc}. Nothing was written (exit {EXIT_ALREADY_RUNNING})"
        )
        raise SystemExit(EXIT_ALREADY_RUNNING) from None
    outcome: ApplyOutcome | None = None
    try:
        conn = connect()
        try:
            conn.read_only = not args.apply  # a dry run PHYSICALLY cannot write
            if args.apply and not _annotation_table_exists(conn):
                raise SystemExit(
                    "annotate: call_annotation does not exist — apply migration 0045 first "
                    "(python -m db.migrate); nothing was written"
                )
            results = classify_rows(
                conn, thesis_id=thesis_id, asof_from=asof_from, progress=progress
            )
            names = {s for r in results for s in r.recorded.armed}
            names |= {s for r in results for sh in (r.pre, r.post) if sh for s in sh.armed}
            tickers = _tickers(conn, names)
            conn.rollback()
            report(results, tickers)
            if args.apply:
                outcome = apply_annotations(conn, results, rule=rule)
                ids = [a for a, _, _ in outcome.appended]
                if ids and _count_ids(conn, ids) != len(ids):
                    conn.rollback()
                    raise SystemExit(
                        "ABORTED: inserted-count mismatch — ROLLED BACK, nothing written"
                    )
                conn.commit()
                print(
                    f"\nAPPLIED: appended {len(outcome.appended)} annotation(s) "
                    f"({sum(1 for _, a, _ in outcome.appended if a == 'exclude')} exclude, "
                    f"{sum(1 for _, a, _ in outcome.appended if a == 'note')} note) · "
                    f"{len(outcome.skipped_existing)} already judged by {RULE_FAMILY} (skipped)"
                )
                for aid, act, now in outcome.stale_existing:
                    print(
                        f"  NOTE: active {act} annotation {aid} now classifies as {now} (left as is)"
                    )
        finally:
            conn.close()
    finally:
        if args.apply and lock is not None:
            lock.release()
    finished_at = datetime.now(timezone.utc)
    print(f"\nruntime: {time.monotonic() - t0:.1f}s for {len(results)} rows")
    payload = None
    if args.apply or args.report_json:
        payload = build_payload(
            results,
            rule=rule,
            dry_run=not args.apply,
            started_at=started_at,
            finished_at=finished_at,
            outcome=outcome,
            tickers=tickers,
        )
    if args.report_json:
        print(f"report: {write_payload(payload, Path(args.report_json))}")
    if args.apply:
        path = _DEFAULT_LOG_DIR / f"{started_at.strftime('%Y%m%dT%H%M%SZ')}.json"
        print(f"provenance: {write_payload(payload, path)}")
    if any(r.classification == "error" for r in results):
        raise SystemExit(1)  # a row that could not be classified is surfaced, never silent


if __name__ == "__main__":
    main()
