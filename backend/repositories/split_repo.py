"""The split list (migration 0052, split plan S1) — every version of every split, its as-of read, and the
operator's verbs, each with a visible inverse (WB#1).

**The history read (S2, the public clock).** ``current_rows`` / ``as_of_rows(clock="public")``: a split counts
from its OWN publication (``db.public_clock.split_versions_sql``), and a view reads its newest version.

**The read.** ``latest_versions`` is the list's as-of read: the newest version of each logical split
(``security_id, key_date``) with ``recorded_at <= known_at`` — and, given an ``asof``, ``valid_from <= asof``
(publicly known by then) — the same latest-version pick as every bitemporal fact (``DISTINCT ON`` the natural
key, ``recorded_at DESC, id DESC``). ``known_at`` defaults to the DATABASE clock (``clock_timestamp()``, the
one-clock rule of ``db/clock.py``), so a version written a moment ago is never invisible to the next read.

**The verbs** (each appends ONE version naming the operator, under a per-split advisory lock, and only when
the caller names the CURRENT version — a stale click is refused, never applied over a newer decision):

- ``confirm`` a needs-review split with one source's values (``sec`` or ``polygon``): its ratio and its
  first session. A first session still ahead lands as ``announced`` (a split is never "in effect" before
  its day). A date that differs from the key re-dates the split — still the same logical split, refused if
  another live split of the same security already sits on that date. Inverse: ``revoke``.
- ``confirm_steward`` a needs-review split with the steward's OWN values (decision D3, migration 0215: a ratio and
  a first session neither source states — POM's ADR termination), citing a document (``cited_url``): the same
  refusals as ``confirm``, plus the stored tape's verdict the caller read (``tape_why``), refused unless the
  steward overrides it (``tape_override`` records that). ``value_source`` ``steward``. Inverse: ``revoke``.
- ``add_steward`` a split NO source lists (framework completion F2-1, migration 0219: the action ``add``), from
  exactly one SEC filing the detector could not place or one split-shaped jump with no split listed, with the
  steward's cited values and the same tape check. Refused when the list already holds it: a version keyed on that
  first session, a live split on it, or a live split of the same exact ratio within the nightly's merge window (so a
  later system decision lands on the steward's row, never on a twin). Inverse: ``revoke`` (``reject`` while
  announced).
- ``reject`` a needs-review (or announced) split. Inverse: ``restore``.
- ``revoke`` a confirmed split, whoever confirmed it. Inverse: ``restore``.
- ``restore`` a rejected or revoked split to EXACTLY the state the reject/revoke replaced — the version
  before it, re-appended (a rejected split goes back to review; a revoked one back to confirmed). A split the
  SYSTEM set aside as a distribution (D2, ``is_auto_set_aside``) goes back to review with the evidence it carries,
  whether it was born set aside (no version before it) or re-decided from review; the restored version names the
  operator, so neither the backfill nor the nightly detector ever touches it again.

**Evidence, not content (W6-1, migration 0216).** ``EVIDENCE_COLUMNS`` ride a version beside its content but are
never compared: Massive.com's label (``polygon_adjustment_type``). A re-run whose only difference is the label is
UNCHANGED, so no stored split is re-versioned for it; a version appended for another reason carries tonight's label,
and every verb carries the label of the version it acts on.

Value-free like every repository: what a split IS comes from the matcher (``domain/splits.py``) and the
operator; this module stores and returns versions. The caller owns the transaction.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
from psycopg import sql as pgsql
from pydantic import BaseModel

from db.change_stamp import mark_changed
from db.public_clock import Clock, public_params, split_versions_sql, to_public_row
from domain.splits import (
    SET_ASIDE,
    SOURCE_POLYGON,
    SOURCE_SEC,
    STATUS_ANNOUNCED,
    STATUS_CONFIRMED,
    STATUS_NEEDS_REVIEW,
    STATUS_REJECTED,
    STATUS_REVOKED,
    VIA_OPERATOR,
    SplitDecision,
    ratio_label,
    same_ratio,
)

# D3 (migration 0215): the row carries the steward's own cited values (``domain.splits`` names the other three)
SOURCE_STEWARD = "steward"

AUTHOR_BACKFILL = "system:backfill_splits"
# S4 — the nightly detector (``pipeline/detect_splits.py``); its versions carry action ``detect`` (0061)
AUTHOR_DETECT = "system:detect_splits"
AUTHOR_OPERATOR = "operator"

ACTION_BACKFILL = "backfill"
ACTION_DETECT = "detect"
ACTION_CONFIRM = "confirm"
ACTION_REJECT = "reject"
ACTION_REVOKE = "revoke"
ACTION_RESTORE = "restore"
# F2-1 (migration 0219): the steward's add of a split no source lists — the first version of a new logical split
ACTION_ADD = "add"

# the CONTENT of a version — everything a re-run compares to decide "unchanged" (identity, authorship and the
# version stamp excluded)
CONTENT_COLUMNS: tuple[str, ...] = (
    "effective_date",
    "ratio_from",
    "ratio_to",
    "status",
    "confirmed_via",
    "value_source",
    "sec_accession",
    "sec_form",
    "sec_filed",
    "sec_url",
    "sec_passage",
    "sec_document",
    "sec_effective_date",
    "sec_date_kind",
    "sec_ratio_from",
    "sec_ratio_to",
    "sec_abstain_reason",
    "polygon_id",
    "polygon_ticker",
    "polygon_execution_date",
    "polygon_split_from",
    "polygon_split_to",
    "note",
    "valid_from",
    "valid_to",
    # D3 (0215): the steward's citation and tape override — NULL on every system version, so a system re-run's
    # content compare is unchanged
    "cited_url",
    "tape_override",
)
# W6-1 (0216): stored and carried, NEVER compared — Massive.com's label is evidence about the row, and as content it
# would re-version every backfill-authored split on the next backfill (``backfill_splits.plan_against_stored``)
EVIDENCE_COLUMNS: tuple[str, ...] = ("polygon_adjustment_type",)

_LIVE = (STATUS_ANNOUNCED, STATUS_CONFIRMED, STATUS_NEEDS_REVIEW)


class SplitConflict(Exception):
    """A verb the split's current state does not allow (stale version, wrong status, no such source value,
    a re-date onto another live split). The API maps it to **409**; the message is operator-facing.
    """


class SplitVersion(BaseModel):
    """One stored version of one split (every column of ``security_split``; ``ticker`` / ``name`` joined from
    the master when the read asks for them)."""

    id: UUID
    tenant_id: UUID
    security_id: UUID
    key_date: date
    effective_date: date
    ratio_from: Decimal
    ratio_to: Decimal
    price_factor: Decimal
    status: str
    confirmed_via: str | None = None
    value_source: str
    action: str
    author: str
    sec_accession: str | None = None
    sec_form: str | None = None
    sec_filed: date | None = None
    sec_url: str | None = None
    sec_passage: str | None = None
    sec_document: str | None = None
    sec_effective_date: date | None = None
    sec_date_kind: str | None = None
    sec_ratio_from: Decimal | None = None
    sec_ratio_to: Decimal | None = None
    sec_abstain_reason: str | None = None
    polygon_id: str | None = None
    polygon_ticker: str | None = None
    polygon_execution_date: date | None = None
    polygon_split_from: Decimal | None = None
    polygon_split_to: Decimal | None = None
    polygon_adjustment_type: str | None = None
    note: str | None = None
    valid_from: date
    valid_to: date | None = None
    cited_url: str | None = None
    tape_override: bool | None = None
    recorded_at: datetime
    ticker: str | None = None
    name: str | None = None

    def content(self) -> tuple[Any, ...]:
        return content_of(self.model_dump())


def _norm(v: Any) -> Any:
    # numerics compare by VALUE (Decimal('16') == Decimal('16.000')), everything else as-is
    if isinstance(v, Decimal):
        return v.normalize()
    return v


def content_of(values: dict[str, Any]) -> tuple[Any, ...]:
    """The comparable content of a version (or of the values about to be appended)."""
    return tuple(_norm(values.get(c)) for c in CONTENT_COLUMNS)


def decision_values(d: SplitDecision) -> dict[str, Any]:
    """A matcher decision as the columns a system version carries (an SEC-only decision has no Massive.com side:
    its ``polygon_*`` columns are NULL): the content, and the evidence (Massive.com's label)."""
    s, p = d.sec, d.polygon
    return {
        "effective_date": d.effective_date,
        "ratio_from": d.ratio_from,
        "ratio_to": d.ratio_to,
        "status": d.status,
        "confirmed_via": d.confirmed_via,
        "value_source": d.value_source,
        "sec_accession": s.accession if s else None,
        "sec_form": s.form if s else None,
        "sec_filed": s.filed if s else None,
        "sec_url": s.url if s else None,
        "sec_passage": (s.passage or None) if s else None,
        "sec_document": s.document if s else None,
        "sec_effective_date": s.effective_date if s else None,
        "sec_date_kind": s.date_kind if s else None,
        "sec_ratio_from": s.ratio_from if s else None,
        "sec_ratio_to": s.ratio_to if s else None,
        "sec_abstain_reason": s.abstain_reason if s else None,
        "polygon_id": p.id if p else None,
        "polygon_ticker": p.ticker if p else None,
        "polygon_execution_date": p.execution_date if p else None,
        "polygon_split_from": p.split_from if p else None,
        "polygon_split_to": p.split_to if p else None,
        "polygon_adjustment_type": p.adjustment_type if p else None,
        "note": d.note,
        "valid_from": d.valid_from,
        "valid_to": None,
    }


# --- reads -------------------------------------------------------------------------------------------------

_SELECT = """
    SELECT s.*, sm.ticker AS ticker, sm.name AS name
      FROM security_split s
      JOIN security_master sm ON sm.id = s.security_id
"""


def latest_versions(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID,
    known_at: datetime | None = None,
    asof: date | None = None,
    security_ids: list[UUID] | None = None,
) -> list[SplitVersion]:
    """THE as-of read: the newest version of every logical split recorded by ``known_at`` (default: the DB
    clock now), optionally only those publicly known by ``asof`` and only for ``security_ids``. Ordered by
    security, then key date."""
    where = [
        "s.tenant_id = %(tenant_id)s",
        "s.recorded_at <= COALESCE(%(known_at)s::timestamptz, clock_timestamp())",
    ]
    if asof is not None:
        where.append("s.valid_from <= %(asof)s")
    if security_ids is not None:
        where.append("s.security_id = ANY(%(ids)s)")
    sql = (
        f"SELECT * FROM (SELECT DISTINCT ON (s.security_id, s.key_date) s.*, sm.ticker AS ticker, "
        f"sm.name AS name FROM security_split s JOIN security_master sm ON sm.id = s.security_id "
        f"WHERE {' AND '.join(where)} "
        f"ORDER BY s.security_id, s.key_date, s.recorded_at DESC, s.id DESC) x "
        f"ORDER BY x.security_id, x.key_date"
    )
    with conn.cursor() as cur:
        cur.execute(
            sql,  # noqa: S608 — every clause above is a literal; values are bound parameters
            {"tenant_id": tenant_id, "known_at": known_at, "asof": asof, "ids": security_ids},
        )
        return [SplitVersion(**r) for r in cur.fetchall()]


def current_rows(
    conn: psycopg.Connection, *, tenant_id: UUID, security_ids: list[UUID]
) -> dict[UUID, list[dict[str, Any]]]:
    """The split list's CURRENT reading on the PUBLIC clock (split plan S2): per security, the newest version
    of each logical split, its ``recorded_at`` rewritten to the split's PUBLIC instant and the honest stamp
    kept as ``ingested_at`` — exactly the rows the backtest's public mirror stores (``db.public_clock.
    split_versions_sql``, the one builder both run). Ungated: a history view reads its own day's list from it
    (``as_of_rows(clock="public")``) and converts its price bars with it (``domain.splits.on_view_scale``,
    which needs the splits the STORE carries too, public or not by the view's day). An entry for EVERY
    requested id (``[]`` when none)."""
    out: dict[UUID, list[dict[str, Any]]] = {sid: [] for sid in security_ids}
    if not security_ids:
        return out
    query = split_versions_sql(
        where=pgsql.SQL("s.tenant_id = %(tenant_id)s AND s.security_id = ANY(%(ids)s)")
    )
    with conn.cursor() as cur:
        cur.execute(query, {"tenant_id": tenant_id, "ids": list(security_ids), **public_params()})
        for r in cur.fetchall():
            out.setdefault(r["security_id"], []).append(to_public_row(dict(r)))
    return out


def as_of_rows(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID,
    security_ids: list[UUID],
    known_at: datetime,
    clock: Clock = "record",
) -> dict[UUID, list[dict[str, Any]]]:
    """The split list as the CALL path reads it (split plan S2): per security, the newest version of each
    logical split as the view knows it — the table's own columns only (no master join), every status, so
    the replay mirror's twin (``replay.pit.ReplayPointInTimeData.split_events``) returns the same rows. An
    entry for EVERY requested id (``[]`` when none), so "nothing listed" is memoized.

    - ``clock="record"`` (the LIVE read): the newest version recorded by ``known_at`` (``DISTINCT ON`` the
      natural key, ``recorded_at DESC, id DESC`` — the ``latest_versions`` pick).
    - ``clock="public"`` (a HISTORY view): the newest version of each split PUBLIC by ``known_at`` — the
      split's own publication (``db.public_clock``), never the day we added the row (``current_rows``,
      gated). No valid-time gate on either clock: a reader decides by first session (``domain.splits``).
    """
    if clock == "public":
        current = current_rows(conn, tenant_id=tenant_id, security_ids=security_ids)
        return {
            sid: [r for r in rows if r["recorded_at"] <= known_at] for sid, rows in current.items()
        }
    out: dict[UUID, list[dict[str, Any]]] = {sid: [] for sid in security_ids}
    if not security_ids:
        return out
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (security_id, key_date) * FROM security_split "
            "WHERE tenant_id = %s AND security_id = ANY(%s) AND recorded_at <= %s "
            "ORDER BY security_id, key_date, recorded_at DESC, id DESC",
            (tenant_id, list(security_ids), known_at),
        )
        for r in cur.fetchall():
            out.setdefault(r["security_id"], []).append(dict(r))
    return out


