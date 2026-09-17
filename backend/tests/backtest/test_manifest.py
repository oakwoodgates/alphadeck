from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime, timezone

import pytest

from backtest import manifest as mf
from backtest.config_overlay import apply_overlay
from domain.config import DEFAULT_CONFIG, CallConfig, config_hash, short_hash

# Run identity. Every assertion here is a PROPERTY — never a literal digest golden. A pinned digest would
# have to be regenerated the first time any dial's default moved, which is exactly when a reviewer stops
# reading it; the invariants below stay true across every such change, and `tests/domain/test_config_hash.py`
# already owns the digest's own determinism.


def _manifest(cfg: CallConfig = DEFAULT_CONFIG, **over) -> mf.BacktestManifest:
    blob = mf.canonical_config_blob(cfg)
    base = dict(
        run_id="20260918T041207Z-test-abc12345",
        created_at="2026-09-18T04:12:07+00:00",
        window_start=date(2025, 9, 1),
        window_end=date(2026, 9, 14),
        pin="2026-09-15T00:00:00+00:00",
        config_hash=config_hash(cfg),
        config_short=short_hash(config_hash(cfg)),
        config_canonical_json=blob,
        config=json.loads(blob),
        mirror=mf.MirrorInfo(hash="0" * 64),
    )
    base.update(over)
    return mf.BacktestManifest(**base)


# --- the fingerprint -------------------------------------------------------------------------------


def test_the_blob_is_exactly_what_config_hash_hashed():
    """The whole reason the blob is stored: a reader can re-verify the digest with a sha256 and nothing
    else — no repo, no Pydantic, no canonical walk."""
    for cfg in (DEFAULT_CONFIG, apply_overlay({"insider_core_alpha_liveness_days": 90})):
        blob = mf.canonical_config_blob(cfg)
        assert hashlib.sha256(blob.encode("utf-8")).hexdigest() == config_hash(cfg)


def test_verify_config_hash_reads_the_manifest_alone():
    assert mf.verify_config_hash(_manifest()) is True


def test_verify_config_hash_catches_a_tampered_blob():
    m = _manifest()
    tampered = m.model_copy(update={"config_canonical_json": m.config_canonical_json + " "})
    assert mf.verify_config_hash(tampered) is False


def test_config_round_trips_from_the_manifest_to_a_CallConfig():
    """Stronger than re-hashing a string: the stored config must rebuild a real ``CallConfig`` whose OWN
    fingerprint matches. That is what makes a run reproducible rather than merely self-consistent.
    """
    for cfg in (
        DEFAULT_CONFIG,
        apply_overlay({"insider_flip_alpha_liveness_days": 30}),
        apply_overlay({"confirmation_kinds": ["technical_breakout"], "breakout_min_return": 0.2}),
    ):
        m = _manifest(cfg)
        rebuilt = CallConfig.model_validate(m.config)
        assert config_hash(rebuilt) == m.config_hash


def test_the_parsed_config_and_the_blob_cannot_drift():
    m = _manifest()
    assert json.loads(m.config_canonical_json) == m.config


def test_short_is_the_one_slicing_point():
    """Nothing slices a hash inline — ``domain.config.short_hash`` is the single place."""
    m = _manifest()
    assert m.config_short == short_hash(m.config_hash)


def test_the_manifest_never_re_derives_the_hash():
    """The manifest carries what ``config_hash`` produced, not its own walk."""
    cfg = apply_overlay({"insider_core_min_usd": 250_000})
    assert _manifest(cfg).config_hash == config_hash(cfg)


def test_two_configs_that_differ_get_different_fingerprints():
    a = _manifest(DEFAULT_CONFIG)
    b = _manifest(apply_overlay({"insider_core_alpha_liveness_days": 91}))
    assert a.config_hash != b.config_hash
    assert a.config_canonical_json != b.config_canonical_json


# --- the run id ------------------------------------------------------------------------------------


def test_run_id_is_sortable_then_legible_then_precise():
    cfg = apply_overlay({"insider_core_alpha_liveness_days": 90})
    rid = mf.make_run_id(
        cfg,
        hypothesis="H5: the exit_by horizon",
        now=datetime(2026, 9, 18, 4, 12, 7, tzinfo=timezone.utc),
    )
    stamp, *_ = rid.split("-", 1)
    assert stamp == "20260918T041207Z"
    assert rid.endswith(short_hash(config_hash(cfg)))
    assert "h5-the-exit-by" in rid
    # ...and the fact axis sits between the two (CW) — see RUN_ID_PARTS, which the collision error reads
    assert rid.startswith("20260918T041207Z-record-")
    assert list(mf.RUN_ID_PARTS) == [
        "utc timestamp (to the second)",
        "clock",
        "hypothesis slug",
        "config short hash",
    ]


def test_run_id_changes_with_the_WALL_CLOCK_so_a_re_run_is_a_NEW_trial():
    """Two runs of identical inputs are two TRIALS. If the id collapsed them the registry would
    under-count exactly the thing it exists to count.

    Named for the WALL clock deliberately: since CW "clock" also means the fact axis (record vs public),
    and that is a different component of the id — the one tested below."""
    now = datetime(2026, 9, 18, 4, 12, 7, tzinfo=timezone.utc)
    later = datetime(2026, 9, 18, 4, 12, 8, tzinfo=timezone.utc)
    assert mf.make_run_id(DEFAULT_CONFIG, hypothesis=None, now=now) != mf.make_run_id(
        DEFAULT_CONFIG, hypothesis=None, now=later
    )


