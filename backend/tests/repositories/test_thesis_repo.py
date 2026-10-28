from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import psycopg
import pytest
from pydantic import ValidationError

from db.migrate import MIGRATIONS_DIR
from db.session import DEFAULT_TENANT_ID, connect
from domain.enums import Authorship, ContextRole, TermTier
from domain.thesis import (
    BENCH_DESCRIPTOR,
    CONTEXT_DESCRIPTOR,
    BasketMember,
    Catalyst,
    Evidence,
    KillCriterion,
    Position,
    Segment,
    TermSetEntry,
    Thesis,
    with_silent_links,
)
from repositories import thesis_repo


def _thesis(security_id) -> Thesis:
    return Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name="HIMS — insider conviction",
        narrative="A director bought ~$1.2M open-market off the lows; watching for confirmation.",
        ticker="HIMS",
        segments=[
            Segment(label="Telehealth platforms", descriptor="catalyst-rich"),
            Segment(label="Compounding / supply"),
        ],
        basket=[
            BasketMember(
                ticker="HIMS",
                role="the name",
                security_id=security_id,
                detail="mkt ~$6B",
                segment="Telehealth platforms",
                thesis_fit="the leading US telehealth platform",
                authored_by=Authorship.OPERATOR_SET,
            )
        ],
        evidence=[
            Evidence(
                id=uuid.uuid4(),
                kind="FORM 4",
                label="Director bought $1.17M open-market",
                ref="0001773751-26-000086",
                date_label="1 wk",
            )
        ],
        catalysts=[
            Catalyst(
                id=uuid.uuid4(),
                label="Q2 earnings",
                kind="earnings",
                when_date=date(2026, 8, 4),
                when_label="~Q2",
            )
        ],
        kill_criteria=[
            KillCriterion(id=uuid.uuid4(), text="Closes back below the breakout base on volume")
        ],
    )


def _hydrated(t: Thesis) -> Thesis:
    """What a load returns for ``t``: the same thesis with the two fixed links (Context, Bench) appended last
    — they are added on every read, so a stored thesis never has to carry them."""
    return t.model_copy(update={"segments": with_silent_links(t.segments)})


def _persist(db, t: Thesis) -> None:
    """Upsert + the sole child-list writers: catalysts / kill criteria left ``upsert`` (the structural
    wipe-guard — the same reason as ``set_term_set``), so a full persist goes through their writers.
    """
    thesis_repo.upsert(db, t)
    tenant = t.tenant_id or DEFAULT_TENANT_ID
    thesis_repo.set_catalysts(db, t.id, t.catalysts, tenant_id=tenant)
    thesis_repo.set_kill_criteria(db, t.id, t.kill_criteria, tenant_id=tenant)


def test_upsert_then_get_roundtrips(db, security_id):
    t = _thesis(security_id)
    _persist(db, t)
    db.commit()
    assert thesis_repo.get(db, t.id) == _hydrated(
        t
    )  # full domain round-trip; no raw row escapes the repo


def test_chain_structure_survives_reload(db, security_id):
    """The HARD Workbench-MVP requirement: the value-chain STRUCTURE — the segment list, which name sits
    in which link, and authorship — survives a reload. Proven by reading it back on a SEPARATE connection
    (durable in the store, not just the writer's session view). If this doesn't hold, the MVP isn't done.
    """
    t = _thesis(security_id)
    _persist(db, t)
    db.commit()

    reloaded = connect()  # a fresh connection — the chain must be durable, not session-local
    try:
        got = thesis_repo.get(reloaded, t.id)
    finally:
        reloaded.close()

    assert got is not None
    assert [(s.label, s.descriptor) for s in got.segments[:2]] == [
        ("Telehealth platforms", "catalyst-rich"),
        ("Compounding / supply", None),
    ]
    assert [s.label for s in got.segments[2:]] == ["Context", "Bench"]  # the fixed links, last
    assert got.basket[0].segment == "Telehealth platforms"
    assert (
        got.basket[0].thesis_fit == "the leading US telehealth platform"
    )  # the prose is durable too
    assert got.basket[0].authored_by is Authorship.OPERATOR_SET
    assert got == _hydrated(t)  # full structural round-trip across the reconnect


def test_thesis_rejects_member_in_unknown_segment():
    """A name cannot sit in a link that isn't in the chain — the segment-consistency validator (no DB)."""
    with pytest.raises(ValidationError):
        Thesis(
            id=uuid.uuid4(),
            name="x",
            narrative="x",
            segments=[Segment(label="Reactor developers")],
            basket=[
                BasketMember(
                    ticker="ZZZ",
                    role="r",
                    segment="Fuel & enrichment",  # not among the thesis's segments
                )
            ],
        )


def test_get_missing_returns_none(db):
    assert thesis_repo.get(db, uuid.uuid4()) is None


# --- the persisted, tiered term set (discovery precision; written ONLY by set_term_set) ---


