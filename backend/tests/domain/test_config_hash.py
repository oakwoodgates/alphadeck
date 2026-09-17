"""The policy fingerprint (F1) — ``domain.config.config_hash`` / ``short_hash``.

PROPERTY tests, never a pinned digest. A golden that asserted the literal sha256 of ``DEFAULT_CONFIG``
would fail on EVERY dial change — it would be a tripwire on ordinary calibration work, not a test of
anything, and re-blessing it each time teaches the next reader that the file is noise. What actually has
to hold is: the same config always hashes the same (so a quiet night's rows agree), a different config
hashes differently (so a dial edit is visible on the record), and the canonicalization is the documented
one (so the backtest's manifest can reproduce it byte-for-byte).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from domain.config import (
    DEFAULT_CONFIG,
    DEFAULT_EXTRACTOR_CONFIG,
    CallConfig,
    _canonical_config,
    config_hash,
    short_hash,
)


def test_hash_is_deterministic_across_calls_and_equal_objects():
    """Same config -> same fingerprint. Twice from one object, and from a distinct-but-equal object:
    the hash must key on the VALUES, never on object identity or construction order — otherwise every
    cron night would stamp a new fingerprint and the column would say nothing."""
    assert config_hash(DEFAULT_CONFIG) == config_hash(DEFAULT_CONFIG)
    assert config_hash(CallConfig()) == config_hash(DEFAULT_CONFIG)
    assert config_hash(DEFAULT_CONFIG.model_copy()) == config_hash(DEFAULT_CONFIG)


def test_a_changed_dial_changes_the_hash_and_an_unchanged_copy_does_not():
    """The whole point: the record must be able to see a policy change. One dial moved -> a different
    fingerprint; a no-op copy -> the same one."""
    base = config_hash(DEFAULT_CONFIG)
    moved = DEFAULT_CONFIG.model_copy(update={"warming_min_entry_triggers": 2})
    assert config_hash(moved) != base
    assert config_hash(DEFAULT_CONFIG.model_copy(update={})) == base


def test_a_nested_policy_edit_also_moves_the_hash():
    """`CallConfig` carries nested models (the 8-K item policies). A dump that flattened or dropped them
    would hash two materially different policies identically — the failure mode that matters most, since
    the item map is exactly the kind of dial a recalibration pass edits."""
    items = {k: v.model_copy() for k, v in DEFAULT_CONFIG.corporate_event_items.items()}
    key = next(iter(items))
    items[key] = items[key].model_copy(update={"liveness_days": items[key].liveness_days + 1})
    assert config_hash(
        DEFAULT_CONFIG.model_copy(update={"corporate_event_items": items})
    ) != config_hash(DEFAULT_CONFIG)


def test_hash_shape_is_sha256_hex():
    h = config_hash(DEFAULT_CONFIG)
    assert len(h) == 64
    assert h == h.lower()
    assert all(c in "0123456789abcdef" for c in h)


def test_canonicalization_is_the_documented_one():
    """The canonicalization is part of the CONTRACT, not an implementation detail: the backtest's run
    manifest reproduces it to prove a lab run and a recorded night ran the same policy. Recomputing it here
    from the documented recipe pins that recipe — change the separators, the sort or the walk and this
    fails, rather than the backtest's reproduction breaking silently."""
    blob = json.dumps(_canonical_config(DEFAULT_CONFIG), sort_keys=True, separators=(",", ":"))
    assert config_hash(DEFAULT_CONFIG) == hashlib.sha256(blob.encode("utf-8")).hexdigest()


def test_the_hash_is_stable_ACROSS_PROCESSES():
    """THE test this module exists for. ``CallConfig`` carries three ``frozenset`` dials, and a frozenset
    iterates in an order that depends on PYTHONHASHSEED — so the obvious recipe
    (``json.dumps(cfg.model_dump(mode="json"), sort_keys=True)``) produces a DIFFERENT digest in every new
    interpreter. MEASURED 2026-09-16 before the fix: three consecutive runs, three different hashes.

    Shipped, that would have stamped a fresh policy fingerprint on every cron night — the record would have
    read as a nightly policy change, which is worse than no column at all. Every single-process assertion
    above passes just as happily with the bug present, so this runs the hash in two SUBPROCESSES under
    deliberately different hash seeds. Slow-ish (two interpreter starts) and worth it: nothing else in the
    suite can see this class of bug.
    """
    code = (
        "from domain.config import DEFAULT_CONFIG, config_hash; print(config_hash(DEFAULT_CONFIG))"
    )
    digests = set()
    for seed in ("0", "1", "12345"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        out = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(Path(__file__).resolve().parents[2]),  # backend/, so `domain` imports
            check=True,
        )
        digests.add(out.stdout.strip())
    assert len(digests) == 1, f"config_hash differs across processes: {digests}"
    assert digests == {config_hash(DEFAULT_CONFIG)}


def test_sets_are_sorted_and_ordered_ladders_are_not():
    """The two halves of the canonicalization, each guarding the opposite bug.

    A SET's order carries no meaning, so it is sorted — otherwise the digest inherits PYTHONHASHSEED. A
    LIST/TUPLE's order CAN be load-bearing: the pip ladders are ascending thresholds, so "sort every list"
    (the tempting blanket fix) would make an ascending and a descending ladder hash IDENTICALLY — two
    materially different policies, one fingerprint."""
    canon = _canonical_config(DEFAULT_CONFIG)
    assert isinstance(canon, dict)
    for field in ("conviction_kinds", "confirmation_kinds", "insider_senior_role_keywords"):
        assert canon[field] == sorted(canon[field]), f"{field} must be sorted (it is a set)"

    reversed_ladder = tuple(reversed(DEFAULT_CONFIG.purity_pip_pct))
    assert reversed_ladder != DEFAULT_CONFIG.purity_pip_pct  # the fixture is meaningful
    flipped = DEFAULT_CONFIG.model_copy(update={"purity_pip_pct": reversed_ladder})
    assert config_hash(flipped) != config_hash(DEFAULT_CONFIG)


def test_an_unhandled_type_raises_rather_than_falling_back_to_a_repr():
    """A fallback to ``str()`` would be the quiet way back to the original bug: a default object repr
    embeds a memory address, so it differs every process. A new dial of an unexpected type must fail HERE,
    in the suite, not on a prod night."""
    with pytest.raises(TypeError, match="no canonical form"):
        _canonical_config(object())


def test_scope_is_callconfig_only():
    """``ExtractorConfig`` is the Workbench extractor's config and never reaches the assembler, so it must
    not enter the call's policy fingerprint — an extractor dial edit moving the RECORD's hash would make
    the column lie about which policy produced a call.

    Asserted structurally, where the risk actually lives: nothing nested inside ``CallConfig`` is an
    ``ExtractorConfig``. The fingerprint is a dump of this model, so the day someone "tidies" the two
    configs together is the day the scope silently widens — this is the test that notices."""
    assert not isinstance(DEFAULT_EXTRACTOR_CONFIG, CallConfig)
    dumped = DEFAULT_CONFIG.model_dump(mode="json")
    for name in type(DEFAULT_EXTRACTOR_CONFIG).model_fields:
        assert (
            name not in dumped
        ), f"an ExtractorConfig field leaked into the call policy dump: {name}"


def test_short_hash_is_the_single_slicing_point():
    full = config_hash(DEFAULT_CONFIG)
    assert short_hash(full) == full[:8]
    assert len(short_hash(full)) == 8
    assert short_hash(None) is None  # unknown in, unknown out — never an empty string
