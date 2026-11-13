"""``notify.escape`` — the Slack escaper (security hardening PR 4a). Slack's documented escaping: ``&``, ``<`` and
``>`` become ``&amp;``, ``&lt;`` and ``&gt;``, which leaves ``<!channel>``, ``<@U123>`` and ``<url|label>`` inert
text; a newline or other control character in a value collapses to a space; a value is capped."""

from __future__ import annotations

from notify.escape import (
    MAX_MESSAGE_LENGTH,
    MAX_VALUE_LENGTH,
    one_line,
    slack_escape,
    slack_message,
)


def test_slack_escape_neutralizes_the_three_markup_characters() -> None:
    assert slack_escape("<!channel> <@U123> <https://evil.example|click> A&B") == (
        "&lt;!channel&gt; &lt;@U123&gt; &lt;https://evil.example|click&gt; A&amp;B"
    )


def test_slack_escape_escapes_the_ampersand_first_so_nothing_is_double_counted() -> None:
    assert slack_escape("&lt;") == "&amp;lt;"


def test_slack_escape_collapses_newlines_and_control_characters_to_a_space() -> None:
    assert slack_escape("Acme\nCorp\r\n\tInc\x00\x1b[31m") == "Acme Corp Inc [31m"
    assert slack_escape("a\u2028b\u2029c\x85d") == "a b c d"


def test_slack_escape_caps_the_length() -> None:
    out = slack_escape("x" * 5000)
    assert len(out) == MAX_VALUE_LENGTH
    assert out.endswith("…")
    assert slack_escape("x" * MAX_VALUE_LENGTH) == "x" * MAX_VALUE_LENGTH


def test_slack_escape_caps_before_escaping_so_an_entity_is_never_cut() -> None:
    out = slack_escape("<" * 5000)
    assert out.endswith("&lt;…")


def test_slack_escape_leaves_plain_text_and_emoji_alone() -> None:
    text = "🔴 AI Memory — ARMED (warming → armed) · MU insider buy, breakout"
    assert slack_escape(text) == text


def test_slack_escape_accepts_a_non_string() -> None:
    assert slack_escape(None) == ""
    assert slack_escape(42) == "42"


def test_one_line_collapses_without_escaping() -> None:
    assert one_line("<b>\nA&B") == "<b> A&B"


def test_one_line_without_a_limit_keeps_a_long_label_whole() -> None:
    assert one_line("y" * 5000, limit=None) == "y" * 5000


def test_slack_message_keeps_the_template_line_breaks_and_escapes_every_line() -> None:
    text = "🔴 <!here> — ARMED\nA&B armed\r\nC <@U1>"
    assert slack_message(text) == "🔴 &lt;!here&gt; — ARMED\nA&amp;B armed\nC &lt;@U1&gt;"


def test_slack_message_caps_the_whole_message() -> None:
    out = slack_message("z" * (MAX_MESSAGE_LENGTH * 2))
    assert len(out) == MAX_MESSAGE_LENGTH
    assert out.endswith("…")