def test_set_term_set_roundtrips(db, security_id):
    """``set_term_set`` is the sole writer of the tiered term set; term + tier + authored_by default + source
    all round-trip through ``get``."""
    t = _thesis(security_id)
    thesis_repo.upsert(db, t)
    db.commit()
    terms = [
        TermSetEntry(term="psilocybin", tier=TermTier.SIGNAL, source="keyword_gen"),
        TermSetEntry(term="MDMA", tier=TermTier.BROAD, source="keyword_gen"),
    ]
    thesis_repo.set_term_set(db, t.id, terms)
    db.commit()
    assert thesis_repo.get(db, t.id).term_set == terms  # full structural round-trip


def test_set_term_set_touches_only_the_term_set(db, security_id):
    """The NARROW writer touches ONLY ``term_set`` — the chain (segments + basket) is untouched."""
    t = _thesis(security_id)
    thesis_repo.upsert(db, t)
    db.commit()
    thesis_repo.set_term_set(db, t.id, [TermSetEntry(term="ibogaine", tier=TermTier.SIGNAL)])
    db.commit()
    got = thesis_repo.get(db, t.id)
    assert [s.label for s in got.segments] == [
        "Telehealth platforms",
        "Compounding / supply",
        "Context",
        "Bench",
    ]
    assert len(got.basket) == 1 and got.basket[0].ticker == "HIMS"
    assert [e.term for e in got.term_set] == ["ibogaine"]


def test_upsert_cannot_blank_a_persisted_term_set(db, security_id):
    """THE STRUCTURAL WIPE-GUARD (the seam where this design would fail SILENTLY): once a term set is produced,
    a later ``upsert`` of the SAME thesis whose object carries an empty ``term_set`` — exactly what ``promote``
    builds from a request that omits it — must NOT blank the stored set. ``upsert`` never names the column, so
    it can't. (A wiped term set is indistinguishable from a never-produced one — that's why this is load-bearing.)
    """
    t = _thesis(security_id)
    thesis_repo.upsert(db, t)
    db.commit()
    thesis_repo.set_term_set(db, t.id, [TermSetEntry(term="psilocin", tier=TermTier.SIGNAL)])
    db.commit()
    # re-upsert the thesis object (its term_set defaults to [], as a promote would) + a narrative edit
    thesis_repo.upsert(db, t.model_copy(update={"narrative": "edited"}))
    db.commit()
    got = thesis_repo.get(db, t.id)
    assert got.narrative == "edited"  # upsert DID write the fields it owns
    assert [e.term for e in got.term_set] == ["psilocin"]  # the term set SURVIVED unblanked


def test_upsert_cannot_blank_authored_catalysts_or_kill_criteria(db, security_id):
    """The term-set structural guard, third instance: ``upsert`` never touches the two child tables,
    so a promote-shaped re-upsert (whose object carries EMPTY lists — exactly what the promote route
    builds from a request that omits them) cannot wipe an authored catalyst surface or kill list."""
    t = _thesis(security_id)
    _persist(db, t)
    db.commit()
    thesis_repo.upsert(
        db, t.model_copy(update={"narrative": "edited", "catalysts": [], "kill_criteria": []})
    )
    db.commit()
    got = thesis_repo.get(db, t.id)
    assert got.narrative == "edited"  # upsert DID write the fields it owns
    assert len(got.catalysts) == 1 and len(got.kill_criteria) == 1  # the lists SURVIVED


def test_upsert_updates_mutable_fields_and_is_idempotent(db, security_id):
    t = _thesis(security_id)
    _persist(db, t)
    db.commit()

    # mutate the narrative + log a fill + RE-SEGMENT the chain (re-label the link, re-place the name,
    # mark it operator-edited), then re-upsert the same thesis id
    t2 = t.model_copy(
        update={
            "narrative": "Position opened; managing to exit-by.",
            "position": Position(entry_price=24.0, opened_on=date(2026, 6, 2)),
            "segments": [Segment(label="Reactor developers", descriptor="re-segmented")],
            "basket": [
                t.basket[0].model_copy(
                    update={
                        "segment": "Reactor developers",
                        "authored_by": Authorship.OPERATOR_EDITED,
                    }
                )
            ],
        }
    )
    thesis_repo.upsert(db, t2)
    db.commit()

    got = thesis_repo.get(db, t.id)
    assert got.narrative == "Position opened; managing to exit-by."
    assert got.position is not None and got.position.opened_on == date(2026, 6, 2)
    assert got.position.entry_price == 24.0
    # the edited chain round-trips: re-labeled segment, re-placed name, edited authorship
    assert [s.label for s in got.segments] == ["Reactor developers", "Context", "Bench"]
    assert got.basket[0].segment == "Reactor developers"
    assert got.basket[0].authored_by is Authorship.OPERATOR_EDITED
    # idempotent: children edited in place by the DELETE-reinsert, never duplicated
    assert len(got.basket) == 1
    assert len(got.evidence) == 1
    assert len(got.catalysts) == 1
    assert len(got.kill_criteria) == 1


# --- TRIAGE: the per-member conviction/size weight ---


def test_conviction_roundtrips(db, security_id):
    """The operator's per-name weight (1–5) persists through the full-replace promote."""
    t = _thesis(security_id)
    t.basket[0].conviction = 4
    thesis_repo.upsert(db, t)
    db.commit()
    assert thesis_repo.get(db, t.id).basket[0].conviction == 4


