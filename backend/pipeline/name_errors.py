"""O1 — the per-NAME ingest errors of a pass, as durable rows instead of a discarded count.

``ingest_thesis`` has always CAPTURED each name's failure (``NameResult.error`` — fail-visible, one bad
name never aborts the others), but the run-of-record kept only ``names_errored`` (a count) and the cron's
printed summary showed nothing at all. So the reasons were gone by morning, and a name that started failing
tonight read exactly like the six that fail every night (MEASURED 2026-10-16: HLLK renamed, GTLS / XOMA /
CPRX / AVNS closed, WONDF never priced).

This module turns a pass's ``NameResult`` list into ``NameIngestError`` rows — one per (name, LEG), because
the legs fail for different reasons and are repaired differently (a price 404 after a rename is not a Form 4
parse error) — and owns the cross-pass key the newly-failing diff uses.

THE DIFF KEY IS ``security_id:leg`` — never the ticker (a ticker can change under a name, and a ticker-less
name must still be able to page) and never the error TEXT (a message that embeds a URL or a retry count
would re-read as "new" every night). A name that has failed on its price leg for weeks and starts failing on
its Form 4 leg too IS news — a different leg, a different repair — so it pages once.

Pure: no DB, no network, no clock. ``pipeline.daily`` does the diff against the previous evaluated artifact
(``cron_run_log.previous_name_errors``), the way the G5a stale-tape diff is done.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:  # keep the layering one-way (no runtime import of the ingest unit)
    from pipeline.ingest_thesis import NameResult

# An error string is stored for the operator to READ, not to grep a stack trace out of: an httpx error can
# carry a long URL + a response body. Long enough for the reason, short enough for a nightly artifact.
MAX_ERROR_CHARS = 300

# The leg a NameResult's error belongs to when the result predates (or bypassed) the structured per-leg
# capture — a hand-built NameResult(error="…") in a test, or any future path that sets only `error`.
UNKNOWN_LEG = "ingest"


@dataclass(frozen=True)
class NameIngestError:
    """One name's failure on one ingest leg tonight. ``leg`` is the leg that raised (``form4`` /
    ``form8k`` / ``schedule13`` / ``price`` / ``fund_shares``, or ``master`` for an id missing from the
    tenant master); ``error`` the captured message, truncated to ``MAX_ERROR_CHARS``."""

    security_id: UUID
    ticker: str | None
    leg: str
    error: str

    @property
    def key(self) -> str:
        """The cross-pass identity (see the module docstring): ``"<security_id>:<leg>"``."""
        return f"{self.security_id}:{self.leg}"

    @property
    def label(self) -> str:
        """The page/report label — the ticker (or the id when ticker-less, never dropped #9) and the leg.
        Never a thesis name: this label rides the health PAGE."""
        return f"{self.ticker or self.security_id} ({self.leg})"


def _clip(s: str) -> str:
    s = " ".join(
        str(s).split()
    )  # one line — a multi-line message must not break the printed report
    return s if len(s) <= MAX_ERROR_CHARS else s[: MAX_ERROR_CHARS - 1] + "…"


def name_errors_of(names: Iterable[NameResult]) -> list[NameIngestError]:
    """Every (name, leg) failure in ``names``, in the order the ingest reported them. A name with the
    structured ``leg_errors`` yields one row per failed leg; a name with only ``error`` set (no leg
    detail) yields ONE row under ``UNKNOWN_LEG`` — reported, never dropped."""
    out: list[NameIngestError] = []
    for n in names:
        legs = tuple(getattr(n, "leg_errors", ()) or ())
        if legs:
            out.extend(
                NameIngestError(n.security_id, n.ticker, str(leg), _clip(msg)) for leg, msg in legs
            )
        elif n.error:
            out.append(NameIngestError(n.security_id, n.ticker, UNKNOWN_LEG, _clip(n.error)))
    return out


def dedup_name_errors(rows: Iterable[NameIngestError]) -> dict[str, NameIngestError]:
    """``key -> row``, first occurrence wins — one dead leg is ONE row even when two theses hold the name
    (the same per-security dedup the stale-tape inventory does)."""
    out: dict[str, NameIngestError] = {}
    for r in rows:
        out.setdefault(r.key, r)
    return out