def history(
    conn: psycopg.Connection, *, tenant_id: UUID, security_id: UUID, key_date: date
) -> list[SplitVersion]:
    """Every version of one logical split, oldest first."""
    with conn.cursor() as cur:
        cur.execute(
            _SELECT + " WHERE s.tenant_id = %s AND s.security_id = %s AND s.key_date = %s "
            "ORDER BY s.recorded_at, s.id",
            (tenant_id, security_id, key_date),
        )
        return [SplitVersion(**r) for r in cur.fetchall()]


def get_version(conn: psycopg.Connection, version_id: UUID) -> SplitVersion | None:
    with conn.cursor() as cur:
        cur.execute(_SELECT + " WHERE s.id = %s", (version_id,))
        row = cur.fetchone()
    return SplitVersion(**row) if row else None


def count_rows(conn: psycopg.Connection, *, tenant_id: UUID | None = None) -> int:
    """``count(*)`` of the TABLE (every version) — what an idempotency check counts."""
    with conn.cursor() as cur:
        if tenant_id is None:
            cur.execute("SELECT count(*) AS n FROM security_split")
        else:
            cur.execute(
                "SELECT count(*) AS n FROM security_split WHERE tenant_id = %s", (tenant_id,)
            )
        return int(cur.fetchone()["n"])


