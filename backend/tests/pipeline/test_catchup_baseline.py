"""The stale-tape and name-error BASELINES across a skipped-thesis catch-up — a name pages ONCE.

M2 lets a ``--catch-up`` SKIP the theses that already hold an on-time row for the night, so that pass covers
only the missing ones. The two per-thesis inventories the page diffs against (the newly-stale tapes, G5a/F1,
and the newly-failing names, O1) used to read only FULL-roster passes, which made a catch-up invisible to
the next pass: a name FIRST flagged by the catch-up paged there and AGAIN at the next full pass, and a name
the catch-up saw CLEAR stayed "already told" and stayed silent when it came back. The rule now is a
PER-THESIS carry-forward: each thesis's entries come from the newest finished live pass that COVERED it,
full or partial (``cron_run_log.carried_inventory``).

Every sequence below runs the REAL pass (``tests/pipeline/catchup_pass_stubs.py`` — the real diffs, the
real artifact writer, the real baseline readers, the real page) over the roster ``run_daily`` returns, for
BOTH inventories. The last test runs the whole thing on the real DB with the real ``run_daily`` and a real
catch-up skip (an on-time row in the ``calls`` table), so the skip is the cron's own, not a stub's.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from db.session import DEFAULT_TENANT_ID
from domain.enums import State
from domain.settings import get_settings
from pipeline import cron_run_log, daily
from pipeline.ingest_thesis import NameResult
from repositories import calls_repo
from tests.pipeline.catchup_pass_stubs import (
    Name,
    covered,
    next_second,
    paged_name_errors,
    paged_tapes,
    prime_pass,
    run_pass,
    skipped,
)

# Three nights: a full pass, a killed night's catch-up (the 10-12 shape), the next full pass.
_D1, _D2, _D3 = date(2026, 10, 14), date(2026, 10, 15), date(2026, 10, 16)

INVENTORIES = ("tape", "name_error")


@pytest.fixture
def pager(monkeypatch, tmp_path):
    monkeypatch.setattr(cron_run_log, "_DEFAULT_CRON_RUNS", tmp_path / "cron_runs")
    return prime_pass(monkeypatch)


def _flag(inventory: str, n: Name) -> dict:
    """``covered`` kwargs that put ``n`` in ``inventory``: a stopped price tape, or a failing price leg."""
    return {"stale": (n,)} if inventory == "tape" else {"failing": (n,)}


def _paged(inventory: str, pages: list) -> set[str]:
    return paged_tapes(pages) if inventory == "tape" else paged_name_errors(pages)


def _label(inventory: str, n: Name) -> str:
    return n.ticker if inventory == "tape" else f"{n.ticker} (price)"


def _baseline(inventory: str) -> set[str] | None:
    if inventory == "tape":
        return cron_run_log.previous_stale_tapes()
    return cron_run_log.previous_name_errors()


def _key(inventory: str, n: Name) -> str:
    return f"price:{n.security_id}" if inventory == "tape" else f"{n.security_id}:price"


def _names():
    return Name("AOK", uuid.uuid4()), Name("BOK", uuid.uuid4()), Name("NNN", uuid.uuid4())


# --- (1) first flagged by a catch-up -> pages ONCE ----------------------------------------------------


@pytest.mark.parametrize("inventory", INVENTORIES)
def test_1_a_name_FIRST_flagged_by_a_catch_up_pages_there_ONCE_not_again_at_the_next_full_pass(
    pager, monkeypatch, inventory
):
    """Full pass (nothing) -> a catch-up that skips A and flags N in B -> the next full pass (N still
    flagged in B). N pages exactly once, at the catch-up."""
    ta, tb = uuid.uuid4(), uuid.uuid4()
    a_ok, b_ok, n = _names()

    _, p1 = run_pass(
        monkeypatch,
        pager,
        [covered(ta, asof=_D1, healthy=a_ok), covered(tb, asof=_D1, healthy=b_ok)],
        asof=_D1,
    )
    assert _paged(inventory, p1) == set()

    _, p2 = run_pass(
        monkeypatch,
        pager,
        [skipped(ta), covered(tb, asof=_D2, healthy=b_ok, **_flag(inventory, n))],
        asof=_D2,
        catch_up=True,
    )
    assert _paged(inventory, p2) == {_label(inventory, n)}  # first seen -> paged here

    _, p3 = run_pass(
        monkeypatch,
        pager,
        [
            covered(ta, asof=_D3, healthy=a_ok),
            covered(tb, asof=_D3, healthy=b_ok, **_flag(inventory, n)),
        ],
        asof=_D3,
    )
    assert _paged(inventory, p3) == set()  # ...and NOT again


# --- (2) cleared during a catch-up, then back -> pages again ------------------------------------------


@pytest.mark.parametrize("inventory", INVENTORIES)
def test_2_a_name_the_catch_up_saw_CLEAR_and_that_comes_BACK_pages_again(
    pager, monkeypatch, inventory
):
    """Full pass flags N in B -> a catch-up that COVERS B sees N clear -> the next full pass flags N again.
    A genuine clear-and-return: N pages again."""
    ta, tb = uuid.uuid4(), uuid.uuid4()
    a_ok, b_ok, n = _names()

    _, p1 = run_pass(
        monkeypatch,
        pager,
        [
            covered(ta, asof=_D1, healthy=a_ok),
            covered(tb, asof=_D1, healthy=b_ok, **_flag(inventory, n)),
        ],
        asof=_D1,
    )
    assert _paged(inventory, p1) == {_label(inventory, n)}

    _, p2 = run_pass(
        monkeypatch,
        pager,
        [skipped(ta), covered(tb, asof=_D2, healthy=b_ok)],  # B covered: N is clear
        asof=_D2,
        catch_up=True,
    )
    assert _paged(inventory, p2) == set()

    _, p3 = run_pass(
        monkeypatch,
        pager,
        [
            covered(ta, asof=_D3, healthy=a_ok),
            covered(tb, asof=_D3, healthy=b_ok, **_flag(inventory, n)),
        ],
        asof=_D3,
    )
    assert _paged(inventory, p3) == {_label(inventory, n)}  # back -> news again


# --- (3) flagged in a thesis the catch-up skipped -> no re-page ---------------------------------------


@pytest.mark.parametrize("inventory", INVENTORIES)
def test_3_a_name_held_by_a_thesis_the_catch_up_SKIPPED_does_not_re_page(
    pager, monkeypatch, inventory
):
    """Full pass flags N in A -> a catch-up that SKIPS A (B covered, clean) -> the next full pass flags N in
    A again. The skipped thesis keeps its entries from the last pass that covered it, so N never re-pages.
    """
    ta, tb = uuid.uuid4(), uuid.uuid4()
    a_ok, b_ok, n = _names()
    flagged_a = {"asof": _D1, "healthy": a_ok, **_flag(inventory, n)}

    _, p1 = run_pass(
        monkeypatch,
        pager,
        [covered(ta, **flagged_a), covered(tb, asof=_D1, healthy=b_ok)],
        asof=_D1,
    )
    assert _paged(inventory, p1) == {_label(inventory, n)}

    _, p2 = run_pass(
        monkeypatch,
        pager,
        [skipped(ta), covered(tb, asof=_D2, healthy=b_ok)],
        asof=_D2,
        catch_up=True,
    )
    assert _paged(inventory, p2) == set()
    assert _key(inventory, n) in _baseline(inventory)  # A's entry carried across the catch-up

    _, p3 = run_pass(
        monkeypatch,
        pager,
        [covered(ta, **{**flagged_a, "asof": _D3}), covered(tb, asof=_D3, healthy=b_ok)],
        asof=_D3,
    )
    assert _paged(inventory, p3) == set()


# --- (4) old artifacts: no coverage fields ------------------------------------------------------------


def _strip_coverage(path) -> None:
    """Rewrite one artifact the way an older writer left it: no M1 ``status`` and no M2 ``skipped`` keys
    (the per-thesis ``skipped`` and the summary count), so it says nothing about coverage."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc.pop("status", None)
    doc["summary"].pop("skipped", None)
    for t in doc["theses"]:
        t.pop("skipped", None)
    path.write_text(json.dumps(doc), encoding="utf-8")


