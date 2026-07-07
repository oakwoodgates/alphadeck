from __future__ import annotations

import json
import re
from pathlib import Path

from domain.settings import get_settings
from ingest import CacheMiss

# Runtime cache lives under the repo's gitignored data/; tests pass a fixtures dir instead.
_DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "data" / "sec_cache"

# --- the canonical-primary rank (the multi-sibling CIK rule) ---------------------------------------------
#
# A CIK often carries SEVERAL master rows (dual-class pairs, US-ADR vs foreign-ordinary, warrants/units,
# preferred lines). Exactly ONE of them is the instrument the operator actually trades — everything downstream
# (the resolved security_id, the shown ticker, the price cache, the position MONITOR tracks) anchors to it, so
# the pick must be CORRECT and STABLE, never arbitrary.
#
# THE EMPIRICAL GATE (2026-07-07, all 1,476 multi-row CIKs in the live file): "SEC file order is primary-first"
# is usually-right, NOT the rule — 51 violations, including the exact dangerous class (DEVSF/OTC listed before
# DEVS/Nasdaq; ORISF before ORIS; SNBRQ before SNBR), units/warrants listed first on the same venue
# (GPATU→GPAT, OABIW→OABI, BAYAU→BAYA), preferred lines first (ICR-PA, ATH-PA), and 44 all-OTC foreign pairs
# with the F-ordinary before the Y-ADR. So the rank is a COMPOSITE, with the SEC's order only the final
# semantic tiebreak:
#
#   1. instrument CLASS   — common (0) over preferred `-P…` (1) over derivative (2: a ticker that extends a
#                           SIBLING's ticker with a warrant/unit/right suffix). Identity-correct even when it
#                           outranks exchange (a pre-separation SPAC's common beats its trading unit; a common
#                           beats a NYSE preferred).
#   2. EXCHANGE rank      — major venue (Nasdaq/NYSE/CBOE) over OTC over none. Splits the ADR/foreign-ordinary
#                           pair (ASML/Nasdaq over ASMLF/OTC) and bankruptcy/temp lines (SNBR over SNBRQ).
#   3. F-ordinary demotion — within OTC, a five-letter `…F` foreign ordinary ranks after a non-F sibling (the
#                           Y-ADR is the US-tradeable line; F is reserved for foreign ordinaries).
#   4. SEC file order     — the final tiebreak, the SEC's own data. NAMED ASSUMPTION (operator-ratified):
#                           SEC-first-row is a proxy for primary US LISTING; for dual-class it tracks
#                           governance-primary (GOOGL class A), not necessarily trading-primary (GOOG class C
#                           is often more liquid). Both liquid Nasdaq so it's moot here. If a dual-class
#                           liquidity case ever bites, the tiebreaker becomes volume.

_MAJOR_EXCHANGES = {"Nasdaq", "NYSE", "CBOE"}
_DERIV_SUFFIXES = {"W", "WS", "U", "UN", "R", "RT", "WT"}  # warrants / units / rights
_PREFERRED_RE = re.compile(r"-P[A-Z]*$")  # the SEC's preferred-line ticker form (ATH-PA, ICR-PA)


def _strip(ticker: str) -> str:
    return ticker.replace("-", "").replace(".", "")


def _class_rank(ticker: str, sibling_tickers: list[str]) -> int:
    """0 common · 1 preferred · 2 derivative-of-a-sibling (suffix check is SIBLING-RELATIVE, so a plain
    ticker that merely ends in W is never misread as a warrant unless its base actually exists)."""
    if _PREFERRED_RE.search(ticker):
        return 1
    t = _strip(ticker)
    for sib in sibling_tickers:
        base = _strip(sib)
        if t != base and t.startswith(base) and t[len(base) :] in _DERIV_SUFFIXES:
            return 2
    return 0


def canonical_sort_key(
    file_index: int, ticker: str, exchange: str | None, sibling_tickers: list[str]
) -> tuple[int, int, int, int]:
    """The composite rank of ONE row among its CIK's siblings — min() wins the primary flag. Pure and
    deterministic; every component is inspectable (the violation cases from the empirical gate are pinned as
    unit tests)."""
    exch = 0 if exchange in _MAJOR_EXCHANGES else (1 if exchange == "OTC" else 2)
    f_ordinary = int(
        exchange == "OTC"
        and ticker.endswith("F")
        and any(not s.endswith("F") for s in sibling_tickers)
    )
    return (_class_rank(ticker, sibling_tickers), exch, f_ordinary, file_index)