def count_needs_review(conn: psycopg.Connection, *, tenant_id: UUID) -> int:
    """How many logical splits stand at ``needs_review`` now (the Admin status count)."""
    return sum(
        1 for v in latest_versions(conn, tenant_id=tenant_id) if v.status == STATUS_NEEDS_REVIEW
    )


# --- writes ------------------------------------------------------------------------------------------------


def append_version(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID,
    security_id: UUID,
    key_date: date,
    values: dict[str, Any],
    action: str,
    author: str,
) -> SplitVersion:
    """INSERT one version (``recorded_at`` = the column default, the DB's now() — never passed, never
    backdated): its content and its evidence. Returns it as stored."""
    stored = (*CONTENT_COLUMNS, *EVIDENCE_COLUMNS)
    cols = ["tenant_id", "security_id", "key_date", "action", "author", *stored]
    row = {
        "tenant_id": tenant_id,
        "security_id": security_id,
        "key_date": key_date,
        "action": action,
        "author": author,
        **{c: values.get(c) for c in stored},
    }
    placeholders = ", ".join(f"%({c})s" for c in cols)
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO security_split ({', '.join(cols)}) VALUES ({placeholders}) "  # noqa: S608 — literal columns
            "RETURNING id",
            row,
        )
        new_id = cur.fetchone()["id"]
    # the split list changes the company's price scale and market cap: move its change stamp (dashboards)
    mark_changed(conn, [security_id])
    out = get_version(conn, new_id)
    assert out is not None
    return out


