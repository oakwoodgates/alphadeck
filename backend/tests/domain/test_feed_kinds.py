"""The feed-kind vocabulary + the ONE page-line builder (pure; no DB, no clock, no settings).

Why this module exists at all is the thing under test: three surfaces report a stopped feed (the
notifier's page, the Admin run row, the cron's stdout summary) and two of them used to keep hand-written
copies of the same sentence in step by convention. With a second feed that would have become four copies.
``stale_feed_bits`` is the single builder; these tests pin what it must say and the fail-soft reading that
keeps an older artifact rendering.
"""

from __future__ import annotations

from domain.feed_kinds import FUND_SHARES, PRICE, StaleFeedLabel, feed_kind, stale_feed_bits


def _lbl(label: str, kind: str = PRICE.key) -> StaleFeedLabel:
    return StaleFeedLabel(kind=kind, label=label)


def test_nothing_stale_produces_NO_LINE_AT_ALL():
    """Loudness marks the exception: a quiet night must emit no line, not an empty one."""
    assert stale_feed_bits([]) == []


def test_ONE_line_per_feed_kind_each_with_its_own_count_names_and_REPAIR():
    """The whole point of kinds. A price tape is repaired by pointing the vendor symbol at the renamed
    listing; a fund-shares tape is a fund/ticker/source question. One merged line would hand half the
    names the wrong instruction, so each feed gets its own line — grouped, never interleaved."""
    bits = stale_feed_bits(
        [_lbl("AAA"), _lbl("ETF1", FUND_SHARES.key), _lbl("BBB"), _lbl("ETF2", FUND_SHARES.key)]
    )

    assert len(bits) == 2
    price, fund = bits
    assert price.startswith("2 price tape(s) newly STALE — AAA, BBB")
    assert fund.startswith("2 fund-shares tape(s) newly STALE — ETF1, ETF2")
    # each carries ITS OWN repair, and not the other's
    assert "price symbol" in price and "price symbol" not in fund
    assert "POLYGON_API_KEY" in fund and "POLYGON_API_KEY" not in price


def test_a_single_kind_produces_a_single_line():
    """The common case — an all-equity universe has no fund feed to say anything about."""
    bits = stale_feed_bits([_lbl("AAA"), _lbl("BBB")])
    assert len(bits) == 1 and bits[0].startswith("2 price tape(s)")


def test_an_UNKNOWN_or_MISSING_kind_reads_as_price_rather_than_raising():
    """Fail-soft, and CORRECT rather than merely safe: every stale row written before fund shares were
    monitored carries no kind, and those rows ARE price rows. (Read any other way, the first pass after
    the deploy would re-page the whole known-dead price inventory as if it were new.) An unrecognized key
    from a future build lands here too — a monitor must not blank a page over a word it does not know.
    """
    assert feed_kind(None) is PRICE
    assert feed_kind("price") is PRICE
    assert feed_kind("fund_shares") is FUND_SHARES
    assert feed_kind("something_new") is PRICE

    bits = stale_feed_bits([_lbl("AAA", "something_new"), _lbl("BBB", None)])  # type: ignore[arg-type]
    assert len(bits) == 1 and bits[0].startswith("2 price tape(s) newly STALE — AAA, BBB")


def test_every_kind_carries_the_words_the_surfaces_need():
    """The vocabulary is data the pages read, so an empty field would silently produce a broken sentence
    rather than an error. Also pins that the two kinds do not share a noun."""
    for kind in (PRICE, FUND_SHARES):
        assert kind.key and kind.noun and kind.edge_noun and kind.advice
    assert PRICE.noun != FUND_SHARES.noun
    assert PRICE.edge_noun == "bar" and FUND_SHARES.edge_noun == "sample"
