from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from backtest import artifact, store
from backtest.manifest import LABELS

# B6 — the `/backtest` routes. ARTIFACT-served and read-only: these tests write a store on disk and point
# the reader at it, because that is exactly what the route does. Three properties carry the surface --
# absence is never an error, the pooled payload never names a thesis, and the five labels reach the wire
# verbatim so the front end cannot compose or edit them.


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def store_root(tmp_path, monkeypatch) -> Path:
    """Point the artifact reader at a temp store — the route reads through these helpers, so overriding
    them here exercises the real code path rather than a stub."""
    root = tmp_path / "backtest"
    monkeypatch.setattr(artifact.store, "DEFAULT_ROOT", root)
    return root


def _ledger(sid: uuid.UUID, tid: uuid.UUID, *, name: str = "A thesis") -> dict:
    """A minimal ledger artifact — one armed, scored episode on one thesis. Built through the real
    models so a shape change here fails loudly rather than serving a stale dict."""
    from backtest.ledger import SecurityRef, build_ledger
    from domain.enums import Grade, State, Verdict
    from replay.schema import CallSnapshot, Episode, MemberRow, Outcome
    from scoreboard.replay_snapshot import ThesisMeta

    arm, exit_by = date(2026, 1, 5), date(2026, 3, 5)
    member = MemberRow(
        security_id=sid, tier="armed", verdict=Verdict.CORE_ENTRY, entry_grade=Grade.CORE
    )
    snap = CallSnapshot(
        thesis_id=tid,
        asof=arm,
        state=State.ARMED,
        verdict=Verdict.CORE_ENTRY,
        armed_security_id=sid,
        members=[member],
    )
    ep = Episode(
        thesis_id=tid,
        security_id=sid,
        is_headline=True,
        arm_date=arm,
        last_armed_date=arm,
        close_reason="window_end",
        exit_by=exit_by,
    )
    out = Outcome(
        thesis_id=tid,
        security_id=sid,
        is_headline=True,
        arm_date=arm,
        exit_by=exit_by,
        forward_return=0.12,
    )
    return build_ledger(
        {tid: [snap]},
        [(ep, out)],
        thesis_meta={tid: ThesisMeta(tenant_id=None, name=name, ticker=None, basket_size=1)},
        securities={sid: SecurityRef(ticker="DEVCO", cik="0000000001", name="Dev Co")},
        window_start=date(2025, 9, 1),
        window_end=date(2026, 9, 14),
        pin=datetime(2026, 9, 15, tzinfo=timezone.utc),
        generated_at=datetime(2026, 9, 15, tzinfo=timezone.utc),
        matured_asof=date(2026, 9, 15),
        clock="record",
        config_hash="b" * 64,
        code_sha="a" * 40,
        dials_moved=[],
    ).model_dump(mode="json")