def test_unset_conviction_stays_none_never_zero(db, security_id):
    """Honest confidence (#6): an unweighted name reads back NULL ("operator hasn't said"), never coerced to 0
    — so future size-weighted attribution can't silently treat unset as zero-weight."""
    t = _thesis(security_id)
    assert t.basket[0].conviction is None  # the default is unset
    thesis_repo.upsert(db, t)
    db.commit()
    got = thesis_repo.get(db, t.id)
    assert got.basket[0].conviction is None  # NOT 0


@pytest.mark.parametrize("bad", [0, 6, -1])
def test_conviction_out_of_range_rejected(bad):
    """The 1–5 scale is validated at the model (no DB): 0 is not "unset" — unset is NULL."""
    with pytest.raises(ValidationError):
        BasketMember(ticker="X", role="r", conviction=bad)


# --- Business-Type M1 (S4): the archetype field is GONE from the spine ---


def test_archetype_is_gone_from_the_spine_and_rejected_loudly():
    """The retirement is total: BasketMember carries no archetype attribute, and a legacy payload
    still sending one FAILS LOUDLY (DomainModel is extra='forbid' — never silently dropped). What a
    name IS lives on the master now (business_type, derive-on-read), not the per-thesis spine."""
    m = BasketMember(ticker="X", role="r")
    assert not hasattr(m, "archetype")
    with pytest.raises(ValidationError):
        BasketMember(ticker="X", role="r", archetype="high_beta")


def test_conviction_survives_a_resave_through_the_mapper(db, security_id):
    """THE WIPE-TRAP guard: every promote reads the basket THROUGH _row_to_basket_member before DELETE+reinsert,
    so an UNMAPPED field is silently wiped on any unrelated resave (a narrative edit). Set
    conviction, then resave the READ-BACK thesis with only the narrative changed — conviction must survive.
    """
    t = _thesis(security_id)
    t.basket[0].conviction = 3
    thesis_repo.upsert(db, t)
    db.commit()

    got = thesis_repo.get(db, t.id)  # read back THROUGH the mapper
    assert got.basket[0].conviction == 3
    # an unrelated edit that resends the read-back basket verbatim (mimics the narrative-edit resave)
    got.narrative = "edited narrative — the basket is resent verbatim"
    thesis_repo.upsert(db, got)
    db.commit()

    assert thesis_repo.get(db, t.id).basket[0].conviction == 3  # not wiped — the mapper carried it


# --- Re-scope S1: surfaced_terms — the frozen discovery-term provenance on the member edge ---


def _member_count(db, thesis_id) -> int:
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM basket_member WHERE thesis_id = %s", (thesis_id,))
        return cur.fetchone()["n"]


def test_surfaced_terms_roundtrips(db, security_id):
    """The frozen provenance persists through upsert and reads back verbatim (order kept — the writer
    freezes a sorted list, the store must not reorder it)."""
    t = _thesis(security_id)
    t.basket[0].surfaced_terms = ["ibogaine", "psilocybin"]
    thesis_repo.upsert(db, t)
    db.commit()
    assert thesis_repo.get(db, t.id).basket[0].surfaced_terms == ["ibogaine", "psilocybin"]


def test_surfaced_terms_default_is_empty_list(db, security_id):
    """The honest empty: an unset field stores '{}' and reads back [] (a hand-added name was surfaced by
    no term) — never None, never a guess."""
    t = _thesis(security_id)
    assert t.basket[0].surfaced_terms == []  # the model default
    thesis_repo.upsert(db, t)
    db.commit()
    assert thesis_repo.get(db, t.id).basket[0].surfaced_terms == []


def test_surfaced_terms_survives_a_resave_through_the_mapper(db, security_id):
    """THE WIPE-TRAP guard, surfaced_terms flavor (the conviction twin): a resave of the READ-BACK basket
    (a narrative edit resends it verbatim) must carry the frozen terms through the mapper — an unmapped
    field would be silently wiped here. AND count the table: the full-replace resave must not grow it
    (the idempotency convention — the read alone can hide a duplicate append)."""
    t = _thesis(security_id)
    t.basket[0].surfaced_terms = ["psilocybin"]
    thesis_repo.upsert(db, t)
    db.commit()
    assert _member_count(db, t.id) == 1

    got = thesis_repo.get(db, t.id)  # read back THROUGH the mapper
    assert got.basket[0].surfaced_terms == ["psilocybin"]
    got.narrative = "edited narrative — the basket is resent verbatim"
    thesis_repo.upsert(db, got)
    db.commit()

    assert thesis_repo.get(db, t.id).basket[0].surfaced_terms == ["psilocybin"]  # not wiped
    assert _member_count(db, t.id) == 1  # the table did not grow


# --- Discovery cleanup S1: signed_off + the multi-membership (N-rows-per-name) basket ---


