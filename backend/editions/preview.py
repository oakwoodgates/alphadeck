"""THE PREVIEW — how an edition's re-run differs from the original record (Edition 0), before it is published.

Compares, per thesis and in total:

- **calls (arm episodes)** over the RECORD's own span (from its first honest night), matched on
  ``(company, arm date)`` — how many the record made, how many the re-run makes, how many are the same call;
- **thesis-nights** both have, and on how many the ARMED SET differs;
- **scores** — finished episodes (window ended by ``end``) on each side and their median forward return, on
  today's stored prices.

Edition 0 is read exactly as the Scoreboard reads it today (``scoreboard.record.thesis_timeline``, the
default reconstructed / annotated / late policies). Every scored episode is listed too, so a reader can slice
it (market-cap tier, horizon, benchmark). Read-only; no LLM.
"""

from __future__ import annotations

from datetime import date
from statistics import median
from typing import Any
from uuid import UUID

import psycopg

from domain.call import CallCard
from replay.episodes import derive_episodes
from replay.schema import CallSnapshot, Episode
from replay.scoring import score_episode
from repositories import thesis_repo
from scoreboard.prices import PgRealizedPrices
from scoreboard.record import thesis_timeline


def _armed(s: CallSnapshot) -> frozenset[UUID]:
    return frozenset(m.security_id for m in s.members if m.tier == "armed")


def _scored(eps: list[Episode], prices: PgRealizedPrices, end: date) -> list[dict[str, Any]]:
    """Every episode with its outcome. ``finished`` = its window ended by ``end`` and prices cover it."""
    out = []
    for ep in eps:
        row: dict[str, Any] = {
            "security_id": str(ep.security_id),
            "arm_date": ep.arm_date.isoformat(),
            "exit_by": ep.exit_by.isoformat() if ep.exit_by else None,
            "key1_source": ep.key1_source,
            "finished": False,
            "forward_return": None,
            "entry_close": None,
            "exit_date": None,
        }
        if ep.exit_by is not None and ep.exit_by <= end:
            o = score_episode(ep, prices)
            if not o.insufficient_prices and o.forward_return is not None:
                row.update(
                    finished=True,
                    forward_return=o.forward_return,
                    entry_close=o.entry_close,
                    exit_date=o.exit_date.isoformat() if o.exit_date else None,
                )
        out.append(row)
    return out


def _returns(rows: list[dict[str, Any]]) -> list[float]:
    return [r["forward_return"] for r in rows if r["finished"]]


def compare_to_record(
    conn: psycopg.Connection, cards: dict[UUID, list[CallCard]], *, end: date
) -> dict[str, Any]:
    names = {t.id: t.name for t in thesis_repo.list_all(conn, include_archived=True)}
    conn.commit()
    rows: list[dict[str, Any]] = []
    tot = dict.fromkeys(
        ("rec_eps", "ed_eps", "same_eps", "rec_nights", "ed_nights", "common", "armed_differs"), 0
    )
    rec_rets: list[float] = []
    ed_rets: list[float] = []
    for tid, tcards in cards.items():
        rec, _ = thesis_timeline(conn, tid, end)
        conn.commit()
        ed = [CallSnapshot.from_card(c) for c in tcards if c.asof <= end]
        if not rec and not ed:
            continue
        rec_days = {s.asof: s for s in rec}
        ed_days = {s.asof: s for s in ed}
        span0 = min(rec_days) if rec_days else end
        e0 = derive_episodes(rec)
        e1_all = derive_episodes(ed)
        e1 = [e for e in e1_all if e.arm_date >= span0]
        k0 = {(e.security_id, e.arm_date) for e in e0}
        k1 = {(e.security_id, e.arm_date) for e in e1}
        common = sorted(set(rec_days) & set(ed_days))
        differs = [d for d in common if _armed(rec_days[d]) != _armed(ed_days[d])]
        thesis = thesis_repo.get(conn, tid)
        conn.commit()
        tenant = thesis.tenant_id if thesis else None
        prices = PgRealizedPrices(conn, tenant_id=tenant, cap=end)
        s0, s1 = _scored(e0, prices, end), _scored(e1_all, prices, end)
        conn.commit()
        r0 = _returns(s0)
        r1 = _returns([r for r, e in zip(s1, e1_all) if e.arm_date >= span0])
        rec_rets += r0
        ed_rets += r1
        row = {
            "thesis_id": str(tid),
            "name": names.get(tid, str(tid)),
            "first_record_night": min(rec_days).isoformat() if rec_days else None,
            "first_edition_night": min(ed_days).isoformat() if ed_days else None,
            "rec_eps": len(k0),
            "ed_eps": len(k1),
            "ed_eps_all": len(e1_all),
            "same_eps": len(k0 & k1),
            "rec_nights": len(rec_days),
            "ed_nights": len(ed_days),
            "common": len(common),
            "armed_differs": len(differs),
            "armed_differs_nights": [d.isoformat() for d in differs],
            "ed_only_nights": len(set(ed_days) - set(rec_days)),
            "rec_only_nights": len(set(rec_days) - set(ed_days)),
            "rec_matured": len(r0),
            "ed_matured": len(r1),
            "rec_median_return": median(r0) if r0 else None,
            "ed_median_return": median(r1) if r1 else None,
            "record_episodes": s0,
            "edition_episodes": s1,
            "record_span_start": span0.isoformat(),
        }
        rows.append(row)
        for k in tot:
            tot[k] += row[k]
    tot["rec_matured"], tot["ed_matured"] = len(rec_rets), len(ed_rets)
    tot["rec_median_return"] = median(rec_rets) if rec_rets else None
    tot["ed_median_return"] = median(ed_rets) if ed_rets else None
    return {"end": end.isoformat(), "totals": tot, "theses": rows}


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{100 * x:+.1f}%"


def render(preview: dict[str, Any]) -> str:
    """The plain-English report (ASCII only: it is printed on a Windows console too)."""
    t = preview["totals"]
    lines = [
        f"PREVIEW - the re-run (Edition 1) vs the original record (Edition 0), through {preview['end']}",
        "",
        f"Calls the record made (from its first night): {t['rec_eps']}. Calls the re-run makes over the same"
        f" nights: {t['ed_eps']}. The same call (same company, same arm date): {t['same_eps']}.",
        f"Thesis-nights in both: {t['common']}; the armed set differs on {t['armed_differs']}"
        + (f" ({100 * t['armed_differs'] / t['common']:.0f}%)." if t["common"] else "."),
        f"The re-run also covers {t['ed_nights'] - t['common']} thesis-nights the record never recorded"
        " (before it began, or nights it missed).",
        f"Finished calls (window ended): record {t['rec_matured']}, median return {_pct(t['rec_median_return'])};"
        f" re-run {t['ed_matured']}, median return {_pct(t['ed_median_return'])} (today's stored prices).",
        "",
        "Per thesis (record calls -> re-run calls, same; armed set differs / nights both have):",
    ]
    for r in sorted(preview["theses"], key=lambda r: -r["ed_eps"]):
        lines.append(
            f"  {r['name'][:32]:32} {r['rec_eps']:4} -> {r['ed_eps']:4}, same {r['same_eps']:4}; "
            f"differs {r['armed_differs']:3}/{r['common']:3}; first night record "
            f"{r['first_record_night'] or '-'} / re-run {r['first_edition_night'] or '-'}"
        )
    return "\n".join(lines)
