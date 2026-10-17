"""Admin's "new" markers and inventories agree with the PAGE across a skipped-thesis catch-up.

The T4 lesson: the panel and the page must read ONE baseline rule. After every pass of the real sequences
(``tests/pipeline/test_catchup_baseline.py`` — the real ``run_daily_pass``, the real artifact writer, the
real baseline readers, the real page; ``run_daily`` returns the roster), ``GET /admin/status`` and
``GET /admin/runs`` must say exactly what the page said:

- the freshness panel's ``newly_stale`` = the labels the last pass PAGED;
- its loud rows (neither closed nor acknowledged) = the keys the NEXT pass treats as already told
  (``cron_run_log.previous_stale_tapes``) — so a name the panel shows never re-pages, and one it does not
  show pages when it goes stale;
- the Price feed repairs panel's never-priced rows = the never-priced loud rows of that same inventory;
- the newest run row's ``name_errors_new`` = the newly-failing labels the last pass PAGED.

N here is the WONDF shape: a member never priced (no stored bar) whose price leg 404s every night — so it
sits in both inventories at once and on the Price feed repairs card.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from pipeline import cron_run_log
from tests.pipeline.catchup_pass_stubs import (
    Name,
    covered,
    paged_name_errors,
    paged_tapes,
    prime_pass,
    run_pass,
    skipped,
)

_D1, _D2, _D3 = date(2026, 10, 14), date(2026, 10, 15), date(2026, 10, 16)


def _assert_admin_agrees_with_the_page(client, pages) -> dict:
    status = client.get("/admin/status").json()
    tape = status["tape"]
    assert tape is not None
    assert set(tape["newly_stale"]) == paged_tapes(pages)
    loud = [r for r in tape["stale"] if not r["closed_at"] and not r["acknowledged"]]
    assert {f"{r['kind']}:{r['security_id']}" for r in loud} == cron_run_log.previous_stale_tapes()
    repairs = status["price_repairs"]
    assert {r["security_id"] for r in repairs["unpriceable"]} == {
        r["security_id"] for r in loud if r["unpriceable"]
    }
    run = client.get("/admin/runs?limit=1").json()["runs"][0]
    assert set(run["name_errors_new"]) == paged_name_errors(pages)
    return status


@pytest.fixture
def pager(monkeypatch):
    return prime_pass(monkeypatch)


def test_5_a_name_FIRST_flagged_by_a_catch_up_is_NEW_on_Admin_exactly_when_it_pages(
    client, monkeypatch, pager
):
    """Sequence (1): a full pass (nothing) -> a catch-up skipping A flags N in B -> the next full pass. Admin
    marks N new on the catch-up (the night it paged), lists it from then on, and marks nothing new on the
    next full pass (which pages nothing)."""
    ta, tb = uuid.uuid4(), uuid.uuid4()
    a_ok, b_ok, n = Name("AOK", uuid.uuid4()), Name("BOK", uuid.uuid4()), Name("NNN", uuid.uuid4())
    dead = {"never_priced": (n,), "failing": (n,)}

    _, p1 = run_pass(
        monkeypatch,
        pager,
        [covered(ta, asof=_D1, healthy=a_ok), covered(tb, asof=_D1, healthy=b_ok)],
        asof=_D1,
    )
    s1 = _assert_admin_agrees_with_the_page(client, p1)
    assert s1["tape"]["stale"] == []

    _, p2 = run_pass(
        monkeypatch,
        pager,
        [skipped(ta), covered(tb, asof=_D2, healthy=b_ok, **dead)],
        asof=_D2,
        catch_up=True,
    )
    assert paged_tapes(p2) == {"NNN"} and paged_name_errors(p2) == {"NNN (price)"}
    s2 = _assert_admin_agrees_with_the_page(client, p2)
    assert [r["ticker"] for r in s2["tape"]["stale"]] == ["NNN"]
    assert [r["ticker"] for r in s2["price_repairs"]["unpriceable"]] == ["NNN"]

    _, p3 = run_pass(
        monkeypatch,
        pager,
        [covered(ta, asof=_D3, healthy=a_ok), covered(tb, asof=_D3, healthy=b_ok, **dead)],
        asof=_D3,
    )
    assert not paged_tapes(p3) and not paged_name_errors(p3)
    s3 = _assert_admin_agrees_with_the_page(client, p3)
    assert [r["ticker"] for r in s3["tape"]["stale"]] == ["NNN"]  # still listed, no longer new


def test_5_a_skipped_theses_rows_stay_on_Admin_across_the_catch_up_and_a_return_is_NEW_again(
    client, monkeypatch, pager
):
    """Sequences (3) and (2) on one roster: M is a dead name in A, N a dead name in B. The catch-up skips A
    and covers B, where N has cleared. Admin keeps listing M (A's last covering pass saw it — the page will
    not re-page it), drops N (the catch-up saw it clear), and when N comes back on the next full pass Admin
    marks it new — the night it pages again."""
    ta, tb = uuid.uuid4(), uuid.uuid4()
    a_ok, b_ok = Name("AOK", uuid.uuid4()), Name("BOK", uuid.uuid4())
    m, n = Name("MMM", uuid.uuid4()), Name("NNN", uuid.uuid4())

    def dead(x):
        return {"never_priced": (x,), "failing": (x,)}

    _, p1 = run_pass(
        monkeypatch,
        pager,
        [
            covered(ta, asof=_D1, healthy=a_ok, **dead(m)),
            covered(tb, asof=_D1, healthy=b_ok, **dead(n)),
        ],
        asof=_D1,
    )
    assert paged_tapes(p1) == {"MMM", "NNN"}
    _assert_admin_agrees_with_the_page(client, p1)

    _, p2 = run_pass(
        monkeypatch,
        pager,
        [skipped(ta), covered(tb, asof=_D2, healthy=b_ok)],  # N clear in B
        asof=_D2,
        catch_up=True,
    )
    assert not paged_tapes(p2) and not paged_name_errors(p2)
    s2 = _assert_admin_agrees_with_the_page(client, p2)
    assert sorted(r["ticker"] for r in s2["tape"]["stale"]) == ["MMM"]

    _, p3 = run_pass(
        monkeypatch,
        pager,
        [
            covered(ta, asof=_D3, healthy=a_ok, **dead(m)),
            covered(tb, asof=_D3, healthy=b_ok, **dead(n)),
        ],
        asof=_D3,
    )
    assert paged_tapes(p3) == {"NNN"} and paged_name_errors(p3) == {"NNN (price)"}
    s3 = _assert_admin_agrees_with_the_page(client, p3)
    assert sorted(r["ticker"] for r in s3["tape"]["stale"]) == ["MMM", "NNN"]