def test_signed_off_roundtrips_and_survives_a_resave(db, security_id):
    """The endorsement marker persists through upsert, reads back through the mapper, and SURVIVES a
    narrative-edit resave of the read-back basket (the wipe-trap class: an unmapped field is silently
    wiped on any unrelated resave). COUNT THE TABLE before and after — the read alone can hide a
    duplicate append."""
    t = _thesis(security_id)
    t.basket[0].signed_off = True
    thesis_repo.upsert(db, t)
    db.commit()
    assert _member_count(db, t.id) == 1

    got = thesis_repo.get(db, t.id)  # read back THROUGH the mapper
    assert got.basket[0].signed_off is True
    got.narrative = "edited narrative — the basket is resent verbatim"
    thesis_repo.upsert(db, got)
    db.commit()

    assert (
        thesis_repo.get(db, t.id).basket[0].signed_off is True
    )  # not wiped — the mapper carried it
    assert _member_count(db, t.id) == 1  # the table did not grow


def test_signed_off_defaults_false(db, security_id):
    """The honest default: no recorded endorsement → false, at the model AND through the store."""
    t = _thesis(security_id)
    assert t.basket[0].signed_off is False  # the model default
    thesis_repo.upsert(db, t)
    db.commit()
    assert thesis_repo.get(db, t.id).basket[0].signed_off is False


def test_multi_segment_membership_roundtrips_n_rows(db, security_id):
    """THE MULTI-MEMBERSHIP CONTRACT (S1): a name recommended into N links is N basket_member rows —
    same security_id, different segment — and the store round-trips them AS N ROWS (no unique
    (thesis, security_id); the full-replace upsert re-writes exactly what it was given). COUNT THE
    TABLE before and after a verbatim resave: the read's shape alone can hide a duplicate append OR a
    silent squash — the count can't."""
    t = _thesis(security_id)
    t.segments = [Segment(label="Telehealth platforms"), Segment(label="Compounding / supply")]
    t.basket = [
        BasketMember(
            ticker="HIMS",
            role="r",
            security_id=security_id,
            segment="Telehealth platforms",
            thesis_fit="one description, per NAME",
            signed_off=True,
        ),
        BasketMember(
            ticker="HIMS",
            role="r",
            security_id=security_id,
            segment="Compounding / supply",
            thesis_fit="one description, per NAME",
            signed_off=True,
        ),
    ]
    thesis_repo.upsert(db, t)
    db.commit()
    assert _member_count(db, t.id) == 2  # N rows STORED, not squashed

    got = thesis_repo.get(db, t.id)
    assert [(m.ticker, m.segment) for m in got.basket] == [
        ("HIMS", "Telehealth platforms"),
        ("HIMS", "Compounding / supply"),
    ]
    assert all(m.security_id == security_id for m in got.basket)  # ONE name, two memberships
    assert all(m.signed_off for m in got.basket)  # the per-name flag rides every row

    # a verbatim resave of the read-back thesis keeps EXACTLY the N rows (count before + after)
    thesis_repo.upsert(db, got)
    db.commit()
    assert _member_count(db, t.id) == 2
    assert len(thesis_repo.get(db, t.id).basket) == 2


def test_set_surfaced_terms_updates_in_place_and_touches_nothing_else(db, security_id):
    """The narrow writer (the backfill's sole write path, the set_term_set idiom): an UPDATE-in-place that
    freezes the named members' terms and touches NOTHING else — ordinal, authorship, prose, conviction all
    intact, the row count unchanged (no DELETE/INSERT churn). An unknown security_id updates nothing.
    """
    t = _thesis(security_id)
    t.basket[0].conviction = 4
    thesis_repo.upsert(db, t)
    db.commit()

    thesis_repo.set_surfaced_terms(
        db,
        t.id,
        {
            security_id: ["ketamine", "psilocybin"],
            uuid.uuid4(): ["ghost"],  # not a member — updates nothing, never inserts
        },
    )
    db.commit()

    got = thesis_repo.get(db, t.id)
    assert got.basket[0].surfaced_terms == ["ketamine", "psilocybin"]
    # everything else untouched
    assert got.basket[0].conviction == 4
    assert got.basket[0].authored_by is Authorship.OPERATOR_SET
    assert got.basket[0].thesis_fit == "the leading US telehealth platform"
    assert got.basket[0].segment == "Telehealth platforms"
    assert _member_count(db, t.id) == 1  # UPDATE-in-place: no row appeared for the unknown id


# --- F12: basket_snapshot — the roster's point-in-time record (the second bitemporal leak) ---


def _snapshot_count(db, thesis_id) -> int:
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM basket_snapshot WHERE thesis_id = %s", (thesis_id,))
        return cur.fetchone()["n"]


def _pin_latest_snapshot_taken_at(db, thesis_id, when: datetime) -> None:
    """Pin the newest snapshot's ``taken_at`` to a controlled instant — ``upsert`` stamps ``now()``, so
    tests that need a deterministic time gap between two snapshots pin them explicitly."""
    with db.cursor() as cur:
        cur.execute(
            "UPDATE basket_snapshot SET taken_at = %s WHERE id = ("
            "  SELECT id FROM basket_snapshot WHERE thesis_id = %s ORDER BY taken_at DESC, id DESC LIMIT 1"
            ")",
            (when, thesis_id),
        )
    db.commit()


def _second_security(db, ticker: str, cik: str) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, cik, "2026-01-01"),
        )
    db.commit()
    return sid


