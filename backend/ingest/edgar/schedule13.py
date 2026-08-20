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
    NULL filer because the identity fetch/parse failed — loud, never a dropped row (#9);
    ``subject_skipped`` = 13D-family filings NOT stored because the cover/header names a DIFFERENT
    subject than this feed's owner (the owner is the FILER of an OUTBOUND stake about someone else —
    the ingest root-cause fix; a precision drop of visible junk, logged, never a real subject's row).
    """

    appended: int = 0
    reversioned: int = 0
    identity_skipped: int = 0
    subject_skipped: int = 0


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
    """The structured-era (post-2024-12) 13D/G cover: ``{filer_cik, filer_name, pct_owned,
    subject_cik}`` from the raw ``primary_doc.xml``.

    Namespace-tolerant localname matching (the schema declares a default xmlns). The FIRST reporting
    person is the lead filer (a group filing lists several; the provenance URL shows the rest — a
    documented v1 bound). ``percentOfClass`` is parsed as a float where numeric, else None — shown
    as evidence, never fired on (#3). The cover's ``dateOfEvent`` is deliberately NOT read (trap #4:
    knowability is the filing date, never the in-document event date).

    ``subject_cik`` is the schedule's TRUE subject — the ``<issuerInfo><issuerCIK>`` the SEC's own
    schema carries verbatim on every structured cover (VERIFIED on the real UEC ``0001437749-26-024641``
    → Uranium Royalty ``0002143673`` and GameStop ``0001193125-26-202465`` → eBay ``0001065088``). It
    is the FREE half of the ingest subject-attribution fix: the cover is already fetched for identity,
    so no extra pull. The ingest compares it to the feed-owner CIK to drop a filing the feed lists only
    because the owner FILED it (its OUTBOUND stake about someone else), never because it is the subject.
    """
    root = ET.fromstring(xml_text)
    filer_cik: str | None = None
    filer_name: str | None = None
    pct: float | None = None
    fallback_cik: str | None = None
    subject_cik: str | None = None
    for elem in root.iter():
        local = elem.tag.rsplit("}", 1)[-1]
        text = (elem.text or "").strip()
        if not text:
            continue
        if local == "reportingPersonName" and filer_name is None:
            filer_name = text
        elif local == "reportingPersonCIK" and filer_cik is None:
            filer_cik = _norm_cik(text)
        elif local == "issuerCIK" and subject_cik is None:
            subject_cik = _norm_cik(
                text
            )  # the schedule's TRUE subject (never confused with the filer)
        elif local == "cik" and fallback_cik is None:
            fallback_cik = _norm_cik(text)  # headerData filer credentials — the fallback id
        elif local == "percentOfClass" and pct is None:
            try:
                pct = float(text.rstrip("%").strip())
            except ValueError:
                pct = None  # a non-numeric percent field stays evidence-absent, never guessed
    return {
        "filer_cik": filer_cik or fallback_cik,
        "filer_name": filer_name,
        "pct_owned": pct,
        "subject_cik": subject_cik,
    }


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


def parse_sgml_subject_cik(txt: str) -> str | None:
    """The classic-era 13D subject CIK from the filing's SGML header ``SUBJECT COMPANY:`` block — the
    company the schedule is filed ABOUT (the mirror of ``parse_sgml_filed_by``, which reads the filer).

    Reads the FIRST ``SUBJECT COMPANY`` block, truncated at a following ``FILED BY`` / ``FILER`` /
    ``REPORTING`` section so a filer's ``CENTRAL INDEX KEY`` can never be mistaken for the subject's
    (block order varies filing to filing — VERIFIED on the real 2021 atai×CMPS header, SUBJECT COMPANY
    = COMPASS Pathways ``0001816590`` filed BY ATAI ``0001840904``). No block / no CIK -> ``None`` (the
    caller then cannot verify the subject and KEEPS the row — recall-safe, #9; unresolved ≠ mis-attributed).
    """
    idx = txt.find("SUBJECT COMPANY")
    if idx < 0:
        return None
    segment = txt[idx + len("SUBJECT COMPANY") :]
    bounds = [segment.find(b) for b in ("FILED BY", "FILER:", "REPORTING") if segment.find(b) >= 0]
    if bounds:
        segment = segment[: min(bounds)]
    for line in segment.splitlines():
        key, _, value = line.partition(":")
        if key.strip() == "CENTRAL INDEX KEY":
            return _norm_cik(value)
    return None


def _fetch_identity(client, cik: str | int, filing: dict[str, Any]) -> dict[str, Any] | None:
    """Fetch + parse the activist's identity for ONE filing per the S5 depth strategy, or ``None``
    for a filing outside the bounded depth (classic-era 13G — no fetch by design).

    Structured era -> the raw primary_doc.xml (small; ``forms/`` immutable prefix — cached forever);
    classic-era 13D-family -> the ``{accession}.txt`` SGML header (one-time per accession, same
    immutable cache). Raises on a fetch/parse failure — the CALLER stores the row with NULL identity
    and counts it loud (a failure is one filing's, never the leg's).

    The returned dict also carries ``subject_cik`` (the schedule's TRUE subject) wherever a document is
    fetched — the ``<issuerCIK>`` on the structured cover, the ``SUBJECT COMPANY`` block on the classic
    header — so the caller can drop a filing the feed lists only because its owner FILED it. No extra
    fetch (the document is already pulled for identity); ``None`` when unresolved (the caller keeps the
    row, #9)."""
    primary_doc = filing.get("primary_doc") or ""
    accession = filing["accession"]
    if primary_doc.lower().endswith(".xml"):
        doc = primary_doc.rsplit("/", 1)[-1]
        xml = client.get_text(
            form4_doc_url(cik, accession, primary_doc), f"forms/{accession}/{doc}"
        )
        return parse_structured_cover(xml)
    if is_13d_family(filing["form"]):
        nodash = accession.replace("-", "")
        url = f"{get_settings().sec_archives_base}/{int(cik)}/{nodash}/{accession}.txt"
        txt = client.get_text(url, f"forms/{accession}/{accession}.txt")
        return {
            **parse_sgml_filed_by(txt),
            "pct_owned": None,
            "subject_cik": parse_sgml_subject_cik(txt),
        }
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
    production leaves it to the DB's ``now()`` (never backdated, #4).

    SUBJECT ATTRIBUTION (the root-cause fix): a 13D-family filing whose cover/header names a subject
    OTHER than ``cik`` (this feed's owner) is DROPPED and counted ``subject_skipped`` (a loud precision
    cut) — the owner FILED it about someone else, so the row is the TRUE subject's, ingested under
    that subject's own feed. An unresolved subject keeps the row (recall-safe #9); 13G-family is out
    of scope (fires nothing; no header fetched in its classic era)."""
    existing = existing_schedule13(conn, security_id, tenant_id=tenant_id)
    result_appended = 0
    result_reversioned = 0
    identity_skipped = 0
    subject_skipped = 0
    owner_cik = _norm_cik(str(cik))  # this feed's owner (the security we are ingesting FOR)
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
        # SUBJECT-ATTRIBUTION SKIP (the ingest root-cause fix; 13D-family only). EDGAR indexes a
        # schedule in BOTH the filer's and the subject's submissions feed, so this SUBJECT-feed
        # enumeration also picks up schedules the owner FILED about OTHER companies (its OUTBOUND
        # stakes). When the cover/header names a subject that is NOT this feed's owner, the owner is
        # the FILER, not the subject — the row belongs to the TRUE subject's tape (ingested there
        # under its own feed), never here. DROP it (never store the wrong-subject row). A RESOLVED
        # subject that MATCHES the owner keeps the row (the normal inbound case); an UNRESOLVED
        # subject (parse failure, or a reused prior identity that carries none) keeps it too
        # (recall-safe #9 — unresolved ≠ mis-attributed; the fire-side ``_is_misattributed`` screen
        # still guards a residual). 13G-family is deliberately OUT of scope: it fires nothing, and its
        # classic era carries no fetched header, so verifying it would cost ~14k header fetches for no
        # fire impact — the 13G→13D switch's own ``filer≠subject`` guard tolerates a residual 13G mis-fan.
        subj = identity.get("subject_cik") if identity else None
        if is_13d_family(f["form"]) and subj and _norm_cik(subj) != owner_cik:
            subject_skipped += 1
            print(
                f"  skip: schedule13 {f['accession']} subject {subj} != feed-owner {owner_cik} "
                "— outbound filing (owner is the filer, not the subject)"
            )
            continue
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
        subject_skipped=subject_skipped,
    )
