"""Q13 push policy (operator 2026-10-17: O3 + O4) on REAL recorded calls, through the real cron wiring.

Every card here is a recorded prod call-of-record (``tests/fixtures/push_policy/real_cards.json`` — extracted
READ-ONLY, values verbatim, the projection named in its ``provenance``). Each test seeds the REAL prior row
into ``calls``, stubs only the assembler to return the REAL next row's card, and runs ``daily.run_daily`` — so
the baseline read, the event build, the notify, the record and the digest are the production path. The
outcomes below are what those real nights produce under the new policy; none was chosen to make a test pass.

The cases (MEASURED in the 2026-10-16 audit, A2): the two downgrades that used to read as new arms (DAT
2026-09-23, Nuclear Buildout 2026-10-07), a real upgrade (Nuclear Buildout 2026-10-06), a real opposite-
direction move (DAT 2026-09-24), the 2026-10-16 nightly pass whose already-armed theses gained names, and
the 2026-10-12 boot catch-up recorded late the next afternoon.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from db.session import DEFAULT_TENANT_ID
from domain.call import CallCard
from domain.settings import get_settings
from notify import DigestEvent, SlackNotifier
from pipeline import daily, push_policy
from repositories import calls_repo

_FIXTURE = json.loads(
    (
        Path(__file__).resolve().parents[1] / "fixtures" / "push_policy" / "real_cards.json"
    ).read_text(encoding="utf-8")
)
_NY = ZoneInfo("America/New_York")


def _row(seq: int) -> dict:
    return _FIXTURE["rows"][str(seq)]


def _card(seq: int) -> CallCard:
    return CallCard.model_validate(_row(seq)["card"])


class _Capture:
    """A complete Notifier double: every surface recorded separately."""

    def __init__(self):
        self.events: list = []
        self.digests: list[DigestEvent] = []

    def notify(self, event):
        self.events.append(event)

    def notify_digest(self, event):
        self.digests.append(event)

    def notify_health(self, event):  # pragma: no cover — run_daily never pages health
        pass

    def notify_spac_status(self, event):  # pragma: no cover — the radar leg is not run here
        pass


def _seed(db, *pairs: tuple[int, int]) -> dict[uuid.UUID, CallCard]:
    """Seed each pair's thesis (its REAL id + name), the REAL prior call-of-record, and the master tickers;
    return thesis_id -> the REAL next card the stubbed assembler will hand the cron."""
    with db.cursor() as cur:
        for sid, ticker in _FIXTURE["tickers"].items():
            cur.execute(
                "INSERT INTO security_master (id, tenant_id, ticker, valid_from) VALUES (%s, %s, %s, %s)",
                (sid, DEFAULT_TENANT_ID, ticker, "2026-01-01"),
            )
        for prior_seq, _ in pairs:
            card = _card(prior_seq)
            cur.execute(
                "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, %s, %s)",
                (card.thesis_id, DEFAULT_TENANT_ID, _row(prior_seq)["thesis"], "n"),
            )
    db.commit()
    nxt: dict[uuid.UUID, CallCard] = {}
    for prior_seq, next_seq in pairs:
        calls_repo.append(db, _card(prior_seq), DEFAULT_TENANT_ID)
        nxt[_card(next_seq).thesis_id] = _card(next_seq)
    db.commit()
    return nxt


def _run(db, monkeypatch, nxt: dict[uuid.UUID, CallCard], *, asof: date, now: datetime, cap):
    """One real `run_daily` pass: ingest stubbed (no network), the assembler returns the recorded card, the
    market clock pinned to the instant the real pass ran (so N1's timing label is the real one)."""
    monkeypatch.setattr(daily, "ingest_thesis", lambda *a, **k: [])
    monkeypatch.setattr(daily, "call_for_thesis", lambda conn, thid, a, **k: nxt[thid])
    monkeypatch.setattr(daily, "market_now", lambda: now)
    monkeypatch.setattr(daily, "market_tz", lambda: _NY)
    return {r.thesis_id: r for r in daily.run_daily(db, asof=asof, allow_live=True, notifier=cap)}


# --- O3: the two downgrades that used to read as new arms are QUIET now -----------------------------------


@pytest.mark.parametrize(
    ("prior_seq", "next_seq", "asof", "ran_at", "text"),
    [
        (
            1168,
            1180,
            date(2026, 9, 23),
            datetime(2026, 9, 23, 22, 36, tzinfo=_NY),
            "Digital Asset Treasury Complex — still armed, weaker: core → flip; 4 → 2 names "
            "(core_entry → starter_entry)\n"
            "no longer armed: NAKA (structural break), ASST (fast breakdown); "
            "Momentum-only — not yet volume-confirmed.",
        ),
        (
            1293,
            1305,
            date(2026, 10, 7),
            datetime(2026, 10, 7, 22, 37, tzinfo=_NY),
            "Nuclear Buildout — still armed, weaker: core → flip; 4 → 3 names "
            "(core_entry → starter_entry)\n"
            "no longer armed: GHM (structural break, fast breakdown); "
            "Momentum-only — not yet volume-confirmed.",
        ),
    ],
)
def test_a_real_downgrade_while_armed_is_a_QUIET_push(
    db, monkeypatch, prior_seq, next_seq, asof, ran_at, text
):
    """DAT 2026-09-23 and Nuclear Buildout 2026-10-07 pushed the loud red "ARMED" header although the setup
    got WEAKER (core -> flip, fewer names). Now: a quiet, honest push naming what moved and the reason the
    card carries (which names left, on which de-arm signal). No new name armed, so no digest either.
    """
    nxt = _seed(db, (prior_seq, next_seq))
    cap = _Capture()
    by = _run(db, monkeypatch, nxt, asof=asof, now=ran_at, cap=cap)

    (evt,) = cap.events
    assert (evt.from_state, evt.to_state) == ("armed", "armed")
    assert evt.push_kind == "downgrade" and not evt.loud
    assert SlackNotifier._format(evt) == text  # no 🔴, no "ARMED", no capitals
    assert cap.digests == []  # nothing newly armed: no digest, never a "nothing new" ping
    tid = _card(next_seq).thesis_id
    assert by[tid].recorded is True and len(calls_repo.list_for_thesis(db, tid)) == 2


def test_a_real_upgrade_while_armed_stays_LOUD_and_its_names_are_not_repeated_in_the_digest(
    db, monkeypatch
):
    """Nuclear Buildout 2026-10-06: starter_entry -> core_entry, 2 -> 4 armed names — a genuine strengthening.
    LOUD, the ARMED style with an explicit up marker and what improved; the push lists every armed name, so
    the two that newly armed are NOT repeated in the digest (a suppressed duplicate)."""
    nxt = _seed(db, (1281, 1293))
    cap = _Capture()
    _run(
        db,
        monkeypatch,
        nxt,
        asof=date(2026, 10, 6),
        now=datetime(2026, 10, 6, 22, 38, tzinfo=_NY),
        cap=cap,
    )

    (evt,) = cap.events
    assert evt.push_kind == "upgrade" and evt.loud
    assert SlackNotifier._format(evt) == (
        "🔴 Nuclear Buildout — ARMED ↑ now core, +2 names (starter_entry → core_entry)\n"
        "VST, GHM, CEG, GEV armed\n"
        "VST insider buy, breakout · GHM catalyst, breakout · CEG catalyst, breakout · "
        "GEV catalyst, breakout"
    )
    assert len(push_policy.newly_armed(_card(1281), _card(1293))) == 2  # two names DID newly arm …
    assert cap.digests == []  # … and the loud push already listed them


def test_a_real_opposite_direction_move_is_QUIET_with_both_sides_stated(db, monkeypatch):
    """DAT 2026-09-24: the grade rose (flip -> core) but armed names fell (2 -> 1). A weaker side never rides a
    loud header: QUIET, both changes stated. The name that newly armed (NAKA re-arming core) was not on any
    thesis-level push, so it reaches the operator through the digest."""
    nxt = _seed(db, (1180, 1192))
    cap = _Capture()
    _run(
        db,
        monkeypatch,
        nxt,
        asof=date(2026, 9, 24),
        now=datetime(2026, 9, 24, 22, 36, tzinfo=_NY),
        cap=cap,
    )

    (evt,) = cap.events
    assert evt.push_kind == "mixed" and not evt.loud
    assert SlackNotifier._format(evt) == (
        "Digital Asset Treasury Complex — still armed, mixed: flip → core; 2 → 1 names "
        "(starter_entry → core_entry)\n"
        "no longer armed: RIOT (fast breakdown), MARA (fast breakdown); "
        "The market is confirming (a volume-backed breakout)."
    )
    (digest,) = cap.digests
    assert SlackNotifier._format_digest(digest) == (
        "Newly armed in already-armed theses\n"
        "Digital Asset Treasury Complex: NAKA insider buy, breakout"
    )


# --- O4: the nightly digest on real nights ----------------------------------------------------------------


def test_the_real_2026_10_16_pass_sends_ONE_digest_grouped_by_thesis(db, monkeypatch):
    """The 2026-10-16 nightly pass: Modern Defense (armed, verdict held) gained LMT + HONA, The Everything
    Exchange (armed, verdict held) gained FIS — no thesis-level push for either, so before Q13 these three
    arms pushed nothing. Navy Shipbuilding Rebuild ENTERED Armed the same pass: its loud push (unchanged)
    lists its own names, and a thesis that was not already Armed is never digest news. ONE digest message for
    the pass, grouped by thesis, in the compact #309 shape."""
    nxt = _seed(db, (1404, 1416), (1410, 1422), (1406, 1418))
    cap = _Capture()
    ran = datetime(2026, 10, 16, 22, 36, tzinfo=_NY)
    _run(db, monkeypatch, nxt, asof=date(2026, 10, 16), now=ran, cap=cap)

    (evt,) = cap.events  # only Navy moved state/verdict
    assert evt.thesis_name == "Navy Shipbuilding Rebuild" and evt.push_kind == "armed" and evt.loud
    assert SlackNotifier._format(evt).startswith(
        "🔴 Navy Shipbuilding Rebuild — ARMED (warming → armed)"
    )
    (digest,) = cap.digests
    assert digest.timing == "current" and digest.name_count == 3
    assert SlackNotifier._format_digest(digest) == (
        "Newly armed in already-armed theses\n"
        "Modern Defense: LMT catalyst, breakout · HONA insider buy, breakout\n"
        "The Everything Exchange: FIS insider buy, breakout"
    )

    # a same-day SECOND pass (an Admin "Run daily now" after the nightly): the baseline is the night's own
    # row (F2), the cards are unchanged — no row, no push, no second digest
    cap2 = _Capture()
    _run(db, monkeypatch, nxt, asof=date(2026, 10, 16), now=ran, cap=cap2)
    assert cap2.events == [] and cap2.digests == []


def test_the_real_late_catch_up_of_2026_10_12_labels_its_digest(db, monkeypatch):
    """The 2026-10-12 pass died mid-run; the boot catch-up recorded the remaining theses at 17:04-17:13 ET the
    NEXT day — after the night's on-time window closed. Three already-armed theses gained a name in it
    (Nuclear Buildout PCG, Rainbow Rush CYCN, The Everything Exchange MELI). The digest carries N1's label so
    a catch-up's arms are never read as tonight's."""
    nxt = _seed(db, (1329, 1371), (1330, 1372), (1332, 1374))
    cap = _Capture()
    _run(
        db,
        monkeypatch,
        nxt,
        asof=date(2026, 10, 12),
        now=datetime(2026, 10, 13, 17, 8, tzinfo=_NY),
        cap=cap,
    )

    assert cap.events == []  # no thesis moved state or verdict
    (digest,) = cap.digests
    assert digest.timing == "late"
    text = SlackNotifier._format_digest(digest)
    assert text.splitlines()[0] == (
        "Newly armed in already-armed theses (asof 2026-10-12, recorded late)"
    )
    assert [t.thesis_name for t in digest.theses] == [
        "Nuclear Buildout",
        "Rainbow Rush",
        "The Everything Exchange",
    ]
    assert [[n.ticker for n in t.names] for t in digest.theses] == [["PCG"], ["CYCN"], ["MELI"]]


# --- fail-open: a digest fault never costs a record or another push -----------------------------------------


def test_a_raising_digest_delivery_costs_nothing(db, monkeypatch):
    nxt = _seed(db, (1404, 1416), (1410, 1422))

    class _Boom(_Capture):
        def notify_digest(self, event):
            raise RuntimeError("slack is down")

    cap = _Boom()
    by = _run(
        db,
        monkeypatch,
        nxt,
        asof=date(2026, 10, 16),
        now=datetime(2026, 10, 16, 22, 36, tzinfo=_NY),
        cap=cap,
    )

    assert all(r.recorded is True and r.error is None for r in by.values())  # both records landed


def test_an_unresolvable_ticker_still_COUNTS_the_name(db, monkeypatch):
    """The digest's ticker read failing (or a name with no master row) degrades the line, never drops the
    name (#9): it is counted as "without a ticker"."""
    nxt = _seed(db, (1410, 1422))

    def _boom(*a, **k):
        raise RuntimeError("resolver down")

    monkeypatch.setattr(push_policy.master, "tickers_for", _boom)
    cap = _Capture()
    by = _run(
        db,
        monkeypatch,
        nxt,
        asof=date(2026, 10, 16),
        now=datetime(2026, 10, 16, 22, 36, tzinfo=_NY),
        cap=cap,
    )

    (digest,) = cap.digests
    assert SlackNotifier._format_digest(digest) == (
        "Newly armed in already-armed theses\nThe Everything Exchange: 1 name without a ticker"
    )
    assert next(iter(by.values())).recorded is True


# --- the Slack adapter delivers the classes as built (a real event, the webhook faked) ---------------------


def test_slack_pushes_the_real_downgrade_ONCE_and_QUIET(db, monkeypatch):
    nxt = _seed(db, (1168, 1180))
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.example/T/B/x")
    get_settings.cache_clear()
    posted: list[str] = []

    def fake_post(url, **kw):
        posted.append(kw["json"]["text"])
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    try:
        _run(
            db,
            monkeypatch,
            nxt,
            asof=date(2026, 9, 23),
            now=datetime(2026, 9, 23, 22, 36, tzinfo=_NY),
            cap=SlackNotifier(),
        )
    finally:
        monkeypatch.delenv("SLACK_WEBHOOK_URL")
        get_settings.cache_clear()

    (text,) = posted  # the quiet downgrade, once — no digest (no new name)
    assert (
        text.startswith("Digital Asset Treasury Complex — still armed, weaker") and "🔴" not in text
    )