def _lock(conn: psycopg.Connection, v: SplitVersion) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"security_split:{v.tenant_id}:{v.security_id}:{v.key_date.isoformat()}",),
        )


def _current(conn: psycopg.Connection, version_id: UUID, *, tenant_id: UUID) -> SplitVersion:
    """The split ``version_id`` belongs to, locked — and refused unless ``version_id`` IS its newest version
    (a click on a stale card never lands over a newer decision)."""
    v = get_version(conn, version_id)
    if v is None or v.tenant_id != tenant_id:
        raise LookupError(f"no split version {version_id}")
    _lock(conn, v)
    hist = history(conn, tenant_id=tenant_id, security_id=v.security_id, key_date=v.key_date)
    if not hist or hist[-1].id != v.id:
        raise SplitConflict(
            f"{v.ticker or v.security_id} {v.key_date.isoformat()}: this split changed since you looked — "
            "reload the panel"
        )
    return v


def _carry(v: SplitVersion, **changes: Any) -> dict[str, Any]:
    """``v``'s content and evidence (its Massive.com label rides every verb), with ``changes`` applied."""
    vals = {c: getattr(v, c) for c in (*CONTENT_COLUMNS, *EVIDENCE_COLUMNS)}
    vals.update(changes)
    return vals


def confirm_values(v: SplitVersion, source: str) -> tuple[Decimal, Decimal, date] | None:
    """``(ratio_from, ratio_to, first_session)`` the operator would confirm from ``source``, or ``None`` when
    that source does not state both a ratio and a date."""
    if source == SOURCE_SEC:
        if v.sec_ratio_from is None or v.sec_ratio_to is None or v.sec_effective_date is None:
            return None
        return v.sec_ratio_from, v.sec_ratio_to, v.sec_effective_date
    if source == SOURCE_POLYGON:
        if (
            v.polygon_split_from is None
            or v.polygon_split_to is None
            or v.polygon_execution_date is None
        ):
            return None
        return v.polygon_split_from, v.polygon_split_to, v.polygon_execution_date
    raise ValueError(f"unknown source {source!r} (sec | polygon)")