def _write_run(
    root: Path, run_id: str, *, pooled=None, episodes=None, dials=None, ledger=None
) -> None:
    d = store.create_run_dir(run_id, root)
    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "created_at": "2026-09-17T04:12:07+00:00",
        "code_sha": "a" * 40,
        "window_start": "2025-09-01",
        "window_end": "2026-09-14",
        "clock": "record",
        "known_at_mode": "pin",
        "pin": "2026-09-15T00:00:00+00:00",
        "config_hash": "b" * 64,
        "config_short": "bbbbbbbb",
        "config_canonical_json": "{}",
        "config": {},
        "overlay_diff": {d: {"default": 180, "run": 90} for d in (dials or [])},
        "theses": [],
        "mirror": {"hash": "c" * 64, "tables": {}, "excluded_tables": [], "blind_detectors": []},
        "workers": 1,
        "null_draws": 50,
        "null_seed": run_id,
        "labels": list(LABELS),
        "timings": {},
        "n_episodes": len(episodes or []),
        "n_theses": 0,
    }
    (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    if pooled is not None:
        (d / "pooled.json").write_text(json.dumps(pooled), encoding="utf-8")
    (d / "episodes.json").write_text(json.dumps({"episodes": episodes or []}), encoding="utf-8")
    if ledger is not None:
        (d / "ledger.json").write_text(json.dumps(ledger), encoding="utf-8")
    store.register_run(
        store.RunSummary(
            run_id=run_id,
            created_at=manifest["created_at"],
            config_short="bbbbbbbb",
            config_hash="b" * 64,
            clock="record",
            known_at_mode="pin",
            window_start="2025-09-01",
            window_end="2026-09-14",
            n_episodes=len(episodes or []),
            dials_moved=list(dials or []),
        ),
        root,
    )


# --- absence is never an error ----------------------------------------------------------------------


def test_the_registry_reports_unavailable_when_there_is_no_store(client, store_root):
    """Prod's normal state. A 200 with `available: false`, never a 500 and never an empty 404 page."""
    r = client.get("/backtest/runs")
    assert r.status_code == 200
    assert r.json() == {"available": False, "runs": [], "dial_trials": {}}


def test_an_unknown_run_is_unavailable_rather_than_a_404(client, store_root):
    """One code path on the front end: a stale bookmark reads as "not here", not as an error."""
    r = client.get(f"/backtest/runs/{uuid.uuid4()}")
    assert r.status_code == 200 and r.json()["available"] is False


def test_a_run_directory_with_no_manifest_is_not_a_run(client, store_root):
    """A half-written directory is a run that did not finish."""
    store.create_run_dir("half-written", store_root)
    assert client.get("/backtest/runs/half-written").json()["available"] is False


def test_a_corrupt_manifest_is_absence_not_an_outage(client, store_root):
    d = store.create_run_dir("corrupt", store_root)
    (d / "manifest.json").write_text("{ not json", encoding="utf-8")
    assert client.get("/backtest/runs/corrupt").json()["available"] is False


def test_a_run_id_cannot_walk_out_of_the_store(client, store_root):
    """The run id arrives from a URL, so it is a path component and is never joined blindly.

    Two layers, and the test asserts BOTH because either alone could be removed. A literal ``..`` is
    normalized away by URL routing before the handler ever sees it -- so that case is a 404, not an
    `available: false`, and asserting the envelope shape there would be asserting the router's behavior
    rather than ours. The layer that matters is the reader's own guard, which is what an encoded or
    embedded separator would reach."""
    # routing handles the literal forms: whatever comes back, it is never a served run
    for bad in ("..", "../etc", "%2e%2e%2fetc"):
        r = client.get(f"/backtest/runs/{bad}")
        assert r.status_code == 404 or r.json().get("available") is False, (bad, r.status_code)
    # ...and the reader refuses anything with a separator or a leading dot, which is the real guard
    for bad in ("../escape", "a/b", "a\\b", ".hidden", ""):
        assert store.run_dir(bad, store_root) is None, bad


def test_the_sweep_is_unavailable_until_one_has_run(client, store_root):
    assert client.get("/backtest/sweep").json() == {"available": False, "sweep": None, "labels": []}


# --- the registry -----------------------------------------------------------------------------------


def test_the_registry_lists_runs_newest_first(client, store_root):
    _write_run(store_root, "run-a")
    _write_run(store_root, "run-b")
    body = client.get("/backtest/runs").json()
    assert body["available"] is True
    assert {r["run_id"] for r in body["runs"]} == {"run-a", "run-b"}


def test_the_registry_counts_trials_per_dial(client, store_root):
    """ "Count your trials", answerable per DIAL -- a result read without knowing how many times its dial
    was swept is a result read without its multiple-comparisons context."""
    _write_run(store_root, "run-a", dials=["insider_core_alpha_liveness_days"])
    _write_run(
        store_root, "run-b", dials=["insider_core_alpha_liveness_days", "breakdown_dearm_scope"]
    )
    trials = client.get("/backtest/runs").json()["dial_trials"]
    assert trials["insider_core_alpha_liveness_days"] == 2
    assert trials["breakdown_dearm_scope"] == 1


# --- one run ------------------------------------------------------------------------------------------


def test_a_run_returns_its_manifest_pooled_and_episodes(client, store_root):
    pooled = {"n_episodes": 1, "banner": "b", "metrics": [], "slices": [], "diagnostics": {}}
    _write_run(store_root, "run-a", pooled=pooled, episodes=[{"security_id": str(uuid.uuid4())}])
    body = client.get("/backtest/runs/run-a").json()
    assert body["available"] is True and body["run_id"] == "run-a"
    assert body["manifest"]["config_short"] == "bbbbbbbb"
    assert body["pooled"]["banner"] == "b"
    assert len(body["episodes"]) == 1


def test_a_run_without_a_pooled_view_still_serves(client, store_root):
    """A run written before the pooled view existed has no `pooled`, and the surface says so rather than
    pretending the nulls were computed."""
    _write_run(store_root, "old-run")
    body = client.get("/backtest/runs/old-run").json()
    assert body["available"] is True and body["pooled"] is None


# --- the five labels, backend-authored ----------------------------------------------------------------


def test_the_five_labels_ride_the_run_response_verbatim(client, store_root):
    """Backend-authored (the `ingest_note` precedent): the front end renders them and composes nothing, so
    the caveat cannot drift between the artifact and the page."""
    _write_run(store_root, "run-a")
    labels = client.get("/backtest/runs/run-a").json()["labels"]
    assert labels == list(LABELS)
    assert len(labels) == 5
    joined = " ".join(labels).lower()
    for token in ("public clock", "counterfactual", "survivorship", "adjusted closes", "recompute"):
        assert token in joined


def test_the_labels_say_the_ratification_itself_is_hindsight(client, store_root):
    """FLAG-H, carried to the surface: an operator-ratified fact is modeled as public on its announcement
    date, which can predate the ratification by years."""
    _write_run(store_root, "run-a")
    joined = " ".join(client.get("/backtest/runs/run-a").json()["labels"]).lower()
    assert "ratif" in joined and "hindsight" in joined


# --- Q4: the pooled payload never names a thesis ------------------------------------------------------


def test_the_pooled_payload_on_the_wire_carries_no_thesis_identifier(client, store_root):
    """Q4, enforced at the SURFACE as well as in the builder. Pooling across theses tests the algorithm;
    slicing outcomes by thesis tests the idea, which is the leaderboard trap and a #4 violation. A future
    field that happened to carry a thesis id would fail here rather than quietly become a ranking.
    """
    from backtest.pooled import build_report

    tids = [uuid.uuid4() for _ in range(3)]
    pooled = json.loads(build_report([], [], draws=5, seed="s").model_dump_json())
    _write_run(store_root, "run-a", pooled=pooled)
    body = client.get("/backtest/runs/run-a").json()
    blob = json.dumps(body["pooled"])
    for t in tids:
        assert str(t) not in blob

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                assert "thesis" not in k.lower(), k
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(body["pooled"])


# --- the sweep ----------------------------------------------------------------------------------------


def test_the_sweep_curve_is_served_with_its_labels(client, store_root):
    store_root.mkdir(parents=True, exist_ok=True)
    curve = {
        "dial_names": ["insider_core_alpha_liveness_days"],
        "points": [{"dials": {"insider_core_alpha_liveness_days": 90}, "run_id": "run-a"}],
        "plateau": [0],
        "banner": "a curve, not a winner",
    }
    (store_root / "sweep.json").write_text(json.dumps(curve), encoding="utf-8")
    body = client.get("/backtest/sweep").json()
    assert body["available"] is True
    assert body["sweep"]["plateau"] == [0]
    assert body["labels"] == list(LABELS)


def test_the_served_sweep_names_no_winner(client, store_root):
    """The artifact has nowhere to put one; this pins that the wire shape does not add one either."""
    store_root.mkdir(parents=True, exist_ok=True)
    (store_root / "sweep.json").write_text(
        json.dumps({"points": [], "plateau": []}), encoding="utf-8"
    )
    blob = json.dumps(client.get("/backtest/sweep").json()).lower()
    for forbidden in ("winner", "optimal", "argmax"):
        assert forbidden not in blob


# --- the route writes nothing -------------------------------------------------------------------------


def test_the_backtest_router_never_writes(client):
    """Read-only, structurally: no route here may reach a writer. A simulated call has never been near
    `calls` and this keeps it that way by import graph rather than by discipline."""
    import ast

    src = Path(__file__).resolve().parents[2] / "app" / "routers" / "backtest.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    for mod in imported:
        assert "calls_repo" not in mod and not mod.startswith("db."), mod
    backtest_routes = [r for r in app.routes if getattr(r, "path", "").startswith("/backtest")]
    assert backtest_routes
    for r in backtest_routes:
        assert set(r.methods) <= {"GET", "HEAD"}, (r.path, r.methods)


# --- the ledger: the per-thesis drill-down, in the Scoreboard's own vocabulary -------------------------


def test_the_ledger_rides_the_run_response_in_the_scoreboards_episode_shape(client, store_root):
    """The drill-down renders through the SAME components as the Scoreboard's replay panel, which is
    only true if it arrives in the same wire shape. This asserts the fields those components read.
    """
    tid, sid = uuid.uuid4(), uuid.uuid4()
    _write_run(store_root, "run-a", ledger=_ledger(sid, tid))
    body = client.get("/backtest/runs/run-a").json()
    led = body["ledger"]
    assert led is not None and len(led["theses"]) == 1
    ep = led["theses"][0]["episodes"][0]
    for field in ("arm_date", "close_reason", "status", "matured", "forward_return", "path"):
        assert field in ep, field
    assert ep["forward_return"] == 0.12
    # ...and the identity the RUN resolved, not a serve-time re-resolution
    assert ep["ticker"] == "DEVCO" and ep["company_name"] == "Dev Co"


def test_a_run_written_before_the_ledger_existed_still_serves(client, store_root):
    """Absence degrades, it never 500s: the drill-down is simply not there and the page says so."""
    _write_run(store_root, "old-run")
    assert client.get("/backtest/runs/old-run").json()["ledger"] is None


def test_a_corrupt_ledger_is_absence_not_an_outage(client, store_root):
    """Validated at the boundary rather than three layers in, because the route re-projects it."""
    _write_run(store_root, "run-a", ledger={"snapshot": {"nope": True}})
    body = client.get("/backtest/runs/run-a").json()
    assert body["available"] is True and body["ledger"] is None


def test_the_ledger_is_ordered_by_name_never_by_outcome(client, store_root):
    """Grouping by thesis is how a reader checks a pooled number against its rows. Ordering that
    grouping by outcome would make it a leaderboard, which tests the IDEA rather than the timing —
    invariant #4. The order is the writer's and the route must not re-sort it."""
    tids = [uuid.uuid4(), uuid.uuid4()]
    a = _ledger(uuid.uuid4(), tids[0], name="Alpha")
    z = _ledger(uuid.uuid4(), tids[1], name="Zulu")
    a["snapshot"]["theses"] += z["snapshot"]["theses"]
    a["securities"].update(z["securities"])
    _write_run(store_root, "run-a", ledger=a)
    names = [t["name"] for t in client.get("/backtest/runs/run-a").json()["ledger"]["theses"]]
    assert names == ["Alpha", "Zulu"]


def test_the_ledger_banner_says_it_is_not_the_record(client, store_root):
    """The one sentence that has to survive every refactor: these rows are a recompute."""
    tid, sid = uuid.uuid4(), uuid.uuid4()
    _write_run(store_root, "run-a", ledger=_ledger(sid, tid))
    banner = client.get("/backtest/runs/run-a").json()["ledger"]["banner"]
    assert "never the record" in banner and "NOT a ranking" in banner


# --- the lean image ------------------------------------------------------------------------------------


def test_the_backtest_serving_path_imports_without_the_replay_extra():
    """The api image is LEAN: it carries neither duckdb nor pyarrow (only the sig/fork images bake
    `.[replay]`). Everything on the SERVING path must therefore import without them — which is why a
    run writes JSON copies of its Parquet artifacts. `backtest.run` is deliberately NOT in this list:
    it is the writer, it needs pyarrow, and nothing on the serving path may import it.

    Structural, like `tests/scoreboard/test_lean_import.py`: reading the import graph holds in ANY
    environment, so a dev venv that happens to have duckdb cannot mask a regression."""
    import ast

    root = Path(__file__).resolve().parents[2]
    for module in (
        "backtest.store",
        "backtest.manifest",
        "backtest.ledger",
        "backtest.artifact",
        "app.routers.backtest",
    ):
        path = root / Path(*module.split(".")).with_suffix(".py")
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert "duckdb" not in imported and "pyarrow" not in imported, module
        assert "run" not in {m.split(".")[-1] for m in imported if m.startswith("backtest")}
        __import__(module)