@pytest.mark.parametrize("inventory", INVENTORIES)
def test_4_an_OLD_artifact_covers_the_theses_it_lists(pager, monkeypatch, inventory):
    """A pre-M1/M2 artifact (no ``status``, no ``skipped``) flags N in A; a catch-up then skips A; the next
    full pass flags N in A again. The old artifact COVERED A (it lists A), so A's entry carries across the
    catch-up and N does not re-page."""
    ta, tb = uuid.uuid4(), uuid.uuid4()
    a_ok, b_ok, n = _names()

    out1, p1 = run_pass(
        monkeypatch,
        pager,
        [
            covered(ta, asof=_D1, healthy=a_ok, **_flag(inventory, n)),
            covered(tb, asof=_D1, healthy=b_ok),
        ],
        asof=_D1,
    )
    assert _paged(inventory, p1) == {_label(inventory, n)}
    _strip_coverage(out1.log_path)

    run_pass(
        monkeypatch,
        pager,
        [skipped(ta), covered(tb, asof=_D2, healthy=b_ok)],
        asof=_D2,
        catch_up=True,
    )
    _, p3 = run_pass(
        monkeypatch,
        pager,
        [
            covered(ta, asof=_D3, healthy=a_ok, **_flag(inventory, n)),
            covered(tb, asof=_D3, healthy=b_ok),
        ],
        asof=_D3,
    )
    assert _paged(inventory, p3) == set()