def flag_primaries(
    rows: list[tuple[str, str, str | None, str | None]],
) -> list[tuple[str, str, str | None, str | None, bool]]:
    """Append ``is_primary`` to each ``(cik, ticker, name, exchange)`` row: exactly ONE True per CIK (the
    canonical_sort_key winner), False for its siblings. Input order (the SEC file's) is preserved — it is the
    rank's final tiebreak."""
    by_cik: dict[str, list[int]] = {}
    for i, (cik, _t, _n, _e) in enumerate(rows):
        by_cik.setdefault(cik, []).append(i)
    primary: set[int] = set()
    for indices in by_cik.values():
        tickers = [rows[i][1] for i in indices]
        primary.add(
            min(
                indices,
                key=lambda i: canonical_sort_key(
                    i, rows[i][1], rows[i][3], [t for t in tickers if t != rows[i][1]]
                ),
            )
        )
    return [(c, t, n, e, i in primary) for i, (c, t, n, e) in enumerate(rows)]


# --- the SEC table itself ----------------------------------------------------------------------------------


def _load_rows(
    cache_dir: Path | None, allow_live: bool, user_agent: str | None
) -> list[dict[str, str | None]]:
    """The SEC ``company_tickers_exchange.json`` table as normalized row dicts (``cik``/``ticker``/``name``/
    ``exchange``), IN FILE ORDER (the rank's final tiebreak). Cache-first — the RAW payload is cached; one
    file, one GET; raises ``CacheMiss`` if it isn't cached and live pulls are disabled."""
    cache_dir = cache_dir or _DEFAULT_CACHE
    path = cache_dir / "company_tickers_exchange.json"
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
    elif allow_live:
        raw = _fetch_live(user_agent)
        cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(raw), encoding="utf-8")
    else:
        raise CacheMiss("no cached SEC company_tickers_exchange.json (live pulls disabled)")
    idx = {f: i for i, f in enumerate(raw["fields"])}
    out: list[dict[str, str | None]] = []
    for r in raw["data"]:
        cik, ticker = r[idx["cik"]], r[idx["ticker"]]
        if cik is None or not ticker:
            continue  # exact mappings only — never a fuzzy guess (INVARIANT #2)
        out.append(
            {
                "cik": f"{int(cik):010d}",
                "ticker": str(ticker).upper(),
                "name": r[idx["name"]],
                "exchange": r[idx["exchange"]],
            }
        )
    return out


def cik_for(
    ticker: str,
    *,
    cache_dir: Path | None = None,
    allow_live: bool = False,
    user_agent: str | None = None,
) -> str | None:
    """Resolve a ticker to a zero-padded 10-digit CIK from the SEC table. Cache-first.

    Returns ``None`` if the ticker isn't in the (cached) table; raises ``CacheMiss`` if the table
    itself isn't cached and live pulls are disabled.
    """
    ticker = ticker.upper()
    for row in _load_rows(cache_dir, allow_live, user_agent):
        if row["ticker"] == ticker:
            return row["cik"]
    return None


def load_all(
    *,
    cache_dir: Path | None = None,
    allow_live: bool = False,
    user_agent: str | None = None,
) -> list[tuple[str, str, str | None, str | None]]:
    """The FULL SEC universe as ``(cik, ticker, name, exchange)`` quadruples, IN FILE ORDER — the broadener's
    input (``populate_universe`` flags the per-CIK canonical primary via ``flag_primaries``).

    ``cik`` zero-padded to 10 digits, ``ticker`` upper-cased, ``exchange`` the SEC's PER-INSTRUMENT venue
    (Nasdaq/NYSE/CBOE/OTC/None — the discriminator the ADR/foreign-ordinary pair needs). Same cache-first +
    ``ALPHADECK_USER_AGENT`` fetch as ``cik_for`` — ONE GET for the whole ~10k-row file. Rows missing a ticker
    or CIK are skipped (exact mappings only — INVARIANT #2). A CIK may appear under several tickers
    (dual-class / ADR pairs / derivatives), preserved here."""
    return [
        (r["cik"], r["ticker"], r["name"], r["exchange"])
        for r in _load_rows(cache_dir, allow_live, user_agent)
    ]


def _fetch_live(user_agent: str | None) -> dict:
    import httpx

    s = get_settings()
    ua = user_agent or s.user_agent
    if not ua:
        raise RuntimeError(
            "set ALPHADECK_USER_AGENT (SEC requires a declared User-Agent with contact)"
        )
    resp = httpx.get(
        s.sec_company_tickers_url, headers={"User-Agent": ua}, timeout=s.http_timeout_s
    )
    resp.raise_for_status()
    return resp.json()
