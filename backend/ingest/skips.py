"""Q18 — per-FILING skips the filing legs already tolerate, as records instead of only print lines.

Three paths skip one filing and keep walking, and each printed a line for it on EVERY pass, because a skipped
filing is never stored and so is re-tried every night (MEASURED 2026-10-17 by replaying the legs read-only
over prod's cache + DB: 76 Form 4 lines + 365 schedule-13 lines a night, about 73% of the cron log):

- ``form4_unparseable`` — ``pipeline.ingest_thesis._form4_leg``: a Form 4 whose document fails to fetch or
  parse (``_tolerable_filing_error``). AUDITED: all 76 are pre-XML filings dated 1997 to 2003-04-02, before
  the June 2003 XML mandate — 60 SGML text documents, 15 accessions with no primary document (the fetched
  "document" is EDGAR's directory listing), 1 whose listed ``0001.txt`` 404s (verified live). None is a parser
  bug, and no ``ValueError`` (the timezone-bug class) appears among them.
- ``schedule13_outbound`` — ``ingest_schedule13``'s subject screen: a 13D-family filing in a company's own
  feed whose cover names a DIFFERENT subject — the company FILED it about someone else. AUDITED: all 365 lines
  (328 accessions) are 13D-family; in 364 the feed owner is the lead filer, in 1 a co-reporting person. A
  deliberate precision cut (#290), never a dropped subject row.
- ``schedule13_identity`` — ``ingest_schedule13``: the filer-identity fetch/parse failed; the row is STORED
  with NULL identity (#9) and retried each pass. None on prod at the audit.

This module only DESCRIBES a skip. Which ones are "known" (failed on the previous finished pass, so they are
counted instead of printed) is decided against the run artifact by ``pipeline.daily`` — the O1 name-error
mechanism, mirrored. A key is ``<kind>:<accession>``: the failure is a property of the filing (an unparseable
document is unparseable for every feed that lists it). Pure: no DB, no network, no clock.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass
from uuid import UUID

FORM4_UNPARSEABLE = "form4_unparseable"
SCHEDULE13_OUTBOUND = "schedule13_outbound"
SCHEDULE13_IDENTITY = "schedule13_identity"
SKIP_KINDS: tuple[str, ...] = (FORM4_UNPARSEABLE, SCHEDULE13_OUTBOUND, SCHEDULE13_IDENTITY)

# the reason is kept for a reader (the artifact), not to grep a traceback out of
MAX_REASON_CHARS = 200


def skip_key(kind: str, accession: str) -> str:
    """The cross-pass identity of one skipped filing: ``"<kind>:<accession>"``."""
    return f"{kind}:{accession}"


def clip_reason(reason: object) -> str:
    s = " ".join(str(reason).split())
    return s if len(s) <= MAX_REASON_CHARS else s[: MAX_REASON_CHARS - 1] + "…"


def is_known(known: Collection[str] | None, kind: str, accession: str) -> bool:
    """Did this filing skip the same way on the previous finished pass? ``None`` = no baseline (the first
    pass, a standalone CLI run, the on-promote kick): nothing is known, so everything still prints.
    """
    return known is not None and skip_key(kind, accession) in known


@dataclass(frozen=True)
class IngestSkip:
    """One filing a leg skipped this pass: which path (``kind``), which filing, for which name, and why."""

    kind: str
    accession: str
    security_id: UUID
    ticker: str | None
    reason: str

    @property
    def key(self) -> str:
        return skip_key(self.kind, self.accession)


def dedup_skips(skips: Iterable[IngestSkip]) -> dict[str, IngestSkip]:
    """``key -> skip``, first occurrence wins — one filing is ONE row even when two theses hold the name."""
    out: dict[str, IngestSkip] = {}
    for s in skips:
        out.setdefault(s.key, s)
    return out
