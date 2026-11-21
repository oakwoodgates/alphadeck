"""V2 W5b: the Slack message a user's flag sends to the steward (``notify.flags``; plan Q5, decisions B4 and W-c).

- one plain message per raised flag, worded per state (the value shown, awaiting the steward, no value yet), with
  the company, the value, what was current when the flag was raised, the note on ONE line and a pointer to the
  steward's company page;
- it says "a user": never an email, a user id, a workspace, a card count or a queue rank (a steward's email in
  ``ratified_by`` is never printed either);
- the note is flattened (``one_line``, its default cap) and every ``& < >`` is escaped at the chokepoint, so a note
  with ``<!channel>``, a link and a newline posts as one inert line;
- the destination: the steward's flag webhook (``ALPHADECK_STEWARD_SLACK_WEBHOOK_URL``), else the env webhook,
  else one log line; a 5-second timeout; a failure or an exception is logged (never the URL) and never raised.

No network: ``httpx.post`` / ``notify.post_text`` are patched.
"""

from __future__ import annotations

import logging
from datetime import date
from uuid import UUID

import httpx
import pytest

import notify
from domain.platform_values import derive_state
from domain.settings import get_settings
from notify import flags as flag_notify
from notify.escape import MAX_VALUE_LENGTH

SID = UUID("11111111-2222-3333-4444-555555555555")
FACT = UUID("aaaaaaaa-0000-0000-0000-000000000001")
CAND = UUID("cccccccc-0000-0000-0000-000000000002")
ORIGIN = "https://deck.example.com"
STEWARD_HOOK = "https://hooks.slack.example/T0/B0/steward"
ENV_HOOK = "https://hooks.slack.example/T0/B0/env"


def _value(by: str | None = "auto", **over) -> dict:
    row = {
        "id": FACT,
        "valid_from": date(2026, 9, 30),
        "shares": 52_113_000,
        "ratified_by": by,
        "source": "10-q-cover",
    }
    row.update(over)
    return row


def _cand(d: date = date(2026, 12, 31), outcome: str = "flag", reason=None) -> dict:
    return {"id": CAND, "outcome": outcome, "event_date": d, "reason": reason}


def _msg(
    st,
    *,
    value_type="shares_outstanding",
    note=None,
    origin=ORIGIN,
    ticker="ACME",
    name="Acme Corp",
):
    return flag_notify.flag_message(
        security_id=SID,
        ticker=ticker,
        name=name,
        value_type=value_type,
        state=st,
        note=note,
        origin=origin,
    )


def _state(picked=None, candidate=None, decision=None):
    return derive_state(
        picked=picked, candidate=candidate, candidate_decision=decision, pinned=False
    )


# --- the text per state -------------------------------------------------------------------------------------------


def test_a_share_count_from_the_filing():
    assert _msg(_state(_value()), note="the cover lists two classes") == (
        "Flag from a user: ACME (Acme Corp), share count\n"
        "Current when flagged: 52,113,000 shares, from the filing, dated 2026-09-30\n"
        "Note: the cover lists two classes\n"
        f"Open: {ORIGIN}/admin/facts/{SID}"
    )


def test_a_value_verified_by_the_steward_never_prints_the_email():
    text = _msg(_state(_value("steward@example.test")))
    assert "verified by the steward" in text
    assert "@" not in text and "steward@example" not in text


def test_a_value_carried_over_from_v1():
    assert (
        "Current when flagged: 52,113,000 shares, verified (carried over from V1), dated 2026-09-30"
        in _msg(_state(_value("operator")))
    )


def test_a_cash_value_from_the_filing():
    text = _msg(_state(_value(shares=None, cash_usd=1.0)), value_type="cash_burn")
    assert text.splitlines()[:2] == [
        "Flag from a user: ACME (Acme Corp), cash and burn",
        "Current when flagged: from the filing, dated 2026-09-30",
    ]


def test_a_value_with_a_newer_filing_pending():
    text = _msg(_state(_value(), _cand(date(2026, 12, 31))))
    assert (
        "Current when flagged: 52,113,000 shares, from the filing, dated 2026-09-30; "
        "a newer filing dated 2026-12-31 awaits the steward"
    ) in text


def test_a_value_awaiting_the_steward_prints_no_number():
    text = _msg(_state(None, {**_cand(date(2026, 9, 30)), "shares": 143_310_556}))
    assert "Current when flagged: awaiting the steward (a filing dated 2026-09-30)" in text
    assert "143" not in text


@pytest.mark.parametrize(
    "candidate,decision,words",
    [
        (None, None, "no value yet (not extracted)"),
        (
            _cand(outcome="nothing", reason="cash-generative"),
            None,
            "no value yet (cash-generative)",
        ),
        (_cand(), "dismiss", "no value yet (dismissed by the steward)"),
    ],
)
def test_no_value_yet(candidate, decision, words):
    assert f"Current when flagged: {words}" in _msg(_state(None, candidate, decision))


def test_no_note_omits_the_line_and_no_origin_names_the_page():
    text = _msg(_state(), origin=None)
    assert text.splitlines() == [
        "Flag from a user: ACME (Acme Corp), share count",
        "Current when flagged: no value yet (not extracted)",
        "Open: Admin > Shared facts > ACME",
    ]


def test_a_company_without_a_ticker_or_name():
    assert _msg(_state(), ticker=None).splitlines()[0] == (
        "Flag from a user: Acme Corp, share count"
    )
    assert _msg(_state(), ticker=None, name=None).splitlines()[0] == (
        "Flag from a user: a company, share count"
    )