@pytest.mark.parametrize("inventory", INVENTORIES)
def test_4b_an_OLD_artifact_does_NOT_cover_a_thesis_it_does_not_list(pager, monkeypatch, inventory):
    """The other half of the rule: an old artifact covers ONLY the theses in its per-thesis results. Thesis
    A is newer than it (absent there); a catch-up skips A; the next full pass flags N in A — never seen in A
    by any pass, so it is news and pages."""
    ta, tb = uuid.uuid4(), uuid.uuid4()
    a_ok, b_ok, n = _names()

    out1, _ = run_pass(monkeypatch, pager, [covered(tb, asof=_D1, healthy=b_ok)], asof=_D1)
    _strip_coverage(out1.log_path)
    run_pass(
        monkeypatch,
        pager,
        [skipped(ta), covered(tb, asof=_D2, healthy=b_ok)],
        asof=_D2,
        catch_up=True,
    )
    _, p3 = run_pass(
        monkeypatch,
        pager,
        [
            covered(ta, asof=_D3, healthy=a_ok, **_flag(inventory, n)),
            covered(tb, asof=_D3, healthy=b_ok),
        ],
        asof=_D3,
    )
    assert _paged(inventory, p3) == {_label(inventory, n)}


# --- the fallback: no readable artifact for a thesis -> everything in it is new (loud) ----------------


@pytest.mark.parametrize("inventory", INVENTORIES)
def test_an_UNREADABLE_covering_artifact_falls_back_to_EVERYTHING_NEW_for_that_thesis(
    pager, monkeypatch, inventory
):
    """Full pass flags N in A — and its artifact is then unreadable; a catch-up skips A; the next full pass
    flags N in A. No readable pass ever covered A, so nothing of A's is "already told": N pages again. Loud,
    never silent — a repeat page at worst."""
    ta, tb = uuid.uuid4(), uuid.uuid4()
    a_ok, b_ok, n = _names()

    out1, _ = run_pass(
        monkeypatch,
        pager,
        [
            covered(ta, asof=_D1, healthy=a_ok, **_flag(inventory, n)),
            covered(tb, asof=_D1, healthy=b_ok),
        ],
        asof=_D1,
    )
    out1.log_path.write_text("{ not json", encoding="utf-8")
    run_pass(
        monkeypatch,
        pager,
        [skipped(ta), covered(tb, asof=_D2, healthy=b_ok)],
        asof=_D2,
        catch_up=True,
    )
    _, p3 = run_pass(
        monkeypatch,
        pager,
        [
            covered(ta, asof=_D3, healthy=a_ok, **_flag(inventory, n)),
            covered(tb, asof=_D3, healthy=b_ok),
        ],
        asof=_D3,
    )
    assert _paged(inventory, p3) == {_label(inventory, n)}