def confirm_options(v: SplitVersion) -> list[str]:
    """The sources a needs-review split can be confirmed from, ONE per distinct value: two when SEC and
    Massive.com state different values, one when only one source states both, or both state the same.
    """
    if v.status != STATUS_NEEDS_REVIEW:
        return []
    out: list[str] = []
    seen: list[tuple[Decimal, Decimal, date]] = []
    for src in (SOURCE_SEC, SOURCE_POLYGON):
        vals = confirm_values(v, src)
        if vals is None:
            continue
        if any(same_ratio(vals[0], vals[1], s[0], s[1]) and vals[2] == s[2] for s in seen):
            continue
        seen.append(vals)
        out.append(src)
    return out


def is_auto_set_aside(v: SplitVersion) -> bool:
    """A split the SYSTEM set aside as a distribution (D2, ``domain.splits.distribution_reason``): ``rejected`` by
    the backfill or the nightly detector — no system path writes ``rejected`` otherwise."""
    return v.status == STATUS_REJECTED and v.author != AUTHOR_OPERATOR


def allowed_actions(v: SplitVersion) -> list[str]:
    """The verbs the split's current status allows (the panel renders exactly these)."""
    if v.status == STATUS_NEEDS_REVIEW:
        return [*(f"confirm:{s}" for s in confirm_options(v)), ACTION_REJECT]
    if v.status == STATUS_ANNOUNCED:
        return [ACTION_REJECT]
    if v.status == STATUS_CONFIRMED:
        return [ACTION_REVOKE]
    if v.status in SET_ASIDE:
        return [ACTION_RESTORE]
    return []


def confirm(
    conn: psycopg.Connection,
    version_id: UUID,
    *,
    source: str,
    tenant_id: UUID,
    today: date,
    author: str = AUTHOR_OPERATOR,
) -> SplitVersion:
    """CONFIRM a needs-review split with ``source``'s values (the module docstring). ``today`` is the market
    day the "still ahead" test reads."""
    v = _current(conn, version_id, tenant_id=tenant_id)
    if v.status != STATUS_NEEDS_REVIEW:
        raise SplitConflict(
            f"only a split that needs review can be confirmed (this one is {v.status})"
        )
    vals = confirm_values(v, source)
    if vals is None:
        raise SplitConflict(
            f"{'SEC' if source == SOURCE_SEC else 'Massive.com'} states no complete ratio and date for this "
            "split — confirm the other source, or reject it"
        )
    ratio_from, ratio_to, first_session = vals
    _refuse_a_redate_onto_a_live_split(conn, v, first_session, tenant_id=tenant_id)
    status = STATUS_ANNOUNCED if first_session > today else STATUS_CONFIRMED
    return append_version(
        conn,
        tenant_id=tenant_id,
        security_id=v.security_id,
        key_date=v.key_date,
        values=_carry(
            v,
            ratio_from=ratio_from,
            ratio_to=ratio_to,
            effective_date=first_session,
            status=status,
            confirmed_via=VIA_OPERATOR,
            value_source=source,
            cited_url=None,
            tape_override=None,
        ),
        action=ACTION_CONFIRM,
        author=author,
    )


def _refuse_a_redate_onto_a_live_split(
    conn: psycopg.Connection, v: SplitVersion, first_session: date, *, tenant_id: UUID
) -> None:
    """A confirm that re-dates ``v`` onto a date another LIVE split of the same security holds is refused."""
    if first_session == v.key_date:
        return
    for other in latest_versions(conn, tenant_id=tenant_id, security_ids=[v.security_id]):
        if (
            other.key_date != v.key_date
            and other.status in _LIVE
            and other.effective_date == first_session
        ):
            raise SplitConflict(
                f"another split of {v.ticker or v.security_id} is already listed on "
                f"{first_session.isoformat()} ({other.status}) — resolve that one first"
            )