def test_the_text_names_no_user_workspace_card_count_or_queue_rank():
    text = _msg(_state(_value("steward@example.test")), note="please check")
    lowered = text.lower()
    for word in ("@", "email", "workspace", "card", "queue", "rank", "user id"):
        assert word not in lowered, word
    assert "a user" in lowered


# --- hostile text -------------------------------------------------------------------------------------------------


@pytest.fixture()
def posted(monkeypatch) -> list[tuple[str, str, float]]:
    sent: list[tuple[str, str, float]] = []

    def fake_post(url, **kw):
        sent.append((url, kw["json"]["text"], kw["timeout"]))
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    return sent


def test_a_hostile_note_posts_as_one_escaped_line(posted, monkeypatch):
    monkeypatch.setenv("ALPHADECK_STEWARD_SLACK_WEBHOOK_URL", STEWARD_HOOK)
    get_settings.cache_clear()
    note = "<!channel> look & see\nFORGED LINE <@U0BAD> <https://evil.example|click>"
    try:
        flag_notify.post_flag(_msg(_state(), note=note, ticker="AC&ME\nFORGED", name="<b>Acme</b>"))
    finally:
        get_settings.cache_clear()
    ((url, text, timeout),) = posted
    assert (url, timeout) == (STEWARD_HOOK, 5.0)
    lines = text.split("\n")
    assert len(lines) == 4, text
    assert lines[2] == (
        "Note: &lt;!channel&gt; look &amp; see FORGED LINE &lt;@U0BAD&gt; "
        "&lt;https://evil.example|click&gt;"
    )
    assert lines[0] == "Flag from a user: AC&amp;ME FORGED (&lt;b&gt;Acme&lt;/b&gt;), share count"
    assert "<" not in text and ">" not in text
    assert not any(line.startswith("FORGED") for line in lines)


def test_a_long_note_is_cut_at_the_escapers_default():
    text = _msg(_state(), note="x" * 2_000)
    (note_line,) = [line for line in text.splitlines() if line.startswith("Note: ")]
    assert len(note_line) == len("Note: ") + MAX_VALUE_LENGTH
    assert note_line.endswith("…")


# --- where it goes, and failure -----------------------------------------------------------------------------------


@pytest.fixture()
def hooks(monkeypatch):
    def set_hooks(steward: str | None, env: str | None) -> None:
        for var, value in (
            ("ALPHADECK_STEWARD_SLACK_WEBHOOK_URL", steward),
            ("SLACK_WEBHOOK_URL", env),
        ):
            if value is None:
                monkeypatch.delenv(var, raising=False)
            else:
                monkeypatch.setenv(var, value)
        get_settings.cache_clear()

    yield set_hooks
    get_settings.cache_clear()


@pytest.fixture()
def calls(monkeypatch) -> list[tuple[str, str, float | None]]:
    seen: list[tuple[str, str, float | None]] = []

    def fake(url, text, *, timeout=None):
        seen.append((url, text, timeout))
        return True, "Slack answered 200"

    monkeypatch.setattr(notify, "post_text", fake)
    return seen


def test_the_stewards_webhook_wins(hooks, calls):
    hooks(STEWARD_HOOK, ENV_HOOK)
    flag_notify.post_flag("hello")
    assert calls == [(STEWARD_HOOK, "hello", 5.0)]


def test_the_env_webhook_is_the_fallback(hooks, calls):
    hooks(None, ENV_HOOK)
    flag_notify.post_flag("hello")
    assert calls == [(ENV_HOOK, "hello", 5.0)]


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_steward_setting_is_unset(hooks, calls, blank):
    hooks(blank, ENV_HOOK)
    flag_notify.post_flag("hello")
    assert calls == [(ENV_HOOK, "hello", 5.0)]


def test_no_webhook_is_one_log_line(hooks, calls, caplog):
    hooks(None, None)
    with caplog.at_level(logging.INFO, logger="alphadeck.flags"):
        flag_notify.post_flag("Flag from a user: ACME\nNote: secret words")
    assert calls == []
    records = [r for r in caplog.records if r.name == "alphadeck.flags"]
    assert len(records) == 1
    assert "secret words" not in records[0].getMessage()


def test_a_slack_failure_is_logged_without_the_url(hooks, monkeypatch, caplog):
    hooks(STEWARD_HOOK, None)
    monkeypatch.setattr(
        notify, "post_text", lambda url, text, *, timeout=None: (False, "Slack answered 500")
    )
    with caplog.at_level(logging.WARNING, logger="alphadeck.flags"):
        flag_notify.post_flag("hello")  # never raises
    messages = [r.getMessage() for r in caplog.records if r.name == "alphadeck.flags"]
    assert len(messages) == 1 and "Slack answered 500" in messages[0]
    assert STEWARD_HOOK not in messages[0] and "hooks.slack" not in messages[0]


def test_a_raising_sender_never_raises(hooks, monkeypatch, caplog):
    hooks(STEWARD_HOOK, None)

    def boom(url, text, *, timeout=None):
        raise RuntimeError(f"exploded posting to {url}")

    monkeypatch.setattr(notify, "post_text", boom)
    with caplog.at_level(logging.WARNING, logger="alphadeck.flags"):
        flag_notify.post_flag("hello")
    messages = [r.getMessage() for r in caplog.records if r.name == "alphadeck.flags"]
    assert len(messages) == 1 and STEWARD_HOOK not in messages[0]
