"""Held insider rows (``insider_txn_hold``, migration 0082) — the WORDS the operator is told them in.

A Form 4 / Form 4/A row whose transaction date cannot be true is held by the ingest instead of being stored as
a fact (``ingest.edgar.form4.write_hold``). The nightly counts the hold table and names each NEW held row once
(``pipeline/insider_hold_scan.py``). This module is the one place that wording is built, so the health page
(``notify.HealthEvent.label``), the Admin run row (``app/routers/admin.py::_problems``) and the printed report
cannot drift. Pure: no I/O, no clock.

Display only. A held row is not a fact, never a call input, and no corrected date is invented for it (#3).
"""

from __future__ import annotations

from collections.abc import Sequence

# The most held rows one page NAMES; the rest are counted (the Form 4/A page's cap, the same number).
PAGE_NAME_CAP = 8


def _shares(shares: float | int | None) -> str:
    if shares is None:
        return "? sh"
    try:
        n = float(shares)
    except (TypeError, ValueError):
        return f"{shares} sh"
    return f"{n:,.0f} sh" if n == int(n) else f"{n:,.4f}".rstrip("0").rstrip(".") + " sh"


def hold_label(
    *,
    ticker: str | None,
    security_id: str,
    accession: str,
    form: str,
    code: str | None,
    shares: float | int | None,
) -> str:
    """One held row as the page names it: the ticker (else the security id's first 8 characters), the
    accession, the transaction code and the shares — ``"ABCD 0000000000-24-000001 (A, 22,500 sh)"``; a row of an
    amendment says so (``"... 4/A (S, 100 sh)"``)."""
    who = ticker or security_id[:8]
    amended = " 4/A" if form == "4/A" else ""
    return f"{who} {accession}{amended} ({code or '?'}, {_shares(shares)})"


def page_names(named: Sequence[str], more: int) -> str:
    """The named rows, then the count of the rest — ``"A, B (+3 more in the run artifact)"``."""
    shown = ", ".join(named)
    if not more:
        return shown
    tail = f"+{more} more in the run artifact"
    return f"{shown} ({tail})" if shown else tail


def page_bit(named: Sequence[str], more: int) -> str:
    """The health line for NEW held rows, without its closing note (each surface adds its own: the page says
    "not a cron error", the Admin run row its benign marker)."""
    n = len(named) + more
    return (
        f"{n} insider row(s) held because the filing's transaction date cannot be true: "
        f"{page_names(named, more)} — held, not stored as a fact, and no date is invented"
    )
