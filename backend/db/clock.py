"""``db_now`` — the DATABASE's current instant, and the ONE-CLOCK rule it exists to hold.

**The rule.** Every bitemporal read gates on ``recorded_at <= known_at``. ``recorded_at`` is written by the
column default ``now()`` — the *database's* clock. So a ``known_at`` taken from ``datetime.now()`` on the
app host compares two different clocks, and a fact recorded while the host clock lagged the database is
INVISIBLE to a read taken immediately afterwards. That is not theoretical: it was the mechanism behind two
separate flakes in this repo, and the second one (the Scoreboard price reader) was reproduced on demand by
injecting the skew.

**MEASURED on this stack.** The natural margin between a fact's ``recorded_at`` and a host-clock
``known_at`` taken right after its commit is ~1.5 ms. Host/container skew was +1.6 ms median at one
measurement and +2.4 to +3.6 ms hours earlier, with a -14.6 ms sample; it DRIFTS, and a host sleep/resume
perturbs it, which is exactly why the symptom appears and disappears. At an injected 2 ms the fact vanished
in 84 of 100 replays; at 4 ms, 100 of 100. Taking the bound from the database removes the host clock from
the comparison altogether: the same injection at 2 ms, 4 ms, 50 ms and 5 s leaves 0 of 100.

**``clock_timestamp()``, not ``now()``.** ``now()`` is TRANSACTION-START time, so inside a read transaction
it would sit before any fact committed during that transaction — under READ COMMITTED a later statement can
see such a row, and the bound must not exclude what the snapshot includes.

**It does not commit or roll back.** The caller owns the transaction (``pipeline.call_for_thesis``'s rule).
One consequence worth knowing: on a connection with no open transaction this SELECT starts one, so a fact
written on that SAME connection afterwards takes its ``recorded_at`` from ``now()`` = this transaction's
start — i.e. from BEFORE the instant returned here — and is therefore visible to a view that just pinned
it. That is an artifact of one connection doing both jobs (a test, essentially); on the serve path a
connection holding a point-in-time view does not write facts.

**Cost**: one round-trip per view construction (~0.1 ms locally), once — never per query. A caller that
needs the bound inside a single statement can inline ``COALESCE(%s::timestamptz, clock_timestamp())``
instead and pay nothing (``repositories.decisions_repo`` does); that is not available to a view which PINS
one bound and reuses it across many reads, because resolving per statement would let two reads of the same
view answer against different instants.

**Where the two clocks MEET is what decides test-side vs product-side.** PR #359 fixed this same
mechanism in the Form 4 tests and correctly touched no product code: there the clocks met only inside a
test's assertion, and the ingest never compared them. Everywhere this module is used they meet in the
PRODUCT — a host timestamp bound into a SQL predicate against a database-stamped column — so the fix
belongs where the comparison is. A test whose two clocks are its own still takes #359's remedy (pin both
bounds to the clock that stamps the column), not this one.

The prior art is ``decisions_repo``, which has bounded the operator log with the database clock since the
first version of this flake, and ``domain.market_time.serve_known_at``, whose docstring says in as many
words that passing a host-clock ``now`` there would reopen it. This module is that decision, made once and
shared, instead of re-made per reader.
"""

from __future__ import annotations

from datetime import datetime

import psycopg


def db_now(conn: psycopg.Connection) -> datetime:
    """The database's current instant — the clock that STAMPS ``recorded_at``. See the module docstring."""
    with conn.cursor() as cur:
        cur.execute("SELECT clock_timestamp() AS t")
        return cur.fetchone()["t"]