def confirm_steward(
    conn: psycopg.Connection,
    version_id: UUID,
    *,
    ratio_from: Decimal,
    ratio_to: Decimal,
    first_session: date,
    cited_url: str,
    note: str | None,
    tape_why: str | None,
    override_tape: bool,
    tenant_id: UUID,
    today: date,
    author: str = AUTHOR_OPERATOR,
) -> SplitVersion:
    """CONFIRM a needs-review split with the steward's own CITED values (decision D3): ``ratio_from`` old shares ->
    ``ratio_to`` new shares, first trading on ``first_session``, as the document at ``cited_url`` states them.
    The same refusals as ``confirm`` (unknown version, a stale card, not awaiting review, a re-date onto another
    live split); then ``tape_why`` — why the stored tape at ``first_session`` does not support the values, read by
    the caller (``None`` = it does) — is refused unless ``override_tape``. ``value_source`` ``steward``,
    ``confirmed_via`` ``operator``; ``tape_override`` records whether the steward confirmed past the tape. ``note``
    (the steward's) replaces the carried note when given. The evidence the split carried (both sources) rides on.
    """
    v = _current(conn, version_id, tenant_id=tenant_id)
    if v.status != STATUS_NEEDS_REVIEW:
        raise SplitConflict(
            f"only a split that needs review can be confirmed (this one is {v.status})"
        )
    if not cited_url:
        raise ValueError("cited values need a cited URL")
    if ratio_from <= 0 or ratio_to <= 0 or ratio_from == ratio_to:
        raise ValueError(f"{ratio_from} -> {ratio_to} is not a split ratio")
    _refuse_a_redate_onto_a_live_split(conn, v, first_session, tenant_id=tenant_id)
    if tape_why is not None and not override_tape:
        raise SplitConflict(
            f"{tape_why} — check the values against the cited document; confirm with the tape override if "
            "the document is right"
        )
    status = STATUS_ANNOUNCED if first_session > today else STATUS_CONFIRMED
    return append_version(
        conn,
        tenant_id=tenant_id,
        security_id=v.security_id,
        key_date=v.key_date,
        values=_carry(
            v,
            ratio_from=ratio_from,
            ratio_to=ratio_to,
            effective_date=first_session,
            status=status,
            confirmed_via=VIA_OPERATOR,
            value_source=SOURCE_STEWARD,
            cited_url=cited_url,
            tape_override=tape_why is not None,
            note=note or v.note,
        ),
        action=ACTION_CONFIRM,
        author=author,
    )


# F2-1: the note an added split carries when the steward gives none (its provenance follows)
ADDED_NOTE = "Added by the steward: no source lists this split."


def _provenance(
    source_filing: Mapping[str, Any] | None, source_jump: Mapping[str, Any] | None
) -> str:
    """Where an added split came from, in the steward's words: the filing (its form, accession, filing day and the
    reader's reason it was not placed) or the jump (its step date and step)."""
    if source_filing is not None:
        f = source_filing
        reason = str(f.get("reason") or "").strip().rstrip(".")
        said = f": {reason}" if reason else ""
        return f"From {f.get('form') or 'filing'} {f['accession']} filed {f['filed'].isoformat()}{said}."
    assert source_jump is not None
    step = float(source_jump["step"])
    how = f"x{step:.3g}" if step >= 1 else f"down to 1/{1 / step:.3g}"
    return f"From the split-shaped jump on {source_jump['step_date'].isoformat()}, {how}."


def _stated(source_filing: Mapping[str, Any] | None) -> tuple[Decimal, Decimal] | None:
    """The exact ratio the source filing states (both sides above 0), or ``None``."""
    if source_filing is None:
        return None
    rf, rt = source_filing.get("ratio_from"), source_filing.get("ratio_to")
    if rf is None or rt is None or Decimal(rf) <= 0 or Decimal(rt) <= 0:
        return None
    return Decimal(rf), Decimal(rt)


