"""``pipeline.repair_shares_cover_dates`` — correct the stored shares counts whose cover date is a filer's typo.

THE GAP (found 2026-10-29 on the dev copy of prod). ``fact_shares_outstanding`` held ASLE at 52,990,947 shares
as of 2034-03-05, ``ratified_by='auto'``, citing the 2026-06-30 10-Q (accession 0001104659-26-092710, filed
2026-08-07). The 10-Q's own cover says "outstanding as of August 3, 2026 was 47,726,763". The 2034 row is
AerSale's 10-K/A (0001558370-24-004010, filed 2024-03-27): its page says "as of March 5, 2024", but the
inline-XBRL context behind the count is instant 2034-03-05. companyfacts carries it verbatim, the shares reader
took the newest ``end``, and get-data's auto-confirm applied it under the 10-Q's URL. The reader now refuses a
cover row dated after its own filing (``ingest.edgar.extract.cover_after_filing``); this CLI corrects what the
old reader stored.

WHICH VERSIONS. Every stored version dated after the filing its ``source_ref`` cites (the accession in the URL;
its filing date and SEC's listed acceptance from the company's submissions index, else from a companyfacts row of
that accession). The rule is ``cover_after_filing`` — IMPORTED, never re-stated. Such a version is one of two
things, told apart by the companyfacts cover row it came from (same count, same date):

- **a date typo** (``correct``): that row is dated after its OWN filing — the parser's refusal — or the version is
  dated after today. ``--apply`` appends the correction;
- **a later filing's count** (``later_filing``): that row is a genuine cover of a LATER filing than the one cited
  (the old reader cites the latest 10-Q but takes the newest cover row, so a 10-K filed after it lends its count).
  The count and its date are that filing's own and were public when stored; only the citation is wrong. NOTHING
  is written: a correction from the cited filing would replace a current count with an older one.

Also listed, never written: a typo the OPERATOR ratified (``operator`` — their decision, re-ratified in the
Workbench); a typo whose cited filing has no single cover row of its own (``no_correction``); a version whose count
matches no companyfacts cover row (``origin_unknown``); a fact whose cited filing is not in the documents read
(``not_judged``, #9).

WHAT IT WRITES — one corrected version per ``correct`` fact, appended, never an UPDATE (the table carries the
``no_update`` trigger): the value and as-of date of the CITED filing's own cover row in companyfacts (its ``accn``
is the ``source_ref``'s accession), screened by the same rule — exactly one value on its newest date; ``source`` /
``source_ref`` / ``ratified_by`` / ADS fields copied from the version it replaces; ``vouched`` NULL;
``supersedes`` = that version's id; ``recorded_at`` = now (the database instant); a note naming the correction.
The as-of read keys this table on ``source_ref`` and picks the latest ``recorded_at`` among visible versions, so
the correction wins at every asof from its own ``valid_from``. The table's public instant is ``recorded_at``
(``db.public_clock``): a history view before today keeps the old version — dated 2034, it was invisible to every
view before 2034 anyway.

SAFETY (the ``set_aside_fundamentals`` / ``reingest_fundamentals`` shape):

- **Dry-run is the DEFAULT.** Its connection is READ ONLY and it prints every version dated after its filing:
  company, accession, filing date, SEC's listed acceptance, stored count and date, where the count came from, the
  cited filing's own cover line where the document is cached, and what ``--apply`` does.
- **Cache only by default.** ``--live`` fetches the documents that are not cached (a submissions index for every
  company, a companyfacts only for a fact dated after its filing) through the polite client and the global SEC
  budget; nothing cached is re-fetched.
- **The repair lock.** ``--apply`` holds the REPAIR lock (``pipeline/run_lock.repair_lock_or_exit``: the
  call-of-record lock, then the fact-writer gate) — exit 75 when a nightly pass, a backfill or a draining company
  writer holds it — counts the table before and after inside ONE transaction, and commits only when the
  difference equals the plan.
- **Idempotent.** A fact whose latest version is not after its filing needs nothing, so a second ``--apply``
  appends zero (count the table).
- **Provenance + journal.** ``--apply`` writes one JSON under ``data/repairs/`` (fail-open) and one
  ``data_repair`` journal row, so the editions cut sees the repair.
- Run ``--apply`` on prod only after a labeled backup, from the deployed image, outside 21:30-00:00 ET.

    python -m pipeline.repair_shares_cover_dates                  # dry run (read-only, cache only)
    python -m pipeline.repair_shares_cover_dates --live           # dry run, fetching uncached documents
    python -m pipeline.repair_shares_cover_dates --apply          # append the corrected versions
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg

from db.bitemporal import append_fact
from db.session import connect
from domain.market_time import market_today
from ingest.edgar.client import EdgarClient
from ingest.edgar.converts import clean_filing_text
from ingest.edgar.extract import companyfacts_url, cover_after_filing, screen_cover_rows
from ingest.edgar.submissions import fetch_submissions
from pipeline.run_lock import repair_lock_or_exit
from repositories import data_repair_repo

_log = logging.getLogger("alphadeck.repair_shares_cover_dates")

TOOL = "repair_shares_cover_dates"
TABLE = "fact_shares_outstanding"

# Provenance lives beside the other repairs' (the repo's gitignored data/ == the container's /data).
_DEFAULT_LOG_DIR = Path(__file__).resolve().parents[2] / "data" / "repairs"

# A fact's status (its latest version, judged by the rule).
CORRECT = "correct"  # a date typo on a machine-applied count: --apply appends the correction
CORRECTED = (
    "corrected"  # its latest version is on or before its filing (an earlier --apply): nothing to do
)
OPERATOR = "operator"  # a date typo the operator ratified: listed, re-ratified in the Workbench
NO_CORRECTION = "no_correction"  # a date typo whose cited filing has no single cover row of its own
LATER_FILING = (
    "later_filing"  # a later filing's genuine count under an earlier filing's URL: nothing written
)
ORIGIN_UNKNOWN = "origin_unknown"  # the count matches no companyfacts cover row: cannot tell
NOT_JUDGED = (
    "not_judged"  # the cited filing is not in the documents read (or the URL names no accession)
)
STATUSES = (CORRECT, CORRECTED, OPERATOR, NO_CORRECTION, LATER_FILING, ORIGIN_UNKNOWN, NOT_JUDGED)

# ``https://www.sec.gov/Archives/edgar/data/<cik>/<18-digit accession>/<document>``
_REF_RE = re.compile(r"/edgar/data/(\d+)/(\d{18})/([^/?#]+)")


@dataclass(frozen=True)
class Filing:
    """The cited filing's dates: SEC's filing date (what the rule judges) and its listed acceptance (evidence)."""

    filed: date
    accepted: str  # SEC's raw acceptanceDateTime ("" when only companyfacts knew the filing)
    form: str
    source: str  # "submissions" | "companyfacts"


@dataclass
class Fact:
    """One logical shares fact (one ``source_ref``) with a version dated after its filing, or not judged."""

    security_id: UUID
    ticker: str | None
    source_ref: str
    accession: str | None
    status: str
    bad: list[
        dict[str, Any]
    ]  # the versions dated after the filing, oldest first ([] when not judged)
    latest: dict[str, Any]  # the version the read picks today (latest recorded_at)
    filing: Filing | None = None
    why: str = ""
    origin: str = ""  # the companyfacts cover row(s) the latest count came from
    corrected_shares: float | None = None
    corrected_asof: date | None = None
    cover_line: str | None = None  # the cited filing's own cover sentence (display-only evidence)
    appended_id: UUID | None = None


@dataclass
class RepairResult:
    facts: list[Fact] = field(default_factory=list)
    versions_scanned: int = 0
    rows_before: int = 0
    rows_after: int = 0
    applied: bool = False
    fetched: int = 0  # documents fetched under --live

    def with_status(self, status: str) -> list[Fact]:
        return [f for f in self.facts if f.status == status]


def parse_source_ref(url: str) -> tuple[int, str, str] | None:
    """(cik, accession ``0001234567-26-012345``, document) from an EDGAR archive URL; ``None`` when it names none."""
    m = _REF_RE.search(url or "")
    if m is None:
        return None
    raw = m.group(2)
    return int(m.group(1)), f"{raw[:10]}-{raw[10:12]}-{raw[12:]}", m.group(3)


def _load(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class Docs:
    """The company documents the rule reads: the EDGAR cache on disk, and — only with a ``client`` (``--live``) —
    a fetch of an uncached one through it. One read per company."""

    def __init__(self, cache_dir: Path, client: EdgarClient | None = None) -> None:
        self.cache_dir = cache_dir
        self.client = client
        self.fetched = 0
        self._subs: dict[int, dict[str, Any] | None] = {}
        self._cf: dict[int, dict[str, Any] | None] = {}

    def _read(self, key: str, fetch) -> dict[str, Any] | None:  # noqa: ANN001 — a zero-arg callable
        doc = _load(self.cache_dir / key)
        if doc is not None or self.client is None:
            return doc
        try:
            doc = fetch()
        except (
            Exception
        ):  # noqa: BLE001 — fail-visible: the fact is reported not judged, never guessed
            _log.warning("fetch of %s failed", key, exc_info=True)
            return None
        self.fetched += 1
        return doc

    def submissions(self, cik: int) -> dict[str, Any] | None:
        if cik not in self._subs:
            self._subs[cik] = self._read(
                f"submissions/CIK{cik:010d}.json", lambda: fetch_submissions(self.client, cik)
            )
        return self._subs[cik]

    def companyfacts(self, cik: int) -> dict[str, Any] | None:
        if cik not in self._cf:
            key = f"companyfacts/CIK{cik:010d}.json"
            self._cf[cik] = self._read(
                key, lambda: self.client.get_json(companyfacts_url(cik), key)
            )
        return self._cf[cik]

    def cover_rows(self, cik: int) -> list[dict[str, Any]]:
        node = (
            (self.companyfacts(cik) or {})
            .get("facts", {})
            .get("dei", {})
            .get("EntityCommonStockSharesOutstanding", {})
        )
        return node.get("units", {}).get("shares", [])

    def filing(self, cik: int, accession: str) -> Filing | None:
        """The filing's dates: the submissions index first (it carries the acceptance), else any companyfacts row
        of that accession (a filing past the index's ``recent`` window)."""
        recent = (self.submissions(cik) or {}).get("filings", {}).get("recent", {})
        accns = recent.get("accessionNumber", [])
        if accession in accns:
            i = accns.index(accession)
            accepts = recent.get("acceptanceDateTime", [])
            forms = recent.get("form", [])
            return Filing(
                filed=date.fromisoformat(recent["filingDate"][i]),
                accepted=accepts[i] if i < len(accepts) else "",
                form=forms[i] if i < len(forms) else "",
                source="submissions",
            )
        facts = (self.companyfacts(cik) or {}).get("facts", {})
        for concepts in facts.values():
            for node in concepts.values():
                for rows in node.get("units", {}).values():
                    for r in rows:
                        if r.get("accn") == accession and r.get("filed"):
                            return Filing(
                                filed=date.fromisoformat(r["filed"]),
                                accepted="",
                                form=r.get("form") or "",
                                source="companyfacts",
                            )
        return None

    def cover_line(self, accession: str, document: str) -> str | None:
        """The cited filing's own cover sentence ("... outstanding as of <date> was <count>"), when the document is
        cached — evidence for the reader, never an input to the correction (never fetched)."""
        path = self.cache_dir / "forms" / accession / document
        try:
            text = clean_filing_text(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            return None
        cut = text.lower().find("part i")
        cover = text[: cut if cut > 0 else 40000]
        for m in re.finditer(r"[^.]{0,200}\boutstanding\b[^.]{0,200}", cover, re.IGNORECASE):
            s = re.sub(r"\s+", " ", m.group(0)).replace("​", "").strip()
            if re.search(r"\d{1,3}(?:,\d{3})+", s):
                return s
        return None


def _origins(docs: Docs, cik: int, shares: float, asof: date) -> list[dict[str, Any]]:
    """The companyfacts cover rows a stored count came from: the same count on the same date."""
    return [
        r
        for r in docs.cover_rows(cik)
        if r.get("end") == asof.isoformat() and float(r.get("val", -1)) == float(shares)
    ]


def _describe(rows: list[dict[str, Any]]) -> str:
    return "; ".join(
        f"{r.get('form') or 'filing'} {r.get('accn')}, filed {r.get('filed')}" for r in rows
    )


def _correction(docs: Docs, cik: int, accession: str) -> tuple[float, date] | str:
    """(count, as-of) from the cited filing's OWN cover row in companyfacts, screened by the rule; else the reason
    there is none."""
    rows, _refused = screen_cover_rows(
        [r for r in docs.cover_rows(cik) if r.get("accn") == accession]
    )
    if not rows:
        return "the cited filing has no cover row of its own in companyfacts dated on or before its filing"
    end = max(r["end"] for r in rows)
    vals = sorted({float(r["val"]) for r in rows if r["end"] == end})
    if len(vals) != 1:
        return f"the cited filing reports {len(vals)} cover counts on {end} (classes) — the operator ratifies"
    return vals[0], date.fromisoformat(end)


def _versions(conn: psycopg.Connection) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT f.*, sm.ticker FROM {TABLE} f "  # noqa: S608 — a module constant
            "LEFT JOIN security_master sm ON sm.id = f.security_id AND sm.tenant_id = f.tenant_id "
            "ORDER BY f.tenant_id, f.source_ref, f.recorded_at, f.id"
        )
        return cur.fetchall()


def _count_rows(conn: psycopg.Connection) -> int:
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {TABLE}")  # noqa: S608 — a module constant
        return int(cur.fetchone()["n"])


def _classify(fact: Fact, docs: Docs, cik: int, today: date) -> None:
    """Set the status of a fact whose latest version is dated after its cited filing (see the module docstring)."""
    latest = fact.latest
    origins = _origins(docs, cik, float(latest["shares"]), latest["valid_from"])
    fact.origin = _describe(origins)
    genuine = [
        r
        for r in origins
        if not (
            r.get("filed")
            and cover_after_filing(latest["valid_from"], date.fromisoformat(r["filed"]))
        )
    ]
    typo = latest["valid_from"] > today or (bool(origins) and not genuine)
    if not typo:
        if genuine:
            fact.status = LATER_FILING
            fact.why = (
                f"the count is the cover of a later filing ({_describe(genuine)}), dated on or before that "
                "filing — the count is right, the citation is the earlier filing; nothing written"
            )
        else:
            fact.status = ORIGIN_UNKNOWN
            fact.why = (
                "the count matches no companyfacts cover row"
                + ("" if docs.companyfacts(cik) else " (companyfacts not read)")
                + " — a typo cannot be told from a later filing's count; nothing written"
            )
    elif latest.get("ratified_by") != "auto":
        fact.status = OPERATOR
        fact.why = (
            f"ratified_by={latest.get('ratified_by')!r}, not machine-applied — the operator's decision; "
            "re-ratify in the Workbench"
        )
    else:
        got = _correction(docs, cik, fact.accession or "")
        if isinstance(got, str):
            fact.status, fact.why = NO_CORRECTION, got
        else:
            fact.status = CORRECT
            fact.corrected_shares, fact.corrected_asof = got


def scan(
    conn: psycopg.Connection,
    *,
    cache_dir: Path | None = None,
    client: EdgarClient | None = None,
    today: date | None = None,
) -> RepairResult:
    """Read every stored version and judge each logical fact by the rule. Reads only; the caller owns the
    connection (a dry run hands in a READ ONLY one). ``client`` = ``--live`` (fetch uncached documents).
    """
    docs = Docs(cache_dir or EdgarClient(allow_live=False).cache_dir, client)
    today = today or market_today()
    versions = _versions(conn)
    by_key: dict[tuple[UUID, str], list[dict[str, Any]]] = {}
    for v in versions:
        by_key.setdefault((v["tenant_id"], v["source_ref"]), []).append(v)
    n = len(versions)
    result = RepairResult(versions_scanned=n, rows_before=n, rows_after=n)
    for (_tenant, ref), vs in by_key.items():
        latest = vs[-1]  # ordered by recorded_at, id — the version the as-of read picks today
        parsed = parse_source_ref(ref)
        filing = docs.filing(parsed[0], parsed[1]) if parsed else None
        fact = Fact(
            security_id=latest["security_id"],
            ticker=latest.get("ticker"),
            source_ref=ref,
            accession=parsed[1] if parsed else None,
            status=NOT_JUDGED,
            bad=[],
            latest=latest,
            filing=filing,
        )
        if filing is None:  # cannot be judged: listed, never skipped silently (#9)
            fact.why = (
                "the source_ref names no EDGAR accession"
                if parsed is None
                else "the cited filing is not in the submissions index or companyfacts read"
            )
            result.facts.append(fact)
            continue
        cik, accession, document = parsed
        fact.bad = [v for v in vs if cover_after_filing(v["valid_from"], filing.filed)]
        if not fact.bad:
            continue
        fact.cover_line = docs.cover_line(accession, document)
        if not cover_after_filing(latest["valid_from"], filing.filed):
            fact.status = CORRECTED
        else:
            _classify(fact, docs, cik, today)
        result.facts.append(fact)
    result.facts.sort(key=lambda f: (STATUSES.index(f.status), f.ticker or "", f.source_ref))
    result.fetched = docs.fetched
    return result


def _note(fact: Fact) -> str:
    old = fact.latest
    origin = f" — the companyfacts row of {fact.origin}" if fact.origin else ""
    return (
        f"Cover-page shares outstanding as of {fact.corrected_asof.isoformat()}, the cited filing's own cover row "
        f"({fact.accession}). Corrected {datetime.now(timezone.utc).date().isoformat()} by "
        f"pipeline.{TOOL}: the version it supersedes read {float(old['shares']):,.0f} as of "
        f"{old['valid_from'].isoformat()}, after this filing's date ({fact.filing.filed.isoformat()}){origin}; "
        "a cover count is never dated after its filing."
    )


def apply(conn: psycopg.Connection, result: RepairResult) -> None:
    """Append one corrected version per ``correct`` fact in ONE transaction, counting the table before and after;
    commit only when the difference equals the plan, else roll back and raise. The caller holds the lock.
    """
    plan = result.with_status(CORRECT)
    before = _count_rows(conn)
    for f in plan:
        old = f.latest
        f.appended_id = append_fact(
            conn,
            TABLE,
            {
                "tenant_id": old["tenant_id"],
                "security_id": old["security_id"],
                "shares": f.corrected_shares,
                "source": old["source"],
                "source_ref": old["source_ref"],
                "ratified_by": old["ratified_by"],
                "vouched": None,
                "ads_ratio": old["ads_ratio"],
                "ads_ratio_status": old["ads_ratio_status"],
                "valid_from": f.corrected_asof,
                "supersedes": old["id"],
                "note": _note(f),
            },
        )
    after = _count_rows(conn)
    if after - before != len(plan):
        conn.rollback()
        for f in plan:
            f.appended_id = None
        raise RuntimeError(
            f"count check failed: {TABLE} went {before} -> {after}, the plan was +{len(plan)} — ROLLED BACK"
        )
    conn.commit()
    result.rows_before, result.rows_after, result.applied = before, after, True


# --- provenance + report -----------------------------------------------------------------------------------


def _iso(x: date | datetime | None) -> str | None:
    return x.isoformat() if x is not None else None


def build_payload(result: RepairResult) -> dict[str, Any]:
    """The provenance payload — PURE (no I/O)."""
    return {
        "kind": TOOL,
        "at": datetime.now(timezone.utc).isoformat(),
        "applied": result.applied,
        "rows_before": result.rows_before,
        "rows_after": result.rows_after,
        "counts": {s: len(result.with_status(s)) for s in STATUSES},
        "facts": [
            {
                "ticker": f.ticker,
                "security_id": str(f.security_id),
                "source_ref": f.source_ref,
                "accession": f.accession,
                "status": f.status,
                "why": f.why or None,
                "filed": _iso(f.filing.filed) if f.filing else None,
                "accepted": f.filing.accepted if f.filing else None,
                "bad_versions": [
                    {
                        "id": str(v["id"]),
                        "shares": float(v["shares"]),
                        "valid_from": _iso(v["valid_from"]),
                        "recorded_at": _iso(v["recorded_at"]),
                        "ratified_by": v["ratified_by"],
                    }
                    for v in f.bad
                ],
                "origin": f.origin or None,
                "corrected_shares": f.corrected_shares,
                "corrected_asof": _iso(f.corrected_asof),
                "cover_line": f.cover_line,
                "appended_id": str(f.appended_id) if f.appended_id else None,
            }
            for f in result.facts
        ],
    }


def write_payload(payload: dict[str, Any], base_dir: Path | None = None) -> Path | None:
    """Write the provenance JSON to ``<base>/<utc-timestamp>-repair-shares-cover-dates.json``; return the path, or
    ``None`` fail-open (the committed rows ARE the repair; a provenance fault is logged, never raised).
    """
    base = base_dir or _DEFAULT_LOG_DIR
    try:
        base.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = base / f"{stamp}-repair-shares-cover-dates.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path
    except OSError:
        _log.exception("provenance write failed (fail-open — the rows are committed)")
        return None


def report(result: RepairResult) -> None:
    for f in result.facts:
        name = f.ticker or str(f.security_id)
        print(f"  [{f.status}] {name} · {f.accession or '?'} · {f.source_ref}")
        if f.filing is not None:
            accepted = f" · SEC acceptance listed {f.filing.accepted}" if f.filing.accepted else ""
            print(
                f"      filing: {f.filing.form or '?'} filed {f.filing.filed.isoformat()}{accepted} "
                f"(from {f.filing.source})"
            )
        for v in f.bad or [f.latest]:  # a not-judged fact shows the version the read picks
            print(
                f"      stored: {float(v['shares']):,.0f} shares as of {v['valid_from'].isoformat()} "
                f"(ratified_by={v['ratified_by']}, recorded {v['recorded_at'].isoformat()}) [row {v['id']}]"
            )
        if f.origin:
            print(f"      that count is the companyfacts cover row of {f.origin}")
        if f.cover_line:
            print(f'      the cited filing\'s own cover: "{f.cover_line}"')
        if f.status == CORRECT:
            verb = "appended" if f.appended_id else "would append"
            print(
                f"      -> {verb}: {f.corrected_shares:,.0f} shares as of {f.corrected_asof.isoformat()}"
                + (f" [row {f.appended_id}]" if f.appended_id else "")
            )
        elif f.status == CORRECTED:
            print("      -> already corrected (its latest version is on or before the filing)")
        else:
            print(f"      -> {f.why}")
    c = {s: len(result.with_status(s)) for s in STATUSES}
    dated_after = sum(c[s] for s in STATUSES if s != NOT_JUDGED)
    verb = "appended" if result.applied else "would append"
    n = sum(1 for f in result.facts if f.appended_id) if result.applied else c[CORRECT]
    print(
        f"SUMMARY: {result.versions_scanned} stored version(s) scanned · {dated_after} fact(s) with a version "
        f"dated after the filing it cites: {c[CORRECT]} date typo(s) to correct, {c[CORRECTED]} already corrected, "
        f"{c[OPERATOR]} operator-ratified typo(s) (left), {c[NO_CORRECTION]} typo(s) without a correction, "
        f"{c[LATER_FILING]} later filing's count (left), {c[ORIGIN_UNKNOWN]} origin unknown (left) · "
        f"{c[NOT_JUDGED]} not judged · {verb} {n} · {result.fetched} document(s) fetched · "
        f"{TABLE} rows {result.rows_before} -> {result.rows_after}"
    )


# --- the CLI ---------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Correct fact_shares_outstanding versions whose cover date is a filer's typo (dated after "
        "its own filing). Lists every version dated after the filing it cites. Dry-run by default (read-only, "
        "cache only); --apply appends corrected versions under the repair lock."
    )
    p.add_argument(
        "--apply", action="store_true", help="append the corrected versions (default: dry-run)"
    )
    p.add_argument(
        "--live",
        action="store_true",
        help="fetch the documents that are not cached (default: cache only, no network)",
    )
    args = p.parse_args(argv)

    # W1 — a writing run excludes the nightly pass AND every company writer (pipeline.run_lock); exit 75 if busy
    lock = repair_lock_or_exit(holder=TOOL) if args.apply else None
    try:
        conn = connect()
        try:
            conn.read_only = not args.apply  # a dry run PHYSICALLY cannot write
            with conn.cursor() as cur:
                cur.execute("SELECT current_database() AS db")
                print(f"target DB : {cur.fetchone()['db']}")
            result = scan(conn, client=EdgarClient(allow_live=True) if args.live else None)
            if not args.apply:
                conn.rollback()
                print(f"\n=== {TOOL} — DRY-RUN (nothing written) ===")
                report(result)
                return
            print(f"\n=== {TOOL} — APPLY ===")
            try:
                apply(conn, result)
            except RuntimeError as exc:
                print(f"ABORTED: {exc}")
                raise SystemExit(1) from None
            report(result)
            provenance = write_payload(build_payload(result))
            print(f"provenance: {provenance}")
            appended = sum(1 for f in result.facts if f.appended_id)
            # S7 — the data-repair journal: one row per --apply, under the lock
            data_repair_repo.journal(
                conn,
                tool=TOOL,
                summary=f"corrected {appended} shares count(s) whose cover date was a filer's typo",
                rows_changed=appended,
                counts={s: len(result.with_status(s)) for s in STATUSES} | {"appended": appended},
                provenance=str(provenance) if provenance else None,
            )
        finally:
            conn.close()
    finally:
        if lock is not None:
            lock.release()


if __name__ == "__main__":
    main()
