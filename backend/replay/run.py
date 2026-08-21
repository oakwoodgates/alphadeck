from __future__ import annotations

import argparse
import os
from collections.abc import Mapping
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID

import psycopg
import pyarrow as pa
import pyarrow.parquet as pq

from db.session import DEFAULT_TENANT_ID, connect
from domain.config import DEFAULT_CONFIG, CallConfig
from replay.episodes import episodes_for
from replay.export import export_snapshot
from replay.harness import replay_all
from replay.metrics import ReplayMetrics, compute_metrics
from replay.pit import connect_mirror
from replay.scoring import RealizedPrices, score_episodes
from repositories import thesis_repo

# --- the lab's detector master switches (one row per switch: everything about it in ONE place) ------
# (config field, CLI flag stem, force-ON env var, --flag help). The `--<stem>` / `--no-<stem>` argparse
# pair and the precedence in `lab_config` are BOTH derived from this table, so a flag name and the
# config field it drives can never drift apart.
_LAB_SWITCHES: tuple[tuple[str, str, str, str], ...] = (
    (
        "breakdown_dearm_enabled",
        "breakdown-dearm",
        "ALPHADECK_BREAKDOWN_DEARM",
        "force the §2.5/§3.3 structural DE-ARM ON (live default: ON since 2026-08-15).",
    ),
    (
        "insider_sell_enabled",
        "insider-sell",
        "ALPHADECK_INSIDER_SELL",
        "force the Band 03 S1 insider-sell cluster RISK detector ON "
        "(live default: ON since 2026-08-19).",
    ),
    (
        "corporate_catalyst_enabled",
        "corporate-catalyst",
        "ALPHADECK_CORPORATE_CATALYST",
        "force the Band 03 S3 8-K item-code CATALYST trigger ON (live default: OFF — still PARKED, "
        "5.02-only since the 1.01 demotion, so a bare run correctly gets it off).",
    ),
    (
        "corporate_risk_enabled",
        "corporate-risk",
        "ALPHADECK_CORPORATE_RISK",
        "force the Band 03 S3 8-K item-code corporate RISK detector ON (live default: ON since "
        "2026-08-17). Two flags (not one) so the lab can measure the trigger and risk sides "
        "independently.",
    ),
    (
        "share_creep_enabled",
        "share-creep",
        "ALPHADECK_SHARE_CREEP",
        "force the Band 03 S4 share-count-creep (ATM detection) RISK detector ON "
        "(live default: ON since 2026-08-19).",
    ),
    (
        "activist_stake_enabled",
        "activist-stake",
        "ALPHADECK_ACTIVIST_STAKE",
        "force the Band 03 S5 SC 13D activist-stake CONVICTION trigger ON "
        "(live default: ON since 2026-08-20).",
    ),
)


def _env_on(name: str, env: Mapping[str, str]) -> bool:
    return env.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def add_switch_args(p: argparse.ArgumentParser) -> None:
    """Declare the `--<stem>` / `--no-<stem>` pair for every lab switch, from the table above."""
    for _field, stem, env_var, help_on in _LAB_SWITCHES:
        p.add_argument(
            f"--{stem}",
            action="store_true",
            help=(
                f"{help_on} Bare runs INHERIT the production default (see --no-{stem} to force it "
                f"off for an off-leg measure). Also forced on by {env_var}=1."
            ),
        )
        p.add_argument(
            f"--no-{stem}",
            action="store_true",
            help=(
                f"force this switch OFF regardless of the live default — the off-leg of an "
                f"off-vs-on measure. Beats --{stem} and {env_var}."
            ),
        )


def lab_config(
    args: argparse.Namespace,
    *,
    base: CallConfig = DEFAULT_CONFIG,
    env: Mapping[str, str] | None = None,
) -> CallConfig:
    """Build the lab's ``CallConfig`` from the parsed switch flags — pure, so it is testable without
    running a replay (``main`` stays thin).

    **The lab INHERITS production** (operator decision "option A", 2026-08-20). Precedence per switch,
    force-OFF > force-ON > inherit:

    1. ``--no-<stem>`` → ``False`` (the explicit off-leg of an off-vs-on measure);
    2. ``--<stem>`` or its env var → ``True``;
    3. otherwise the LIVE default from ``base`` (``DEFAULT_CONFIG``).

    Before this, each switch was an unconditional override (``args.x or _env_on(...)``), so a missing
    flag forced ``False``. Once five of the six switches flipped ON in ``DEFAULT_CONFIG`` (2026-08-15 →
    -08-20) that quietly meant a bare ``python -m replay.run`` backtested a configuration that was NOT
    production — the lab measured a stack with five live detectors disabled. Inheriting is what keeps
    "what the lab measures" and "what prod runs" the same thing by default.

    The force-OFF leg is CLI-ONLY on purpose (no ``ALPHADECK_NO_*``): an env var that silently disables
    a live detector is exactly the footgun this change removes, so turning a detector off for a
    measurement has to be typed on the command line, visibly, in that one run's invocation.
    """
    environ = os.environ if env is None else env
    update: dict[str, bool] = {}
    for field, stem, env_var, _help in _LAB_SWITCHES:
        dest = stem.replace("-", "_")
        if getattr(args, f"no_{dest}", False):
            update[field] = False
        else:
            update[field] = bool(
                getattr(args, dest, False) or _env_on(env_var, environ) or getattr(base, field)
            )
    return base.model_copy(update=update)