def test_the_same_experiment_on_the_two_FACT_CLOCKS_gets_two_ids_in_the_same_second():
    """CW. A record pass and a public pass of one grid under one hypothesis are two measurements, run back
    to back — and on a short window a point finishes inside the timestamp's one-second resolution. Without
    the clock in the id they collide and the second run dies on `create_run_dir`. This is the cheap half of
    that fix; `tests/backtest/test_clock_wiring.py` drives the same thing through two real sweeps.
    """
    now = datetime(2026, 9, 18, 4, 12, 7, tzinfo=timezone.utc)
    rec = mf.make_run_id(DEFAULT_CONFIG, hypothesis="H5", now=now, clock="record")
    pub = mf.make_run_id(DEFAULT_CONFIG, hypothesis="H5", now=now, clock="public")
    assert rec != pub
    # ...and the axis is legible in a directory listing, which is where a reader comparing the two looks
    assert "-record-" in rec and "-public-" in pub


@pytest.mark.parametrize(
    "text,expected",
    [
        (None, "default"),
        ("", "default"),
        ("   ", "default"),
        ("!!!", "default"),
        ("H1 / revenue re-accel -> flip", "h1-revenue-re-accel-flip"),
        ("Already-Clean", "already-clean"),
    ],
)
def test_slug_is_filesystem_safe(text, expected):
    slug = mf.slugify(text)
    assert slug == expected
    assert all(c.isalnum() or c == "-" for c in slug)


# --- the hashes that make a comparison attributable ------------------------------------------------


def test_roster_hash_is_order_sensitive():
    """A REORDERED basket is a different roster to the ranking, so it must fingerprint differently."""
    a, b = uuid.uuid4(), uuid.uuid4()
    assert mf.roster_hash([a, b]) != mf.roster_hash([b, a])
    assert mf.roster_hash([a, b]) == mf.roster_hash([a, b])


def test_roster_hash_distinguishes_an_unresolved_member():
    """A member whose security stopped resolving is a DIFFERENT roster, not silently the same one."""
    a = uuid.uuid4()
    assert mf.roster_hash([a, None]) != mf.roster_hash([a])


def test_mirror_hash_is_content_addressed(tmp_path):
    """Two runs quoting one mirror hash swept provably the same facts — the property that makes a config
    delta attributable to the config rather than to the tape moving underneath it."""
    (tmp_path / "fact_a.parquet").write_bytes(b"alpha")
    (tmp_path / "fact_b.parquet").write_bytes(b"beta")
    first = mf.mirror_hash(tmp_path)
    assert mf.mirror_hash(tmp_path) == first  # stable
    (tmp_path / "fact_b.parquet").write_bytes(b"beta!")
    assert mf.mirror_hash(tmp_path) != first  # content-sensitive


def test_mirror_hash_ignores_non_parquet_siblings(tmp_path):
    """The manifest and the metrics live in the same directory; they are OUTPUTS, not the swept facts."""
    (tmp_path / "fact_a.parquet").write_bytes(b"alpha")
    first = mf.mirror_hash(tmp_path)
    (tmp_path / "metrics.json").write_text("{}", encoding="utf-8")
    assert mf.mirror_hash(tmp_path) == first


# --- the pre-registration fields -------------------------------------------------------------------


def test_the_five_labels_ride_every_manifest():
    """Q7: all five, on every surface. They are backend-authored so the caveat cannot drift between the
    artifact and the page."""
    labels = _manifest().labels
    assert len(labels) == 5
    joined = " ".join(labels).lower()
    for token in ("public clock", "counterfactual", "survivorship", "adjusted closes", "recompute"):
        assert token in joined


def test_the_labels_say_the_ratification_itself_is_hindsight():
    """FLAG-H, as a label rather than a redesign: an operator-ratified fact is modeled as public on its
    announcement date, which can predate the ratification by years."""
    joined = " ".join(_manifest().labels).lower()
    assert "ratif" in joined and "hindsight" in joined


def test_clock_and_known_at_mode_are_recorded():
    m = _manifest()
    assert m.clock == "record" and m.known_at_mode == "pin"
    lockstep = _manifest(clock="public", known_at_mode="lockstep")
    assert lockstep.clock == "public" and lockstep.known_at_mode == "lockstep"


def test_an_unknown_clock_is_rejected():
    with pytest.raises(Exception):
        _manifest(clock="wall")


def test_write_and_read_round_trip(tmp_path):
    m = _manifest(hypothesis="H5", decision_rule="plateau + sign agreement", regime="one regime")
    mf.write_manifest(tmp_path, m)
    back = mf.read_manifest(tmp_path)
    assert back is not None
    assert back.model_dump() == m.model_dump()
    assert mf.verify_config_hash(back) is True


def test_reading_an_absent_manifest_is_absence_not_an_error(tmp_path):
    assert mf.read_manifest(tmp_path) is None


def test_reading_a_corrupt_manifest_is_absence_not_an_error(tmp_path):
    """A half-written run directory is a run that did not finish; the registry and the route both need to
    skip it quietly rather than fail the whole listing."""
    (tmp_path / mf.MANIFEST_NAME).write_text("{ not json", encoding="utf-8")
    assert mf.read_manifest(tmp_path) is None


def test_code_sha_is_a_sha_or_none():
    """NULL when unknown, never fabricated — the ``calls`` row's rule."""
    sha = mf.resolve_code_sha()
    assert sha is None or (len(sha) == 40 and all(c in "0123456789abcdef" for c in sha))