def _refuse_a_listed_split(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID,
    security_id: UUID,
    who: str,
    ratio_from: Decimal,
    ratio_to: Decimal,
    first_session: date,
    near_days: int,
) -> None:
    """An add of a split the list already holds is refused: a version keyed on ``first_session`` (any status: act
    on it), a LIVE split whose first session it is, or a LIVE split of the same exact ratio within ``near_days``
    (the nightly's own merge window, ``pipeline.backfill_splits.match_stored``: a later system decision about the
    split then lands on the steward's row, never on a twin)."""
    listed = latest_versions(conn, tenant_id=tenant_id, security_ids=[security_id])
    day = first_session.isoformat()
    for v in listed:
        if v.key_date == first_session:
            raise SplitConflict(
                f"a split of {who} is already keyed on {day} ({v.status}) — act on that one instead"
            )
    for v in listed:
        if v.status in _LIVE and v.effective_date == first_session:
            raise SplitConflict(
                f"another split of {who} is already listed on {day} ({v.status}) — resolve that one first"
            )

    def gap(v: SplitVersion) -> int:
        return abs((v.effective_date - first_session).days)

    twins = [
        v
        for v in listed
        if v.status in _LIVE
        and same_ratio(v.ratio_from, v.ratio_to, ratio_from, ratio_to)
        and gap(v) <= near_days
    ]
    if twins:
        v = min(twins, key=lambda v: (gap(v), v.key_date))
        raise SplitConflict(
            f"a {ratio_label(ratio_from, ratio_to)} of {who} is already listed on "
            f"{v.effective_date.isoformat()} ({v.status}), {gap(v)} days from {day}: the nightly reads the two as "
            "one split — confirm that one's values or revoke it first"
        )


def add_steward(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID,
    security_id: UUID,
    ratio_from: Decimal,
    ratio_to: Decimal,
    first_session: date,
    cited_url: str,
    note: str | None,
    tape_why: str | None,
    override_tape: bool,
    today: date,
    near_days: int,
    source_filing: Mapping[str, Any] | None,
    source_jump: Mapping[str, Any] | None = None,
    author: str = AUTHOR_OPERATOR,
) -> SplitVersion:
    """ADD a split no source lists (framework completion F2-1): ``ratio_from`` old shares -> ``ratio_to`` new shares,
    first trading on ``first_session``, as the document at ``cited_url`` states them — the first version of a new
    logical split keyed on ``first_session``, action ``add``.

    It starts from exactly one signal (D-F2a): ``source_filing`` — an unplaced SEC filing as the caller read it
    (``accession``, ``form``, ``filed``, ``url``, ``passage``, ``document``, ``ratio_from`` / ``ratio_to`` when it
    states one, ``reason``) — or ``source_jump`` (``step_date``, ``step``). Refusals: ``ValueError`` on a bad ratio, a
    blank URL or not exactly one signal; ``LookupError`` for a security not on ``tenant_id``; then, under an advisory
    lock on the security's adds and on the key, ``SplitConflict`` when the list already holds the split
    (``_refuse_a_listed_split``, ``near_days``), or when ``tape_why`` (the stored tape's verdict the caller read,
    ``None`` = it supports the values) is set and ``override_tape`` is not.

    The version: ``announced`` when ``first_session`` is after ``today``, else ``confirmed``; ``confirmed_via``
    ``operator``, ``value_source`` ``steward``, the URL, ``tape_override`` = the steward added past the tape; the
    filing as its SEC side (its stated ratio, its reason as ``sec_abstain_reason``), no Massive.com side. ``note`` =
    the steward's, else ``ADDED_NOTE`` plus the provenance. ``valid_from`` = the filing's day when that filing states
    the same exact ratio and was filed before the first session, else the first session: never early.
    Inverse: ``revoke`` (``reject`` while announced); ``restore`` returns it.
    """
    if not cited_url:
        raise ValueError("cited values need a cited URL")
    if ratio_from <= 0 or ratio_to <= 0 or ratio_from == ratio_to:
        raise ValueError(f"{ratio_from} -> {ratio_to} is not a split ratio")
    if (source_filing is None) == (source_jump is None):
        raise ValueError("an add starts from exactly one unplaced filing or one split-shaped jump")
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ticker FROM security_master WHERE id = %s AND tenant_id = %s",
            (security_id, tenant_id),
        )
        master = cur.fetchone()
        if master is None:
            raise LookupError(f"no security {security_id}")
        for key in (
            f"security_split_add:{tenant_id}:{security_id}",
            f"security_split:{tenant_id}:{security_id}:{first_session.isoformat()}",
        ):
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (key,))
    _refuse_a_listed_split(
        conn,
        tenant_id=tenant_id,
        security_id=security_id,
        who=master["ticker"] or str(security_id),
        ratio_from=ratio_from,
        ratio_to=ratio_to,
        first_session=first_session,
        near_days=near_days,
    )
    if tape_why is not None and not override_tape:
        raise SplitConflict(
            f"{tape_why} — check the values against the cited document; add it with the tape override if the "
            "document is right"
        )
    f = source_filing or {}
    stated = _stated(source_filing)
    valid_from = first_session
    if stated is not None and same_ratio(ratio_from, ratio_to, *stated):
        valid_from = min(first_session, f["filed"])
    return append_version(
        conn,
        tenant_id=tenant_id,
        security_id=security_id,
        key_date=first_session,
        values={
            "effective_date": first_session,
            "ratio_from": ratio_from,
            "ratio_to": ratio_to,
            "status": STATUS_ANNOUNCED if first_session > today else STATUS_CONFIRMED,
            "confirmed_via": VIA_OPERATOR,
            "value_source": SOURCE_STEWARD,
            "sec_accession": f.get("accession"),
            "sec_form": f.get("form"),
            "sec_filed": f.get("filed"),
            "sec_url": f.get("url"),
            "sec_passage": f.get("passage"),
            "sec_document": f.get("document"),
            "sec_ratio_from": stated[0] if stated else None,
            "sec_ratio_to": stated[1] if stated else None,
            "sec_abstain_reason": f.get("reason") or None,
            "note": note or f"{ADDED_NOTE} {_provenance(source_filing, source_jump)}",
            "valid_from": valid_from,
            "cited_url": cited_url,
            "tape_override": tape_why is not None,
        },
        action=ACTION_ADD,
        author=author,
    )


