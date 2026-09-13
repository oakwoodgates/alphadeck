"""The FEED KINDS a recency monitor can report on — one vocabulary, one prose table.

``pipeline/tape_health.py`` owns the RULE (is this edge too old? — ``is_tape_stale``, one implementation
for every kind). This module owns the two things that legitimately DIFFER per feed:

- **the noun** ("price tape" / "fund-shares tape") — what to call it on a page, a panel and a run row; and
- **the repair advice** — what the operator should actually go and do, which is NOT the same for the two:
  a dead price tape is usually a vendor symbol the master must be pointed at, while a dead fund-shares
  sample is a fund/ticker/source question. Merging them would produce a line that is wrong for half the
  rows, and a page whose advice is wrong is worse than a page with none.

**Why this lives in ``domain/`` and not beside the rule.** Three surfaces need this wording: the notifier
(``notify.HealthEvent.label``), the Admin run row (``app/routers/admin.py::_problems``) and the cron's
stdout summary (``pipeline/daily._report``). Those two first sites already duplicated the tape prose by
documented convention ("kept in step with it"), and adding a second kind would have doubled that
duplication into four places to keep in step. ``stale_feed_bits`` below is the ONE builder all of them
call, so a kind's wording exists once. ``domain/`` is where this repo puts shared vocabulary
(``enums.py``, ``market_time.py``) and it is importable from every layer without inverting anything —
``notify`` already imports ``domain.enums``.

**Value-free.** No I/O, no clock, no DB, no settings read: this module knows names and sentences, never
thresholds (those are ``Settings``) and never judgments (those are ``pipeline/tape_health.py``).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class FeedKind:
    """One monitored per-name feed: its wire ``key``, its display ``noun``, the word for ONE datum
    (``edge_noun`` — what the edge date is the date OF), and the ``advice`` a page gives when it stops.
    ``key`` is what rides the artifact and the wire (a stable string, never reformatted for display);
    ``noun`` is singular and gets a "(s)" from the formatter."""

    key: str
    noun: str
    edge_noun: str
    advice: str


PRICE = FeedKind(
    key="price",
    noun="price tape",
    edge_noun="bar",
    # The G5a repair, unchanged: the SEC ticker stays canonical while the vendor prices the name under a
    # new symbol after a rename (or it delisted), so the fix is to point the price leg at the vendor's
    # current symbol on the master. Teaching the resolver to follow renames automatically is deliberately
    # NOT built — a wrong auto-resolve files another company's tape under the member (#4/#6).
    advice="no new bars; check for a ticker rename or a delisting and set the vendor price symbol",
)

FUND_SHARES = FeedKind(
    key="fund_shares",
    noun="fund-shares tape",
    edge_noun="sample",
    # A DIFFERENT repair from the price tape's, which is the whole reason kinds carry their own advice.
    # The sampler is a fallback chain (Polygon when keyed -> the issuer page -> the aggregator), and its
    # legs miss independently: the fund may have closed or renamed, an issuer page redesign may have
    # broken the parse (the composite warns visibly when a PRIMARY leg errors, so the run's own output
    # names it), or the key may be absent. `security_master.price_symbol` is NOT the lever here — that is
    # the price leg's.
    advice=(
        "no new samples; check the fund still trades under that ticker, the run's fund-shares warnings "
        "for a broken source page, and POLYGON_API_KEY"
    ),
)

# Every kind the monitor knows, by wire key. A kind is added HERE and the pages, panel and summary follow.
FEED_KINDS: dict[str, FeedKind] = {k.key: k for k in (PRICE, FUND_SHARES)}


def feed_kind(key: str | None) -> FeedKind:
    """The ``FeedKind`` for a wire key, FAIL-SOFT to ``PRICE``.

    Two callers depend on the softness, both reading data written by an OLDER build: the Admin history
    re-reads run artifacts whose stale rows predate the ``kind`` field entirely, and the newly-stale diff
    reads the previous pass's artifact. Those rows are price rows — that is all the monitor recorded then
    — so defaulting to ``PRICE`` is the CORRECT reading, not merely a safe one. (Getting this wrong has a
    visible cost: every already-known stale price tape would page again as if new on the first pass after
    the deploy.) An unrecognized key from a FUTURE build also lands here rather than raising: a monitor
    must not blank a panel over a word it does not know."""
    if key is None:
        return PRICE
    return FEED_KINDS.get(key, PRICE)


@dataclass(frozen=True)
class StaleFeedLabel:
    """One newly-stale name as the PAGE needs it: which feed stopped (``kind``, a wire key) and what to
    call the name (``label`` — its ticker, or its security id when it has none, per
    ``tape_health.stale_label``).

    Deliberately a pair and not a bare string: the page groups by kind so each feed gets its own count and
    its own repair advice, and the artifact records the same pair so the Admin history re-derives exactly
    the page the run emitted. A bare string reached the artifact before fund shares existed; the reader
    turns those into ``kind="price"`` pairs (``feed_kind`` above)."""

    kind: str
    label: str


def stale_feed_bits(labels: Sequence[StaleFeedLabel] | Iterable[StaleFeedLabel]) -> list[str]:
    """The newly-stale page lines — ONE per feed kind present, in ``FEED_KINDS`` order, each naming its
    count, its names and its own repair advice. ``[]`` when nothing is newly stale (loudness marks the
    exception: a silent night produces no line at all).

    THE ONE BUILDER. ``notify.HealthEvent.label`` (the Slack/log page) and
    ``app/routers/admin.py::_problems`` (the Admin run row) both call this, so the two cannot drift the
    way they were documented to require manual step-keeping before. Each caller appends its OWN tail —
    the notifier's "not a cron error", the router's benign marker — because they are making different
    points about the same fact: a stopped feed is a FEED gap to repair, never a cron fault, so it must
    never take over the one-word cron verdict.

    Order is stable and grouped, never interleaved, so a page reads "2 price tape(s) …" and "1
    fund-shares tape(s) …" rather than a mixed list the operator has to sort by hand."""
    by_kind: dict[str, list[str]] = {}
    for item in labels:
        by_kind.setdefault(feed_kind(item.kind).key, []).append(item.label)
    out: list[str] = []
    for key, kind in FEED_KINDS.items():
        names = by_kind.get(key)
        if not names:
            continue
        out.append(f"{len(names)} {kind.noun}(s) newly STALE — {', '.join(names)} ({kind.advice})")
    return out