def _two_pinned_rosters(db, security_id) -> tuple[uuid.UUID, uuid.UUID]:
    """A thesis with two snapshots at controlled times: roster A (1 member) @ 2026-06-01, roster B
    (2 members) @ 2026-06-10. Returns (thesis_id, second_security_id)."""
    t = _thesis(security_id)  # 1 member, HIMS, both segments defined
    thesis_repo.upsert(db, t)
    db.commit()
    _pin_latest_snapshot_taken_at(db, t.id, datetime(2026, 6, 1, tzinfo=timezone.utc))

    sid2 = _second_security(db, "DEVCO2", "0007654321")
    t2 = t.model_copy(
        update={
            "basket": [
                t.basket[0],
                BasketMember(
                    ticker="DEVCO2",
                    role="r",
                    security_id=sid2,
                    segment="Compounding / supply",
                ),
            ]
        }
    )
    thesis_repo.upsert(db, t2)
    db.commit()
    _pin_latest_snapshot_taken_at(db, t.id, datetime(2026, 6, 10, tzinfo=timezone.utc))
    return t.id, sid2


def test_snapshot_written_on_promote_and_deduped_on_unchanged_repromote(db, security_id):
    """A promote writes ONE snapshot; an UNCHANGED re-promote (the Workbench full-replaces on every
    interactive save) writes NONE (dedup on content_hash); a CHANGED roster writes another. COUNT THE
    TABLE before/after — the idempotency convention (a correct read hides a duplicate append)."""
    t = _thesis(security_id)
    thesis_repo.upsert(db, t)
    db.commit()
    assert _snapshot_count(db, t.id) == 1  # promote froze the roster

    # an unchanged re-promote of the read-back thesis must NOT grow the table
    thesis_repo.upsert(db, thesis_repo.get(db, t.id))
    db.commit()
    assert _snapshot_count(db, t.id) == 1  # deduped: identical content_hash

    # a CHANGED roster DOES append a new snapshot
    got = thesis_repo.get(db, t.id)
    got.basket[0].conviction = 5
    thesis_repo.upsert(db, got)
    db.commit()
    assert _snapshot_count(db, t.id) == 2


def test_get_asof_now_matches_live_get(db, security_id):
    """THE LIVE-PATH BYTE-IDENTICAL guarantee at the roster level: after a promote (which seeds a
    snapshot at now()), ``get_asof(now)`` reconstructs the EXACT roster ``get`` reads — so the
    live/today call path is unchanged. The whole thesis round-trips identical too."""
    t = _thesis(security_id)
    thesis_repo.upsert(db, t)
    db.commit()
    live = thesis_repo.get(db, t.id)
    asof_now = thesis_repo.get_asof(db, t.id, datetime.now(timezone.utc))
    assert asof_now is not None
    assert asof_now.basket == live.basket  # byte-identical roster at a live known_at
    assert asof_now == live  # ...and the whole thesis


def test_get_asof_returns_the_older_roster_between_two_snapshots(db, security_id):
    """A known_at BETWEEN two snapshots yields the OLDER roster (the count as it was known then), and a
    known_at after both yields the newer — the roster is versioned by taken_at."""
    tid, _sid2 = _two_pinned_rosters(db, security_id)

    between = datetime(2026, 6, 5, tzinfo=timezone.utc)  # after A (06-01), before B (06-10)
    got = thesis_repo.get_asof(db, tid, between)
    assert [m.ticker for m in got.basket] == ["HIMS"]  # the OLDER 1-member roster
    assert len(got.basket) == 1

    after = datetime(2026, 6, 15, tzinfo=timezone.utc)  # after B
    assert len(thesis_repo.get_asof(db, tid, after).basket) == 2  # the newer 2-member roster


def test_get_asof_no_lookahead_never_reads_a_snapshot_after_known_at(db, security_id):
    """No lookahead (#1): a snapshot taken AFTER known_at is invisible. The 2-member roster is pinned to
    06-10; a known_at of 06-09 sees only the earlier 1-member roster — never the future one — while
    06-10 itself does see it (proving the 2-member snapshot exists and is hidden only by time)."""
    tid, _sid2 = _two_pinned_rosters(db, security_id)

    before_b = datetime(2026, 6, 9, tzinfo=timezone.utc)
    assert len(thesis_repo.get_asof(db, tid, before_b).basket) == 1  # future roster not read

    at_b = datetime(2026, 6, 10, tzinfo=timezone.utc)
    assert len(thesis_repo.get_asof(db, tid, at_b).basket) == 2  # <= known_at is inclusive


def test_basket_size_asof_count_only_pit_read(db, security_id):
    """The COUNT-ONLY PIT read the Scoreboard uses (no full thesis hydration): the roster count as of
    known_at from the latest snapshot <= known_at (same no-lookahead selection as get_asof), else the
    live_fallback. The distinctive live_fallback (99) proves the real historical count is returned, not
    the fallback."""
    tid, _sid2 = _two_pinned_rosters(db, security_id)  # A (1) @ 06-01, B (2) @ 06-10

    between = datetime(2026, 6, 5, tzinfo=timezone.utc)
    assert thesis_repo.basket_size_asof(db, tid, between, live_fallback=99) == 1  # the OLDER count
    after = datetime(2026, 6, 15, tzinfo=timezone.utc)
    assert thesis_repo.basket_size_asof(db, tid, after, live_fallback=99) == 2  # the newer count

    # a known_at before any snapshot -> the live_fallback (no lookahead into the future roster)
    early = datetime(2000, 1, 1, tzinfo=timezone.utc)
    assert thesis_repo.basket_size_asof(db, tid, early, live_fallback=7) == 7

    # no snapshot at all -> the live_fallback (pre-F12 thesis)
    with db.cursor() as cur:
        cur.execute("DELETE FROM basket_snapshot WHERE thesis_id = %s", (tid,))
    db.commit()
    assert thesis_repo.basket_size_asof(db, tid, None, live_fallback=5) == 5