def reject(
    conn: psycopg.Connection, version_id: UUID, *, tenant_id: UUID, author: str = AUTHOR_OPERATOR
) -> SplitVersion:
    """REJECT a needs-review or announced split (kept on the list, grayed). Inverse: ``restore``."""
    v = _current(conn, version_id, tenant_id=tenant_id)
    if v.status not in (STATUS_NEEDS_REVIEW, STATUS_ANNOUNCED):
        raise SplitConflict(
            f"only a split that needs review or is announced can be rejected (this one is {v.status}"
            + ("; revoke a confirmed split instead)" if v.status == STATUS_CONFIRMED else ")")
        )
    return append_version(
        conn,
        tenant_id=tenant_id,
        security_id=v.security_id,
        key_date=v.key_date,
        values=_carry(v, status=STATUS_REJECTED),
        action=ACTION_REJECT,
        author=author,
    )


def revoke(
    conn: psycopg.Connection, version_id: UUID, *, tenant_id: UUID, author: str = AUTHOR_OPERATOR
) -> SplitVersion:
    """REVOKE a confirmed split, auto-confirmed or not (kept on the list, grayed). Inverse: ``restore``."""
    v = _current(conn, version_id, tenant_id=tenant_id)
    if v.status != STATUS_CONFIRMED:
        raise SplitConflict(f"only a confirmed split can be revoked (this one is {v.status})")
    return append_version(
        conn,
        tenant_id=tenant_id,
        security_id=v.security_id,
        key_date=v.key_date,
        values=_carry(v, status=STATUS_REVOKED),
        action=ACTION_REVOKE,
        author=author,
    )


def restore(
    conn: psycopg.Connection, version_id: UUID, *, tenant_id: UUID, author: str = AUTHOR_OPERATOR
) -> SplitVersion:
    """RESTORE a rejected or revoked split to exactly the state before the reject/revoke (that version's
    content re-appended) — a split the system set aside as a distribution back to review, with its own content
    (``is_auto_set_aside``)."""
    v = _current(conn, version_id, tenant_id=tenant_id)
    if v.status not in SET_ASIDE:
        raise SplitConflict(
            f"only a rejected or revoked split can be restored (this one is {v.status})"
        )
    if is_auto_set_aside(v):
        # D2: the system's set-aside goes back to review with its own evidence (it may have no version before it)
        values = _carry(v, status=STATUS_NEEDS_REVIEW, confirmed_via=None)
    else:
        hist = history(conn, tenant_id=tenant_id, security_id=v.security_id, key_date=v.key_date)
        if len(hist) < 2:
            raise SplitConflict("nothing to restore to — this split was never in another state")
        values = _carry(hist[-2])
    return append_version(
        conn,
        tenant_id=tenant_id,
        security_id=v.security_id,
        key_date=v.key_date,
        values=values,
        action=ACTION_RESTORE,
        author=author,
    )


def prior_status(conn: psycopg.Connection, v: SplitVersion) -> str | None:
    """The status a ``restore`` would return ``v`` to (``None`` unless ``v`` is set aside)."""
    if v.status not in SET_ASIDE:
        return None
    if is_auto_set_aside(v):
        return STATUS_NEEDS_REVIEW
    hist = history(conn, tenant_id=v.tenant_id, security_id=v.security_id, key_date=v.key_date)
    return hist[-2].status if len(hist) >= 2 else None
