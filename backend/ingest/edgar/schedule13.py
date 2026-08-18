"""SC 13D/G activist-stake ingest (Band 03 S5) — the ownership tape's evidence layer.

Appends one ``fact_activist_stake`` row per (SUBJECT security, 13D/G filing) from the SAME
submissions JSON the Form 4 / 8-K legs already fetch — the S5-verified enumeration path (a): EDGAR
indexes an ownership schedule under both the filer and the subject, so the subject's own submissions
JSON lists every 13D/G filed about it, across BOTH form-name eras (``SCHEDULE13_FORMS`` — the
rename trap), at zero extra enumeration fetches. Store EVERY 13D/G-family filing, not just the
detector cut (#9 recall): 13G rows and amendments stay on the tape for the deferred 13G→13D-switch
and %-owned refinements — no re-ingest.

FILER IDENTITY (the 0024 capture pattern, applied to the activist). The subject's submissions rows
carry no filer, so identity costs ONE small immutable-cached document fetch per filing, bounded by
the S5 depth strategy (cost is the operator's to spend, never ambient):

- **structured era** (``primary_doc`` is ``…/primary_doc.xml``, post-2024-12): the RAW XML at the
  accession root (the ``form4_doc_url`` xsl-drop) — ``reportingPersonName`` / ``reportingPersonCIK``
  / ``percentOfClass``, a deterministic parse (#3: identity strings + a structured number shown as
  evidence, never the fire decision);
- **classic-era 13D-family**: the filing's SGML header (``{accession}.txt``) ``FILED BY:`` block —
  deterministic key-value lines, no NLP;
- **classic-era 13G**: NO fetch — stored with NULL filer by design (those rows can never fire, and
  identity is backfillable later; the 0024 "no backfill required" precedent).

An identity fetch/parse failure NEVER drops the row — it lands with NULL filer and a LOUD counter
(``identity_skipped``); a later run retries and RE-VERSIONS the row when identity resolves
(append-if-changed — a new version, never an UPDATE; the form8k semantics).

``valid_from = filed`` (the EDGAR acceptance date IS the knowability — gold-doc trap #4: the stake
crossing inside the filing predates dissemination by up to 10 days, and the structured cover's
``dateOfEvent`` is deliberately never read for time). ``recorded_at`` is left to the DB's ``now()``,
NEVER backdated (invariant #4). The natural-key constraint carries ``security_id`` (migration 0039 —
the 0037 lesson).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

import psycopg

from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.settings import get_settings
from ingest.edgar.form8k import filing_index_url
from ingest.edgar.submissions import SCHEDULE13_D_FORMS, form4_doc_url


@dataclass(frozen=True)
class Schedule13Result:
    """One security's 13D/G ingest outcome: ``appended`` = brand-new filings stored;
    ``reversioned`` = new VERSIONS of already-stored filings whose fact surface changed (identity
    resolving on a retry / a corrected filed date); ``identity_skipped`` = rows stored THIS run with
    NULL filer because the identity fetch/parse failed — loud, never a dropped row (#9)."""

    appended: int = 0
    reversioned: int = 0
    identity_skipped: int = 0


def is_13d_family(form: str) -> bool:
    """13D-family (intent) vs 13G-family (passive) — the fire boundary AND the identity-depth
    boundary, decided on the exact form string across both naming eras."""
    return form in SCHEDULE13_D_FORMS


def _norm_cik(raw: str | None) -> str | None:
    """Normalize a captured CIK to EDGAR's canonical 10-digit form (deterministic compares across
    re-fetches); a non-numeric capture is discarded (None) rather than stored malformed."""
    s = (raw or "").strip()
    if not s.isdigit():
        return None
    return s.zfill(10)


def parse_structured_cover(xml_text: str) -> dict[str, Any]:
    """The structured-era (post-2024-12) 13D/G cover: ``{filer_cik, filer_name, pct_owned}`` from
    the raw ``primary_doc.xml``.

    Namespace-tolerant localname matching (the schema declares a default xmlns). The FIRST reporting
    person is the lead filer (a group filing lists several; the provenance URL shows the rest — a
    documented v1 bound). ``percentOfClass`` is parsed as a float where numeric, else None — shown
    as evidence, never fired on (#3). The cover's ``dateOfEvent`` is deliberately NOT read (trap #4:
    knowability is the filing date, never the in-document event date)."""
    root = ET.fromstring(xml_text)
    filer_cik: str | None = None
    filer_name: str | None = None
    pct: float | None = None
    fallback_cik: str | None = None
    for elem in root.iter():
        local = elem.tag.rsplit("}", 1)[-1]
        text = (elem.text or "").strip()
        if not text:
            continue
        if local == "reportingPersonName" and filer_name is None:
            filer_name = text
        elif local == "reportingPersonCIK" and filer_cik is None:
            filer_cik = _norm_cik(text)
        elif local == "cik" and fallback_cik is None:
            fallback_cik = _norm_cik(text)  # headerData filer credentials — the fallback id
        elif local == "percentOfClass" and pct is None:
            try:
                pct = float(text.rstrip("%").strip())
            except ValueError:
                pct = None  # a non-numeric percent field stays evidence-absent, never guessed
    return {"filer_cik": filer_cik or fallback_cik, "filer_name": filer_name, "pct_owned": pct}


def parse_sgml_filed_by(txt: str) -> dict[str, Any]:
    """The classic-era filer identity: ``{filer_cik, filer_name}`` from the filing's SGML header
    ``FILED BY:`` block (machine-generated key-value lines — deterministic, no NLP).

    Reads the FIRST ``FILED BY`` block only (the lead filer of a group filing), truncated at a
    following ``SUBJECT COMPANY`` section so the subject's keys can never be mistaken for the
    filer's (block order varies filing to filing). No block found -> both None (the caller stores
    the row with NULL identity — never dropped, #9)."""
    idx = txt.find("FILED BY")
    if idx < 0:
        return {"filer_cik": None, "filer_name": None}
    segment = txt[idx:]
    subj = segment.find("SUBJECT COMPANY")
    if subj >= 0:
        segment = segment[:subj]
    name: str | None = None
    cik: str | None = None
    for line in segment.splitlines():
        key, _, value = line.partition(":")
        k = key.strip()
        if k == "COMPANY CONFORMED NAME" and name is None:
            name = value.strip() or None
        elif k == "CENTRAL INDEX KEY" and cik is None:
            cik = _norm_cik(value)
        if name is not None and cik is not None:
            break
    return {"filer_cik": cik, "filer_name": name}


def _fetch_identity(client, cik: str | int, filing: dict[str, Any]) -> dict[str, Any] | None:
    """Fetch + parse the activist's identity for ONE filing per the S5 depth strategy, or ``None``
    for a filing outside the bounded depth (classic-era 13G — no fetch by design).

    Structured era -> the raw primary_doc.xml (small; ``forms/`` immutable prefix — cached forever);
    classic-era 13D-family -> the ``{accession}.txt`` SGML header (one-time per accession, same
    immutable cache). Raises on a fetch/parse failure — the CALLER stores the row with NULL identity
    and counts it loud (a failure is one filing's, never the leg's)."""
    primary_doc = filing.get("primary_doc") or ""
    accession = filing["accession"]
    if primary_doc.lower().endswith(".xml"):
        doc = primary_doc.rsplit("/", 1)[-1]
        xml = client.get_text(form4_doc_url(cik, accession, primary_doc), f"forms/{accession}/{doc}")
        return parse_structured_cover(xml)
    if is_13d_family(filing["form"]):
        nodash = accession.replace("-", "")
        url = f"{get_settings().sec_archives_base}/{int(cik)}/{nodash}/{accession}.txt"
        txt = client.get_text(url, f"forms/{accession}/{accession}.txt")
        return {**parse_sgml_filed_by(txt), "pct_owned": None}
    return None  # classic-era 13G: out of the bounded identity depth — NULL filer by design


def existing_schedule13(
    conn: psycopg.Connection, security_id: UUID, *, tenant_id: UUID = DEFAULT_TENANT_ID
) -> dict[str, dict[str, Any]]:
    """The LATEST stored version of each 13D/G accession for (tenant, security) — the compare
    surface (form / filed / filer / pct) the append-if-changed reads. Accession is the filing
    identity; the latest version per accession is what the as-of read would return at now, so
    "unchanged vs latest" == "the tape already says this"."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (accession) accession, form, filed, filer_cik, filer_name, "
            "pct_owned FROM fact_activist_stake WHERE tenant_id = %s AND security_id = %s "
            "ORDER BY accession, recorded_at DESC, id DESC",
            (tenant_id, security_id),
        )
        return {r["accession"]: r for r in cur.fetchall()}


def _pct(value: Any) -> float | None:
    return None if value is None else float(value)


def ingest_schedule13(
    conn: psycopg.Connection,
    security_id: UUID,
    cik: str | int,
    filings: list[dict[str, Any]],
    client,
    *,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    recorded_at=None,
) -> Schedule13Result:
    """Append-if-changed the 13D/G tape for one SUBJECT security (the caller owns the txn — no
    commit here).

    ``filings`` is ``submissions.schedule13_filings`` output: ``{accession, form, filed,
    primary_doc}`` with ``filed`` an ISO date string. Per filing: resolve the activist's identity
    (``_fetch_identity`` — bounded depth; a stored row whose identity already resolved is NEVER
    re-fetched, and a stored NULL-identity row in depth is retried each run, cache-first). A filing
    whose surface (form / filed / filer / pct) matches the stored latest version appends nothing
    (count-the-table idempotent); a changed surface — the load-bearing case being identity resolving
    on a retry — appends a new VERSION. An identity failure stores the row with NULL filer +
    ``identity_skipped`` (loud, never dropped — #9). ``recorded_at`` is a TEST seam only —
    production leaves it to the DB's ``now()`` (never backdated, #4)."""
    existing = existing_schedule13(conn, security_id, tenant_id=tenant_id)
    result_appended = 0
    result_reversioned = 0
    identity_skipped = 0
    for f in filings:
        filed = date.fromisoformat(f["filed"])
        prior = existing.get(f["accession"])
        if prior is not None and (prior["filer_cik"] or prior["filer_name"]):
            # identity already on the tape — reuse it (no fetch; nothing to resolve)
            identity: dict[str, Any] | None = {
                "filer_cik": prior["filer_cik"],
                "filer_name": prior["filer_name"],
                "pct_owned": _pct(prior["pct_owned"]),
            }
        else:
            try:
                identity = _fetch_identity(client, cik, f)
            except Exception as e:  # noqa: BLE001 — one filing's identity failure, never the leg's
                identity = None
                identity_skipped += 1
                print(f"  warn: schedule13 {f['accession']} identity unresolved: {e}")
        filer_cik = identity["filer_cik"] if identity else None
        filer_name = identity["filer_name"] if identity else None
        pct_owned = identity.get("pct_owned") if identity else None
        if prior is not None and (
            prior["form"] == f["form"]
            and prior["filed"] == filed
            and prior["filer_cik"] == filer_cik
            and prior["filer_name"] == filer_name
            and _pct(prior["pct_owned"]) == pct_owned
        ):
            continue  # the tape already says this — no duplicate append
        values = {
            "tenant_id": tenant_id,
            "security_id": security_id,
            "form": f["form"],
            "filer_cik": filer_cik,
            "filer_name": filer_name,
            "pct_owned": pct_owned,
            "accession": f["accession"],
            "filed": filed,
            "source_ref": filing_index_url(cik, f["accession"]),
            "valid_from": filed,  # = filed (knowability; never the in-document event date — trap #4)
        }
        if recorded_at is not None:
            values["recorded_at"] = recorded_at
        append_fact(conn, "fact_activist_stake", values)
        if prior is None:
            result_appended += 1
        else:
            result_reversioned += 1
    return Schedule13Result(
        appended=result_appended,
        reversioned=result_reversioned,
        identity_skipped=identity_skipped,
    )