def test_get_asof_with_no_snapshot_falls_back_to_the_live_roster(db, security_id):
    """PRE-SNAPSHOT FALLBACK: a thesis with no qualifying snapshot (promoted before F12, or a known_at
    entirely before its first snapshot) recomputes on the LIVE roster — the honest best available, never
    an empty basket."""
    t = _thesis(security_id)
    thesis_repo.upsert(db, t)
    db.commit()

    # simulate a pre-F12 thesis: basket_member rows present, snapshot dropped
    with db.cursor() as cur:
        cur.execute("DELETE FROM basket_snapshot WHERE thesis_id = %s", (t.id,))
    db.commit()
    assert _snapshot_count(db, t.id) == 0
    got = thesis_repo.get_asof(db, t.id, datetime.now(timezone.utc))
    assert got is not None
    assert [m.ticker for m in got.basket] == ["HIMS"]  # live fallback, not empty

    # a known_at BEFORE the first snapshot (re-created here) also falls back to live
    thesis_repo.upsert(db, thesis_repo.get(db, t.id))
    db.commit()
    early = datetime(2000, 1, 1, tzinfo=timezone.utc)
    assert [m.ticker for m in thesis_repo.get_asof(db, t.id, early).basket] == ["HIMS"]


def test_get_asof_none_known_at_is_now(db, security_id):
    """``known_at=None`` reads the latest snapshot (treated as now) — the live-read default the serve
    path and the cron rely on."""
    t = _thesis(security_id)
    thesis_repo.upsert(db, t)
    db.commit()
    got = thesis_repo.get_asof(db, t.id, None)
    assert got is not None and [m.ticker for m in got.basket] == ["HIMS"]


def test_get_asof_missing_thesis_returns_none(db):
    assert thesis_repo.get_asof(db, uuid.uuid4(), datetime.now(timezone.utc)) is None


def test_deploy_seed_covers_a_snapshotless_thesis(db, security_id):
    """The migration's deploy-seed writes one snapshot per EXISTING thesis. Simulate a pre-F12 thesis
    (basket_member rows, no snapshot), re-run the migration file (idempotent), and confirm exactly one
    snapshot appears covering the live roster — and a second run adds none."""
    t = _thesis(security_id)
    thesis_repo.upsert(db, t)
    db.commit()
    with db.cursor() as cur:
        cur.execute("DELETE FROM basket_snapshot WHERE thesis_id = %s", (t.id,))
    db.commit()
    assert _snapshot_count(db, t.id) == 0

    seed_sql = (MIGRATIONS_DIR / "0043_basket_snapshot.sql").read_text(encoding="utf-8")
    with db.cursor() as cur:
        cur.execute(seed_sql)  # CREATE ... IF NOT EXISTS (no-ops) + the NOT-EXISTS-guarded seed
    db.commit()
    assert _snapshot_count(db, t.id) == 1  # the snapshotless thesis got seeded
    got = thesis_repo.get_asof(db, t.id, datetime.now(timezone.utc))
    assert [m.ticker for m in got.basket] == ["HIMS"]  # the seeded roster == the live roster

    # idempotent: a second run seeds nothing more (the thesis now has a snapshot)
    with db.cursor() as cur:
        cur.execute(seed_sql)
    db.commit()
    assert _snapshot_count(db, t.id) == 1


def test_a_thesis_stored_without_the_fixed_links_reads_back_with_both_appended_last(
    db, security_id
):
    """Context and Bench are added on EVERY load, so a thesis stored before they existed (its ``segments``
    column holds neither) needs no data migration: ``get``, ``get_asof`` and ``list_all`` all return both,
    last, with the fixed descriptors — and the stored column is never rewritten by a read."""
    t = _thesis(security_id)
    thesis_repo.upsert(db, t)
    db.commit()
    with db.cursor() as cur:
        cur.execute("SELECT segments FROM thesis WHERE id = %s", (t.id,))
        stored = cur.fetchone()["segments"]
    assert [s["label"] for s in stored] == ["Telehealth platforms", "Compounding / supply"]

    want = [
        ("Telehealth platforms", "catalyst-rich"),
        ("Compounding / supply", None),
        ("Context", CONTEXT_DESCRIPTOR),
        ("Bench", BENCH_DESCRIPTOR),
    ]
    got = thesis_repo.get(db, t.id)
    asof = thesis_repo.get_asof(db, t.id, datetime(2100, 1, 1, tzinfo=timezone.utc))
    listed = next(x for x in thesis_repo.list_all(db) if x.id == t.id)
    for loaded in (got, asof, listed):
        assert [(s.label, s.descriptor) for s in loaded.segments] == want

    with db.cursor() as cur:  # reading never wrote the links back
        cur.execute("SELECT segments FROM thesis WHERE id = %s", (t.id,))
        assert cur.fetchone()["segments"] == stored