@pytest.mark.parametrize("inventory", INVENTORIES)
def test_a_thesis_DROPPED_from_the_roster_takes_its_entries_with_it(pager, monkeypatch, inventory):
    """The roster bound: a thesis absent from the newest pass (archived — `thesis_repo.list_all` lists the
    active ones) contributes nothing, so its frozen entries can never mask a name elsewhere. N is flagged in
    A; A leaves the roster; a catch-up (B covered, C skipped) and a full pass without A; then N goes stale in
    B — it was never told for B and A is gone, so it pages."""
    ta, tb, tc = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    a_ok, b_ok, n = _names()
    c_ok = Name("COK", uuid.uuid4())

    run_pass(
        monkeypatch,
        pager,
        [
            covered(ta, asof=_D1, healthy=a_ok, **_flag(inventory, n)),
            covered(tb, asof=_D1, healthy=b_ok),
            covered(tc, asof=_D1, healthy=c_ok),
        ],
        asof=_D1,
    )
    run_pass(  # A archived; the catch-up skips C, covers B (clean)
        monkeypatch,
        pager,
        [covered(tb, asof=_D2, healthy=b_ok), skipped(tc)],
        asof=_D2,
        catch_up=True,
    )
    assert _key(inventory, n) not in _baseline(inventory)
    _, p3 = run_pass(
        monkeypatch,
        pager,
        [
            covered(tb, asof=_D3, healthy=b_ok, **_flag(inventory, n)),
            covered(tc, asof=_D3, healthy=c_ok),
        ],
        asof=_D3,
    )
    assert _paged(inventory, p3) == {_label(inventory, n)}


# --- the whole sequence on the real DB, with the real run_daily and a real catch-up skip --------------

_UTC = ZoneInfo("UTC")


def _db_now(db) -> datetime:
    with db.cursor() as cur:
        cur.execute("SELECT clock_timestamp() AS t")
        t = cur.fetchone()["t"]
    db.rollback()
    return t.astimezone(timezone.utc)


def _security(db, ticker) -> uuid.UUID:
    """A one-row master entry under its own CIK (a shared CIK would trip the multi-row primary guard)."""
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, f"{sid.int % 10**10:010d}", "2026-01-01"),
        )
    db.commit()
    return sid


def _thesis(db, name, *, created_at: datetime, members: list[tuple[str, uuid.UUID]]) -> uuid.UUID:
    """A thesis that EXISTED before the night under test (``created_at`` explicit — the column default
    would make it younger than the pinned night, so out of that night's roster)."""
    tid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative, created_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            (tid, DEFAULT_TENANT_ID, name, "n", created_at),
        )
        for i, (ticker, sid) in enumerate(members):
            cur.execute(
                "INSERT INTO basket_member "
                "(id, tenant_id, thesis_id, ordinal, ticker, role, security_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (uuid.uuid4(), DEFAULT_TENANT_ID, tid, i, ticker, "—", sid),
            )
    db.commit()
    return tid