def _single_name_security(conn: psycopg.Connection, tenant_id: UUID) -> dict[UUID, UUID]:
    """thesis_id -> its sole member's security_id, for single-name theses (the unit the withheld-arm
    metric can price). Multi-name themes are omitted."""
    out: dict[UUID, UUID] = {}
    for t in thesis_repo.list_all(conn):
        sids = [m.security_id for m in t.basket if m.security_id is not None]
        if len(sids) == 1:
            out[t.id] = sids[0]
    return out


def _write_parquet(path: Path, rows: list[dict]) -> None:
    if rows:  # all-five tables are populated on the seed; skip writing an empty artifact
        pq.write_table(pa.Table.from_pylist(rows), path)


def run(
    conn: psycopg.Connection,
    *,
    start: date,
    end: date,
    pin: datetime,
    out_dir: str | Path,
    cfg: CallConfig = DEFAULT_CONFIG,
    tenant_id: UUID = DEFAULT_TENANT_ID,
) -> ReplayMetrics:
    """The full instrument: export the SoR to the Parquet mirror, replay every thesis day-by-day over
    ``[start, end]`` at ``known_at=pin`` (the determinism pin), derive arm episodes, score them against
    realized forward prices, and aggregate the metric set. Writes ``outcomes.parquet`` / ``episodes.parquet``
    (DuckDB-queryable) + ``metrics.json`` (the readable summary) to ``out_dir``. Deterministic for a given
    ``(snapshot, pin, window, cfg)`` — no clock/random in the loop. Returns the metrics."""
    out = Path(out_dir)
    export_snapshot(conn, out, tenant_id=tenant_id)
    con = connect_mirror(out)
    try:
        timeline = replay_all(
            conn, con, start=start, end=end, known_at=pin, cfg=cfg, tenant_id=tenant_id
        )
        episodes = episodes_for(timeline)
        realized = RealizedPrices(con, tenant_id=tenant_id)
        outcomes = score_episodes(episodes, realized)
        metrics = compute_metrics(
            outcomes,
            timeline=timeline,
            realized=realized,
            single_name_security=_single_name_security(conn, tenant_id),
        )
        _write_parquet(out / "outcomes.parquet", [o.model_dump(mode="json") for o in outcomes])
        _write_parquet(out / "episodes.parquet", [e.model_dump(mode="json") for e in episodes])
        (out / "metrics.json").write_text(
            metrics.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        return metrics
    finally:
        con.close()


def main() -> None:
    p = argparse.ArgumentParser(
        description="Alpha Deck replay/backtest harness (Phase 1, the instrument)."
    )
    p.add_argument("--start", required=True, help="window start, YYYY-MM-DD")
    p.add_argument("--end", required=True, help="window end, YYYY-MM-DD")
    p.add_argument("--pin", required=True, help="the known_at determinism pin, ISO timestamp")
    p.add_argument(
        "--out", required=True, help="output dir for the Parquet mirror + outcomes + metrics"
    )
    add_switch_args(p)
    args = p.parse_args()
    pin = datetime.fromisoformat(args.pin)
    if pin.tzinfo is None:  # the recorded_at axis is tz-aware; assume UTC for a bare timestamp
        pin = pin.replace(tzinfo=timezone.utc)

    # One explicit cfg rides into the whole replay (prod/other callers untouched). A bare run now
    # INHERITS the production defaults — see lab_config for the precedence and why.
    cfg = lab_config(args)
    conn = connect()
    try:
        metrics = run(
            conn,
            start=date.fromisoformat(args.start),
            end=date.fromisoformat(args.end),
            pin=pin,
            out_dir=args.out,
            cfg=cfg,
        )
        print(metrics.model_dump_json(indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