def test_upserting_a_hydrated_thesis_stores_each_fixed_link_once(db, security_id):
    """Round-trip safety: a thesis loaded (links appended) and saved again does not stack a second pair."""
    t = _thesis(security_id)
    thesis_repo.upsert(db, t)
    thesis_repo.upsert(db, thesis_repo.get(db, t.id))
    db.commit()
    assert [s.label for s in thesis_repo.get(db, t.id).segments] == [
        "Telehealth platforms",
        "Compounding / supply",
        "Context",
        "Bench",
    ]


# --- Context roles (Phase 1): the role on the membership, per company per card ----------------------------


def _two_link_company(security_id, role: ContextRole | None) -> Thesis:
    """One company in TWO links (two rows, one security_id), the role (or None) on both."""
    t = _thesis(security_id)
    t.basket = [
        BasketMember(
            ticker="HIMS",
            role="r",
            security_id=security_id,
            segment="Telehealth platforms",
            context_role=role,
        ),
        BasketMember(
            ticker="HIMS",
            role="r",
            security_id=security_id,
            segment="Compounding / supply",
            context_role=role,
        ),
    ]
    return t


def _stored_roles(db, thesis_id) -> list[str | None]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT context_role FROM basket_member WHERE thesis_id = %s ORDER BY ordinal",
            (thesis_id,),
        )
        return [r["context_role"] for r in cur.fetchall()]


def test_snapshot_object_omits_the_context_role_key_when_unset_and_carries_it_when_set(security_id):
    """The snapshot dedup hashes ``md5(members::text)``: a ``"context_role": null`` on every object would move
    every thesis's hash and make its next unchanged re-promote append a redundant row. So the key is emitted
    ONLY when a role is set; an unset member's object is the exact key set the 0043 seed builds."""
    bare = thesis_repo._member_to_snapshot(0, BasketMember(ticker="X", role="r"))
    assert "context_role" not in bare
    assert set(bare) == {
        "ordinal",
        "ticker",
        "role",
        "security_id",
        "detail",
        "segment",
        "thesis_fit",
        "conviction",
        "surfaced_terms",
        "authored_by",
        "signed_off",
    }
    tagged = thesis_repo._member_to_snapshot(
        2, BasketMember(ticker="X", role="r", context_role=ContextRole.COUNTER_CASE)
    )
    assert tagged["context_role"] == "counter_case"  # JSON-native: the value, not the enum member
    assert {k: v for k, v in tagged.items() if k != "context_role"} == {
        **bare,
        "ordinal": 2,
    }


def test_the_mapper_round_trips_a_set_role_and_reads_a_pre_feature_snapshot_as_unset():
    from repositories.mappers import _row_to_basket_member

    obj = thesis_repo._member_to_snapshot(
        0, BasketMember(ticker="X", role="r", context_role=ContextRole.PAYER)
    )
    assert _row_to_basket_member(obj).context_role is ContextRole.PAYER
    # a snapshot written before the feature has no key at all — unset, never an error
    pre_feature = thesis_repo._member_to_snapshot(0, BasketMember(ticker="X", role="r"))
    assert "context_role" not in pre_feature
    assert _row_to_basket_member(pre_feature).context_role is None
    # …and a stored column NULL reads the same way
    assert _row_to_basket_member({**pre_feature, "context_role": None}).context_role is None


def test_context_role_roundtrips_on_every_row_and_get_asof_hydrates_it(db, security_id):
    t = _two_link_company(security_id, ContextRole.PAYER)
    thesis_repo.upsert(db, t)
    db.commit()
    assert _stored_roles(db, t.id) == ["payer", "payer"]  # the column, on BOTH rows
    assert [m.context_role for m in thesis_repo.get(db, t.id).basket] == [ContextRole.PAYER] * 2
    # the snapshot carries it on both rows, and get_asof hydrates the roster from that snapshot
    with db.cursor() as cur:
        cur.execute("SELECT members FROM basket_snapshot WHERE thesis_id = %s", (t.id,))
        (snap,) = cur.fetchall()
    assert [m["context_role"] for m in snap["members"]] == ["payer", "payer"]
    asof = thesis_repo.get_asof(db, t.id, datetime.now(timezone.utc))
    assert [m.context_role for m in asof.basket] == [ContextRole.PAYER] * 2


def test_an_unset_role_is_stored_null_and_hydrates_none_both_live_and_asof(db, security_id):
    t = _two_link_company(security_id, None)
    thesis_repo.upsert(db, t)
    db.commit()
    assert _stored_roles(db, t.id) == [None, None]
    assert [m.context_role for m in thesis_repo.get(db, t.id).basket] == [None, None]
    assert [
        m.context_role for m in thesis_repo.get_asof(db, t.id, datetime.now(timezone.utc)).basket
    ] == [None, None]


