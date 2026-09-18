"""The run MANIFEST — what a backtest run says about itself.

A result nobody can reproduce is an anecdote. The manifest is the difference: it carries the code, the
dials, the window, the clock, the pin, the rosters and the exclusions that produced the episodes sitting
beside it, so a number quoted six months from now can be traced to the run that made it — and so promoting
a dial to ``config.py`` can cite run ids instead of a memory.

**The fingerprint is the SHIPPED helper, never a local re-derivation.** ``domain.config.config_hash`` walks
the config canonically (sets sorted, list order kept, dict keys sorted, enums as wire values) precisely
because a ``model_dump`` one-liner hashes DIFFERENTLY IN EVERY PROCESS — ``CallConfig`` carries three
``frozenset`` dials whose iteration order follows ``PYTHONHASHSEED``. That was measured on the record side
before it shipped; reproducing the walk here by hand would reintroduce it. So: import the helper, and store
the exact blob it hashed, so a reader can re-verify the digest with nothing but ``sha256`` — no repo, no
Pydantic, no walk.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from domain.config import CallConfig, _canonical_config, config_hash, short_hash
from domain.settings import get_settings

SCHEMA_VERSION = 1
MANIFEST_NAME = "manifest.json"

# The five permanent labels every backtest surface carries (operator decision Q7, all five). Backend-
# authored strings — the front end renders them, it never composes them (the ``ingest_note`` precedent),
# so the caveat cannot drift between the artifact and the page.
LABELS: tuple[str, ...] = (
    "public clock — facts enter when they became public, not when this system ingested them",
    "counterfactual universe — baskets were authored in 2026 over names that had already moved; "
    "operator-ratified facts are modeled as public on their announcement date, and the ratification "
    "itself is 2026 hindsight",
    "survivorship — the baskets hold names that were alive to be added in 2026",
    "adjusted closes — split and dividend factors known later are baked into every bar",
    "recompute, not the record — these calls were never logged, notified, or acted on",
)


def resolve_code_sha() -> str | None:
    """The git SHA of the code that ran, or ``None`` when it genuinely cannot be determined.

    ``Settings.image_sha`` is the sha BAKED INTO THE IMAGE, and its docstring is explicit that on a
    bind-mounted tier the running code is the worktree's and the value can lag. A backtest is normally
    kicked from a HOST venv where that variable is unset, so fall through to the worktree's own HEAD, which
    there IS the running code. ``None`` when neither answers — never a fabricated value, the same rule the
    ``calls`` row's stamp holds (``NULL`` when unknown, never invented)."""
    baked = get_settings().image_sha
    if baked:
        return baked
    try:
        out = subprocess.run(  # noqa: S603,S607 — fixed argv, no shell, no caller input
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    sha = out.stdout.strip()
    return sha if out.returncode == 0 and sha else None


class TableCounts(BaseModel):
    """One fact table's accounting through the export. Four numbers because they answer four different
    questions, and collapsing them would hide the one that matters.

    ``rows_dropped_null_clock`` counts RAW rows the public clock could not date; ``identities_lost`` counts
    whole FACTS that therefore vanished. A 47% raw-row drop with 0.67% identities lost is a healthy export
    (the dropped rows were superseded versions); a small row drop with a large identity loss is a hole in
    the tape. ``versions_collapsed`` is how many re-versions the export resolved before writing."""

    rows_in: int
    rows_out: int
    rows_dropped_null_clock: int = 0
    versions_collapsed: int = 0
    identities_lost: int = 0


class MirrorInfo(BaseModel):
    """The frozen Parquet mirror this run swept, and what it does NOT contain.

    ``excluded_tables`` + ``blind_detectors`` are a pair on purpose: naming a table that was left out is
    only half an answer, because the reader still has to work out what stopped firing. Saying both means a
    result can never be read as "that detector is inert on this tape" when the truth is "that detector had
    no tape"."""

    hash: str
    tables: dict[str, TableCounts] = Field(default_factory=dict)
    excluded_tables: list[str] = Field(default_factory=list)
    blind_detectors: list[str] = Field(default_factory=list)


class ThesisEntry(BaseModel):
    """One thesis's identity in the run — including WHOSE roster it replayed on.

    ``roster_source`` / ``fallback_days`` / ``total_days`` come straight off the harness's ``RosterSource``
    and are the honest half: ``basket_snapshot`` history begins 2026-09-15, so for any window before that
    every thesis reads ``live_fallback`` and its membership is a labeled counterfactual. ``roster_hash``
    fingerprints the roster the run STARTED from (the live basket, ordered by ordinal then security) — read
    together with ``roster_source`` it says both which names and how point-in-time they were."""

    thesis_id: UUID
    name: str
    basket_size: int
    roster_hash: str
    roster_source: str  # "snapshot" | "live_fallback" | "no_sessions"
    fallback_days: int
    total_days: int


class BacktestManifest(BaseModel):
    """Everything needed to reproduce, or to refuse to trust, one run.

    Nothing here is optional-by-accident. ``hypothesis`` and ``decision_rule`` are nullable only because a
    bare exploratory run at the default config is a legitimate thing to do; the CLI REQUIRES both the moment
    an overlay is supplied, so no run that moved a dial can exist without its pre-registration."""

    schema_version: int = SCHEMA_VERSION
    run_id: str
    # The PASS this run belongs to — one id shared by every run of one curve (S1). ``None`` for a run
    # launched on its own, which is an honest answer rather than a missing one: a lone run IS its own
    # evidence, while a point of a curve is only readable beside its siblings.
    pass_id: str | None = None
    created_at: str
    code_sha: str | None = None

    window_start: date
    window_end: date
    # WHICH CLOCK the facts entered on: "record" = recorded_at, what this system held (the Scoreboard's
    # axis); "public" = when anyone could have known (B2). They must never be pooled.
    clock: Literal["record", "public"] = "record"
    # HOW the transaction-axis cap moved across the sweep: "pin" = one run-wide known_at (the determinism
    # pin); "lockstep" = per-session known_at_for_asof(T, now=pin), so the fact axis tracks the roster axis.
    known_at_mode: Literal["pin", "lockstep"] = "pin"
    pin: str

    config_hash: str
    config_short: str
    # The EXACT bytes `config_hash` was computed over. Stored so the digest can be re-verified with nothing
    # but a sha256 of this string — no repo, no Pydantic, no canonical walk.
    config_canonical_json: str
    # The same content, parsed, because a human reading manifest.json should be able to see the dials. A
    # test pins the two together so they cannot drift.
    config: dict[str, Any] = Field(default_factory=dict)
    overlay_diff: dict[str, dict[str, Any]] = Field(default_factory=dict)
    overlay_path: str | None = None

    theses: list[ThesisEntry] = Field(default_factory=list)
    mirror: MirrorInfo

    # HOW the run was executed (B5b). Not a dial -- it must not change a result, and a test pins that --
    # but recorded so a timing in this manifest can be read against the shape that produced it.
    workers: int = 1
    # THE NULLS' OWN IDENTITY (B4). A null model is EVIDENCE, so it has to be re-derivable from the
    # manifest alone: K draws per episode from this seed, with each episode drawing from a sub-seed of
    # (seed, thesis, security, arm_date) so that adding an episode never reshuffles the others.
    null_draws: int = 0
    null_seed: str = ""

    hypothesis: str | None = None
    decision_rule: str | None = None
    regime: str | None = None
    labels: list[str] = Field(default_factory=lambda: list(LABELS))
    timings: dict[str, float] = Field(default_factory=dict)
    n_episodes: int = 0
    n_theses: int = 0


def canonical_config_blob(cfg: CallConfig) -> str:
    """The exact string ``domain.config.config_hash`` hashes. Reproduced from the SAME canonical walk, so
    the two cannot disagree; a test asserts ``sha256(blob) == config_hash(cfg)``."""
    return json.dumps(_canonical_config(cfg), sort_keys=True, separators=(",", ":"))


def roster_hash(security_ids: list[UUID | None]) -> str:
    """Fingerprint one thesis's roster. Ordered as the basket is ordered (ordinal), because a REORDERED
    basket is a different roster to the ranking, and ``None`` members (a row with no resolved security)
    render as a literal so a basket that loses a resolution is visibly a different roster rather than
    silently the same one."""
    payload = json.dumps([str(s) if s is not None else None for s in security_ids])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def mirror_hash(mirror_dir: str | Path) -> str:
    """Fingerprint the frozen mirror: sha256 over the sorted ``(filename, size, sha256(bytes))`` of every
    Parquet file in the run's mirror. Content-addressed, so two runs quoting the same mirror hash swept
    provably the same facts — the thing that makes a config delta attributable to the config."""
    entries = []
    for path in sorted(Path(mirror_dir).glob("*.parquet")):
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
        entries.append((path.name, path.stat().st_size, digest.hexdigest()))
    return hashlib.sha256(json.dumps(entries).encode("utf-8")).hexdigest()


def slugify(text: str | None, *, fallback: str = "default", limit: int = 24) -> str:
    """A short, filesystem-safe token for the run id — lowercase, ascii-alphanumeric, dash-separated."""
    if not text:
        return fallback
    kept = [c.lower() if c.isalnum() else "-" for c in text]
    slug = "-".join(part for part in "".join(kept).split("-") if part)[:limit].strip("-")
    return slug or fallback


#: What a run id is made of, in order — named once so the error a COLLISION raises can say which parts
#: had to agree for it to happen (``store.create_run_dir``), rather than leaving a reader to infer the
#: composition from a dashed string.
RUN_ID_PARTS: tuple[str, ...] = (
    "utc timestamp (to the second)",
    "clock",
    "window start",
    "hypothesis slug",
    "config short hash",
)


def make_run_id(
    cfg: CallConfig,
    *,
    hypothesis: str | None,
    now: datetime | None = None,
    clock: str = "record",
    window_start: date | None = None,
) -> str:
    """``<utc timestamp>-<clock>-<window start>-<hypothesis slug>-<config short hash>`` — see
    ``RUN_ID_PARTS``.

    Sortable first (so a directory listing is a timeline), then legible (which axis, which window, which
    experiment), then precise (which dials). The timestamp is what guarantees a re-run is a NEW run rather
    than a silent overwrite — two runs of identical inputs are two trials, and counting trials is the
    point.

    The CLOCK is a component because it is a component of the EXPERIMENT (CW): the same grid under the same
    hypothesis on the record clock and on the public clock is two different measurements, and they are
    routinely run back to back. Without it those two collide inside one second — the timestamp's resolution
    — and the second one dies on ``create_run_dir``.

    The WINDOW START is a component for exactly the same reason, one slice later (S1): once the unit of
    work is a sub-window, ONE curve point is N runs that differ ONLY by window, and a tiling pass launches
    them concurrently — so they land in the same second by construction. It is the START alone, not the
    whole span: a pass tiles DISJOINT windows, so starts are unique within it; the manifest carries both
    ends authoritatively; and the residual case (same start, different length, same second, same dials)
    still raises a legible ``RunDirExists`` naming every component. Omitted (``None``) it is absent from
    the id entirely, so a run launched without a window concept reads exactly as it did before.

    **TESTS: two ``execute()`` calls with identical inputs inside ONE second is reachable, and became so
    after M1** — a seed-sized run now finishes in well under a second, so a test that runs the same thing
    twice on one root collides on ``create_run_dir``. It is not flaky, it is timing-dependent, which is
    worse: it passed on a slow Windows box and failed on CI. Pass distinct ``now=`` values (this function
    composes the id from that parameter), or a distinct ``hypothesis`` where the test's point IS the
    timestamp."""
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    window = f"w{window_start.strftime('%Y%m%d')}-" if window_start is not None else ""
    return f"{stamp}-{slugify(clock)}-{window}{slugify(hypothesis)}-{short_hash(config_hash(cfg))}"


def make_pass_id(
    *, hypothesis: str | None, now: datetime | None = None, clock: str = "record"
) -> str:
    """The id every run of ONE curve shares — ``<utc timestamp>-<clock>-<hypothesis slug>``.

    Deliberately carries NO config hash: a pass spans configs, that being what a curve is. It exists
    because once a point is N window runs, the only record of "these 75 runs are one curve" was
    ``sweep.json`` — which is latest-only and overwritten by the next sweep (a known gap this slice leans
    on much harder). With the pass id on every manifest and every registry row, the grouping survives in
    the artifacts themselves, and the registry can answer it without the curve file."""
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{slugify(clock)}-{slugify(hypothesis)}"


def write_manifest(run_dir: str | Path, manifest: BacktestManifest) -> Path:
    """Write ``manifest.json`` into the run directory."""
    path = Path(run_dir) / MANIFEST_NAME
    path.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
    return path


def read_manifest(run_dir: str | Path) -> BacktestManifest | None:
    """Read a run's manifest, or ``None`` when it is absent or unreadable.

    Absence, not an exception: a half-written run directory is a run that did not finish, and the registry
    and (later) the route both need to skip it quietly rather than fail the whole listing."""
    path = Path(run_dir) / MANIFEST_NAME
    if not path.is_file():
        return None
    try:
        return BacktestManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — an unreadable manifest is absence, not an outage
        return None


def verify_config_hash(manifest: BacktestManifest) -> bool:
    """Re-verify a manifest's fingerprint from the manifest ALONE — the property the stored blob exists for.
    Needs no ``CallConfig``, no canonical walk, and no repo."""
    recomputed = hashlib.sha256(manifest.config_canonical_json.encode("utf-8")).hexdigest()
    return recomputed == manifest.config_hash
