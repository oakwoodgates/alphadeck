"""The horizon registry — where the point-in-time view's read BOUNDS come from (Board/Cockpit perf PR-1b).

A bounded fact table is prefetched with an event-time floor (``valid_from >= asof - bound``) so a
196-name thesis stops materializing every bar and every Form 4 a name ever had. A floor that rested on a
comment ("the detectors provably never look past asof - 210 d") is the "next detector forgets it" trap
the EDGAR-TTL postmortem already paid for: a future reader, or a widened dial, that reads past the floor
would silently TRUNCATE rows and change a verdict. So the floor is never hand-typed:

- every reader of a bounded table DECLARES its max horizon per table — detectors from the SAME
  ``CallConfig`` the assembler runs with (``Detector.horizons(cfg)``), display members from their module
  ``LOOKBACK_DAYS`` (``DisplayMember.horizons``), the thesis-level readers via a module ``HORIZONS``;
- the PIT's bound for a table = ``max(declared) + MARGIN_DAYS``; a reader declaring ``None`` makes the
  table UNBOUNDED for that PIT; a table no reader declares stays unbounded;
- a reader with NO declaration fails a TEST (``tests/signals/test_horizons.py``), never a read.

Only ``BOUNDED_TABLES`` are ever floored. Every other table stays unbounded whatever is declared (a
numeric declaration on an unbounded table is inert — it documents the reader's need, it never truncates).
The derived numbers are pinned in the registry test so a dial change is review-visible.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from db.bitemporal import _FACT_IDENTITY
from domain.config import CallConfig
from signals import laggard, registered_detectors, theme_conviction
from signals.display import registered_display_members, relative_strength, theme_breadth

# Slack ABOVE the max declared horizon. Why a margin exists at all: the dials are CALENDAR days counted
# back from asof, while a reader's true need is expressed in the tape's BAR dates (a lookback window's
# first bar, a cluster window's oldest buy) — the floor must sit comfortably before any bar a reader can
# legitimately ask for, never on top of it. A named constant so the slack is one review-visible number.
MARGIN_DAYS = 30

# The tables a PIT may floor. Every other fact table is read unbounded regardless of declarations.
BOUNDED_TABLES: frozenset[str] = frozenset({"fact_price_eod", "fact_insider_txn"})


def derive_bounds(declarations: Iterable[Mapping[str, int | None]]) -> dict[str, int | None]:
    """The PIT read bound per bounded table from a set of reader declarations (pure).

    For each table in ``BOUNDED_TABLES``: ``None`` (unbounded) when no reader declares it or ANY reader
    declares ``None``; else ``max(declared days) + MARGIN_DAYS``. An unknown table name in a
    declaration fails loud (a typo must never quietly leave a table unbounded)."""
    decls = list(declarations)
    for d in decls:
        for table in d:
            if table not in _FACT_IDENTITY:
                raise ValueError(f"horizon declared for an unknown fact table: {table!r}")
    out: dict[str, int | None] = {}
    for table in sorted(BOUNDED_TABLES):
        seen = [d[table] for d in decls if table in d]
        if not seen or any(v is None for v in seen):
            out[table] = None
        else:
            out[table] = max(seen) + MARGIN_DAYS
    return out


def call_horizons(cfg: CallConfig) -> list[Mapping[str, int | None]]:
    """Every reader the CALL PIT serves: the registered detectors + the two thesis-level readers
    (``laggard``, ``theme_conviction``), each evaluated against the cfg the assembler will run with.
    """
    return [d.horizons(cfg) for d in registered_detectors()] + [
        laggard.HORIZONS(cfg),
        theme_conviction.HORIZONS(cfg),
    ]


def call_bounds(cfg: CallConfig) -> dict[str, int | None]:
    """The CALL PIT's read bounds (``pipeline.call_for_thesis``) — derived, never hand-typed."""
    return derive_bounds(call_horizons(cfg))


def display_horizons() -> list[Mapping[str, int | None]]:
    """Every reader the DISPLAY PIT serves: the registered display members + the thesis-level
    ``theme_breadth`` and the supersector RS rollup."""
    return [m.horizons for m in registered_display_members()] + [
        theme_breadth.HORIZONS,
        relative_strength.SECTOR_RS_HORIZONS,
    ]


def display_bounds() -> dict[str, int | None]:
    """The DISPLAY PIT's read bounds (the ``/display-signals`` route) — derived, never hand-typed."""
    return derive_bounds(display_horizons())


INSIDER_TABLE = "fact_insider_txn"


def insider_signal_codes(cfg: CallConfig) -> dict[str, int | None]:
    """R8-A — ``{Form 4 transaction code: the furthest-back days before asof any CALL detector reading that code
    can use}``, DERIVED from the registry: every registered detector that declares ``fact_insider_txn`` in its
    horizons contributes its declared ``txn_codes`` with that horizon (the max per code; ``None`` = unbounded
    wins). The keys are the amendment classifier's signal code set (``domain.insider_amendments`` — a 4/A that
    changes no row of these codes has no call effect and resolves itself); the values set the "in tonight's
    window" urgency. A detector that reads the table without declaring its codes is a registry error, raised
    here and failed by ``tests/signals/test_insider_signal_codes.py`` — never a silently narrower set.
    """
    out: dict[str, int | None] = {}
    for det in registered_detectors():
        h = det.horizons(cfg)
        if INSIDER_TABLE not in h:
            continue
        if not det.txn_codes:
            raise ValueError(
                f"detector {det.name!r} reads {INSIDER_TABLE} but declares no txn_codes — the amendment "
                "classifier cannot know which rows it reads"
            )
        days = h[INSIDER_TABLE]
        for code in det.txn_codes:
            if code in out and out[code] is None:
                continue
            prior = out.get(code)
            out[code] = None if days is None else max(days, prior or 0)
    return out