def test_the_REAL_pass_a_name_first_flagged_by_a_REAL_catch_up_pages_ONCE(
    db, monkeypatch, tmp_path
):
    """Sequence (1) end to end on the real DB: the real ``run_daily`` loop, its real M2 skip (read from an
    on-time row in the ``calls`` table, written through the cron's own writer as a killed pass leaves it),
    the real recency judgment and per-leg errors, the real artifacts and the real page. Only the network
    legs are stubbed: the per-thesis ingest reports each member's tape edge and leg errors (the FACTS the
    real ingest reports). N is a member of thesis B whose price tape stops (a last bar 40 days back) and
    whose price leg 404s — first seen by the catch-up. It pages there, once, for both inventories.
    """
    home = tmp_path / "cron_runs"
    monkeypatch.setattr(cron_run_log, "_DEFAULT_CRON_RUNS", home)
    cutoff = _db_now(db) - timedelta(hours=1)  # the night's RUN_AT, an hour ago (UTC market zone)
    asof = cutoff.date()
    pinned = get_settings().model_copy(
        update={
            "cron_run_at": cutoff.time().isoformat(),
            "image_sha": "test-sha",
            "tape_stale_days": 5,
            "fund_shares_stale_days": 5,
        }
    )
    real_connect = daily.connect
    pager = prime_pass(monkeypatch)  # stubs the network legs + the lock; run_daily stays REAL
    monkeypatch.setattr(daily, "connect", real_connect)  # ...and so does the pass's DB
    monkeypatch.setattr(daily, "get_settings", lambda: pinned)
    monkeypatch.setattr(daily, "market_tz", lambda: _UTC)
    born = cutoff - timedelta(days=30)
    a_ok, b_ok, n_sid = _security(db, "AOK"), _security(db, "BOK"), _security(db, "NNN")
    ta = _thesis(db, "A", created_at=born, members=[("AOK", a_ok)])
    tb = _thesis(db, "B", created_at=born, members=[("BOK", b_ok), ("NNN", n_sid)])
    tickers = {a_ok: "AOK", b_ok: "BOK", n_sid: "NNN"}
    dead: set[uuid.UUID] = set()  # the names whose tape has stopped and whose price leg 404s

    def _ingest(conn, thesis_id, **k):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT security_id FROM basket_member WHERE thesis_id = %s ORDER BY ordinal",
                (thesis_id,),
            )
            sids = [r["security_id"] for r in cur.fetchall()]
        return [
            NameResult(
                ticker=tickers[s],
                security_id=s,
                form4_appended=0,
                price_bars_appended=0 if s in dead else 1,
                error=f"price: HTTP 404 Not Found for {tickers[s]}" if s in dead else None,
                leg_errors=(
                    (("price", f"HTTP 404 Not Found for {tickers[s]}"),) if s in dead else ()
                ),
                tape_edge=(asof - timedelta(days=40)) if s in dead else asof,
            )
            for s in sids
        ]

    monkeypatch.setattr(daily, "ingest_thesis", _ingest)

    def _pass(*, night: date, catch_up: bool):
        before = len(pager.health)
        next_second()
        out = daily.run_daily_pass(
            asof=night, allow_live=True, notifier=pager, catch_up=catch_up, run_kind="cron"
        )
        return out, pager.health[before:]

    # (a) the previous night's full pass — nothing stale, nothing failing
    out1, p1 = _pass(night=asof - timedelta(days=1), catch_up=False)
    assert not paged_tapes(p1) and not paged_name_errors(p1)
    assert all(r.skipped is None for r in out1.results)

    # (b) tonight's pass is KILLED after recording A on time (its START artifact never finalized)
    cron_run_log.write_run_start(
        asof=asof, allow_live=True, started_at=cutoff + timedelta(seconds=1), run_kind="cron"
    )
    card = daily.call_for_thesis(db, ta, asof, record=False)
    assert card.state is State.INCUBATING
    assert calls_repo.record_if_changed(db, card, DEFAULT_TENANT_ID, run_kind="cron") is True
    db.commit()

    # (c) overnight N's tape stops and its price leg starts 404ing; the boot catch-up skips A, covers B
    dead.add(n_sid)
    out2, p2 = _pass(night=asof, catch_up=True)
    by = {r.thesis_id: r for r in out2.results}
    assert by[ta].skipped and by[tb].skipped is None  # the cron's OWN skip (the on-time row)
    assert paged_tapes(p2) == {"NNN"} and paged_name_errors(p2) == {"NNN (price)"}

    # (d) the next pass covers both theses; N is still dead — it must NOT page again
    _, p3 = _pass(night=asof, catch_up=False)
    assert not paged_tapes(p3) and not paged_name_errors(p3)
    assert cron_run_log.previous_stale_tapes() == {f"price:{n_sid}"}
    assert cron_run_log.previous_name_errors() == {f"{n_sid}:price"}
    db.rollback()