def test_the_database_refuses_a_context_role_outside_the_six(db, security_id):
    """Migration 0069's CHECK is the last line: a value the enum would never send (a typo, a seventh role, a
    write that bypassed the model) is refused by the column itself, and a valid one is accepted."""
    t = _two_link_company(security_id, None)
    thesis_repo.upsert(db, t)
    db.commit()
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute(
            "UPDATE basket_member SET context_role = 'supplier' WHERE thesis_id = %s", (t.id,)
        )
    db.rollback()
    db.execute("UPDATE basket_member SET context_role = 'payer' WHERE thesis_id = %s", (t.id,))
    db.commit()
    assert _stored_roles(db, t.id) == ["payer", "payer"]


def test_context_role_survives_a_resave_through_the_mapper(db, security_id):
    """THE WIPE-TRAP guard (the conviction test's twin): a promote reads the basket THROUGH the mapper before
    DELETE+reinsert, so an unmapped column is silently wiped by any unrelated resave."""
    t = _two_link_company(security_id, ContextRole.FINANCIER)
    thesis_repo.upsert(db, t)
    db.commit()
    got = thesis_repo.get(db, t.id)
    got.narrative = "edited narrative — the basket is resent verbatim"
    thesis_repo.upsert(db, got)
    db.commit()
    assert _stored_roles(db, t.id) == ["financier", "financier"]


def test_snapshot_hash_parity_a_roleless_roster_never_grows_the_table_and_a_role_appends_one(
    db, security_id
):
    """COUNT THE TABLE. An unchanged role-less roster re-promoted appends ZERO snapshot rows (the unset key
    is omitted, so its hash is the one already stored); setting a role changes the roster and appends ONE;
    re-sending that same roster appends none; clearing it appends one more (the roster changed back).
    """
    t = _two_link_company(security_id, None)
    thesis_repo.upsert(db, t)
    db.commit()
    assert _snapshot_count(db, t.id) == 1

    thesis_repo.upsert(db, thesis_repo.get(db, t.id))  # unchanged, role-less
    db.commit()
    assert _snapshot_count(db, t.id) == 1

    tagged = thesis_repo.get(db, t.id)
    tagged.basket = [
        m.model_copy(update={"context_role": ContextRole.BENCHMARK}) for m in tagged.basket
    ]
    thesis_repo.upsert(db, tagged)
    db.commit()
    assert _snapshot_count(db, t.id) == 2  # a role is a roster change

    thesis_repo.upsert(db, thesis_repo.get(db, t.id))  # the same tagged roster again
    db.commit()
    assert _snapshot_count(db, t.id) == 2

    cleared = thesis_repo.get(db, t.id)
    cleared.basket = [m.model_copy(update={"context_role": None}) for m in cleared.basket]
    thesis_repo.upsert(db, cleared)
    db.commit()
    assert _snapshot_count(db, t.id) == 3  # clearing is a roster change too
    assert _stored_roles(db, t.id) == [None, None]


def test_a_roleless_repromote_dedups_against_the_0043_deploy_seed(db, security_id):
    """The reason the unset key is omitted: the 0043 seed (which knows nothing of roles) wrote the hash a
    pre-feature thesis carries; a role-less re-promote after this feature ships must hash the same and append
    nothing. Reproduce the seed on a snapshotless thesis, then re-promote unchanged."""
    t = _two_link_company(security_id, None)
    thesis_repo.upsert(db, t)
    db.commit()
    with db.cursor() as cur:
        cur.execute("DELETE FROM basket_snapshot WHERE thesis_id = %s", (t.id,))
        cur.execute((MIGRATIONS_DIR / "0043_basket_snapshot.sql").read_text(encoding="utf-8"))
    db.commit()
    assert _snapshot_count(db, t.id) == 1  # the seed's row

    thesis_repo.upsert(db, thesis_repo.get(db, t.id))
    db.commit()
    assert _snapshot_count(db, t.id) == 1  # same hash as the seed: nothing appended


def test_the_db_check_rejects_a_value_outside_the_six(db, security_id):
    t = _two_link_company(security_id, None)
    thesis_repo.upsert(db, t)
    db.commit()
    with pytest.raises(psycopg.errors.CheckViolation):
        with db.cursor() as cur:
            cur.execute(
                "UPDATE basket_member SET context_role = 'watch' WHERE thesis_id = %s", (t.id,)
            )
    db.rollback()
    with db.cursor() as cur:  # every valid value, and NULL, is accepted
        for v in [r.value for r in ContextRole] + [None]:
            cur.execute(
                "UPDATE basket_member SET context_role = %s WHERE thesis_id = %s", (v, t.id)
            )
    db.rollback()


def test_migration_0069_is_idempotent(db):
    """Re-running the migration file is a clean no-op (the runner tracks the file once; this is the second
    line): the column and the named CHECK end up exactly as one run leaves them."""
    sql = (MIGRATIONS_DIR / "0069_basket_member_context_role.sql").read_text(encoding="utf-8")
    with db.cursor() as cur:
        cur.execute(sql)
        cur.execute(sql)
        cur.execute(
            "SELECT count(*) AS n FROM pg_constraint "
            "WHERE conname = 'basket_member_context_role_check' "
            "AND conrelid = 'basket_member'::regclass"
        )
        assert cur.fetchone()["n"] == 1
    db.commit()
