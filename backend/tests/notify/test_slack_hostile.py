"""Hostile text reaches Slack INERT (security hardening PR 4a, spec item 4).

Every push template is driven with a user- or feed-authored value that tries Slack's control syntax
(``<!channel>``, a ``<url|label>`` link, a bare ``&``) and a newline that would forge a line of its own. The
posted payload must carry the markup escaped (``&lt;``, ``&gt;``, ``&amp;``) and no forged line. Each template
fills EVERY string it interpolates with the hostile value, so a template that interpolates a value without
the escaper fails here. The log record is checked too: a newline in a value never starts a new log line.

The escaping option taken (reported in the PR): no template uses angle-bracket markup on purpose (the only
deliberate formatting is the line break between a push's lines and literal emoji), so ``&``, ``<`` and ``>``
are escaped ONCE at the chokepoint (``post_text`` and ``notify.page``'s fallback POST), which no future
template can bypass. Because the line breaks ARE deliberate, the chokepoint cannot collapse newlines: each
template flattens the values it interpolates (``notify.escape.one_line``).
"""

from __future__ import annotations

import json
import logging
import urllib.request
from datetime import date
from uuid import uuid4

import httpx
import pytest

from domain.feed_kinds import StaleFeedLabel
from domain.settings import get_settings
from notify import (
    ArmedName,
    DigestEvent,
    DigestThesis,
    EditionCutEvent,
    HealthEvent,
    LogNotifier,
    SlackNotifier,
    SpacStatusEvent,
    SpacThesisMatch,
    TransitionEvent,
    page,
    post_text,
)

_WEBHOOK = "https://hooks.slack.example/T000/B000/hostile"

THESIS = "<!channel> <https://evil.example|click>"
COMPANY = "Smith & Wesson\nFORGED LINE <@U0BADBAD>"
FORGED = "FORGED LINE"


@pytest.fixture(autouse=True)
def _slack_on(monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", _WEBHOOK)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def posted(monkeypatch) -> list[str]:
    texts: list[str] = []

    def fake_post(url, **kw):
        texts.append(kw["json"]["text"])
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    return texts


def _assert_inert(text: str) -> None:
    assert "<" not in text and ">" not in text, text
    assert "&lt;!channel&gt;" in text or "&lt;@U0BADBAD&gt;" in text, text
    for line in text.split("\n"):
        assert not line.startswith(FORGED), text
    # every ampersand is the start of an entity Slack decodes as plain text
    rest = text.replace("&amp;", "").replace("&lt;", "").replace("&gt;", "")
    assert "&" not in rest, text


def _armed(**kw) -> TransitionEvent:
    base = dict(
        thesis_id=uuid4(),
        thesis_name=THESIS,
        asof=date(2026, 11, 12),
        from_state="warming",
        to_state="armed",
        from_verdict="watching",
        to_verdict="core_entry",
        armed=(
            ArmedName(COMPANY, ("insider", "technical_breakout")),
            ArmedName(None, ("insider",), name=COMPANY),
        ),
    )
    base.update(kw)
    return TransitionEvent(**base)


def test_the_loud_armed_push_is_inert(posted):
    SlackNotifier().notify(_armed())
    assert len(posted) == 1
    _assert_inert(posted[0])
    assert "&lt;!channel&gt; &lt;https://evil.example|click&gt; — ARMED" in posted[0]
    assert "Smith &amp; Wesson FORGED LINE" in posted[0]


def test_the_loud_upgrade_push_is_inert(posted):
    SlackNotifier().notify(
        _armed(from_state="armed", from_grade="flip", to_grade="core", from_armed=1, to_armed=2)
    )
    assert len(posted) == 1
    _assert_inert(posted[0])


def test_the_quiet_armed_to_armed_push_is_inert(posted):
    SlackNotifier().notify(
        _armed(
            from_state="armed",
            from_grade="core",
            to_grade="flip",
            from_armed=3,
            to_armed=2,
            reasons=(f"{COMPANY} (structural break)", THESIS),
        )
    )
    assert len(posted) == 1
    _assert_inert(posted[0])
    assert "\n" in posted[0]  # the template's own line break survives


def test_the_digest_is_inert(posted):
    SlackNotifier().notify_digest(
        DigestEvent(
            asof=date(2026, 11, 12),
            theses=(
                DigestThesis(
                    thesis_id=uuid4(),
                    thesis_name=THESIS,
                    names=(ArmedName(COMPANY, ("insider",)),),
                    rearms=(ArmedName(None, (), name=COMPANY),),
                ),
            ),
        )
    )
    assert len(posted) == 1
    _assert_inert(posted[0])


def test_the_spac_page_is_inert(posted):
    match = SpacThesisMatch(
        thesis_id=uuid4(),
        thesis_name=THESIS,
        accession="0002222222-26-000002",
        signal_terms=(COMPANY,),
        broad_terms=(THESIS,),
        url="https://www.sec.gov/Archives/edgar/data/2222/000222222226000002/",
    )
    other = SpacThesisMatch(
        thesis_id=uuid4(),
        thesis_name=COMPANY,
        accession="0002222222-26-000003",
        signal_terms=(THESIS,),
        url="https://evil.example/<!here>\nFORGED LINE",
    )
    SlackNotifier().notify_spac_status(
        SpacStatusEvent(
            cik="0000002222",
            company_name=COMPANY,
            ticker=THESIS,
            accession="0002222222-26-000002",
            filed=date(2026, 11, 12),
            matches=(match, other),
            url=match.url,
        )
    )
    assert len(posted) == 1
    _assert_inert(posted[0])


def test_the_health_page_is_inert(posted):
    SlackNotifier().notify_health(
        HealthEvent(
            asof=date(2026, 11, 12),
            theses=1,
            withheld_no_live=0,
            withheld_failure=0,
            errored=1,
            edgar_fetches=1,
            frozen=False,
            crashed=f"ValueError: {COMPANY}",
            name_errors_new=(f"{COMPANY} (prices)",),
            audit_failed=(THESIS,),
            split_defects_new=(COMPANY,),
            successor_cik_new=(COMPANY,),
            tape_stale_new=(StaleFeedLabel(kind="price", label=COMPANY),),
            insider_amendments_new=(COMPANY,),
            insider_holds_new=(COMPANY,),
        )
    )
    assert len(posted) == 1
    _assert_inert(posted[0])


def test_the_edition_line_is_inert(posted):
    SlackNotifier().notify_edition(
        EditionCutEvent(
            number=3, previous=2, edition_label=f"{THESIS} {COMPANY}", company_nights_changed=4
        )
    )
    assert len(posted) == 1
    _assert_inert(posted[0])


def test_the_failure_page_is_inert(posted):
    assert page.send_page(date(2026, 11, 12), f"{THESIS} {COMPANY}") is True
    assert len(posted) == 1
    _assert_inert(posted[0])


def test_the_failure_page_fallback_post_is_inert(monkeypatch):
    sent: list[bytes] = []

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        sent.append(req.data)
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    def broken():
        raise RuntimeError("no seam")

    monkeypatch.setattr(page, "get_notifier", broken)
    assert page.send_page(date(2026, 11, 12), f"{THESIS} {COMPANY}") is False
    assert len(sent) == 1
    _assert_inert(json.loads(sent[0])["text"])


def test_post_text_escapes_whatever_it_is_given(posted):
    delivered, _ = post_text(_WEBHOOK, f"Alpha Deck test message for {THESIS} & co")
    assert delivered
    assert posted == [
        "Alpha Deck test message for &lt;!channel&gt; &lt;https://evil.example|click&gt; &amp; co"
    ]


def test_a_newline_in_a_value_never_starts_a_log_line(caplog):
    caplog.set_level(logging.WARNING)
    sink = LogNotifier()
    sink.notify(_armed(thesis_name=f"Real thesis\n{FORGED}"))
    sink.notify_health(
        HealthEvent(
            asof=date(2026, 11, 12),
            theses=1,
            withheld_no_live=0,
            withheld_failure=0,
            errored=0,
            edgar_fetches=1,
            frozen=False,
            crashed=f"ValueError: x\n{FORGED}",
        )
    )
    sink.notify_digest(
        DigestEvent(
            asof=date(2026, 11, 12),
            theses=(
                DigestThesis(
                    thesis_id=uuid4(),
                    thesis_name=f"T\n{FORGED}",
                    names=(ArmedName(None, (), name=f"N\r\n{FORGED}"),),
                ),
            ),
        )
    )
    sink.notify_spac_status(
        SpacStatusEvent(
            cik="1",
            company_name=f"C\n{FORGED}",
            ticker=None,
            accession="a",
            filed=date(2026, 11, 12),
        )
    )
    sink.notify_edition(
        EditionCutEvent(
            number=2, previous=1, edition_label=f"E\n{FORGED}", company_nights_changed=1
        )
    )
    assert len(caplog.records) == 5
    for rec in caplog.records:
        assert "\n" not in rec.getMessage() and "\r" not in rec.getMessage()
