"""``scripts/deploy.sh`` (I4) — the prod deploy wrapper, run for real against PATH-shimmed fakes.

Every test runs the REAL script under bash, in a throwaway copy of the repo root (``tmp_path``: the
script, a ``.git`` directory, a ``.env``), with fake ``git`` / ``docker`` / ``curl`` / ``date`` first on
PATH. No Docker daemon, no prod endpoint and no real repository is ever touched: the launcher refuses
to start the script unless all four commands resolve to the shims, and ``DOCKER_HOST`` points at a dead
port in case anything slipped past it.

The fakes log every call (``SHIM_LOG``), so a test asserts what the script DID, not only what it printed.
``docker exec -i <backend> python -c <parse>`` runs the script's own /admin/status parser on this
interpreter, so the parse under test is the real one; the pg_locks read is canned (``FAKE_LOCK``).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO / "scripts" / "deploy.sh"

HEAD = "4f1c0de5" * 5  # the checkout's HEAD in every test (40 hex chars, like `git rev-parse HEAD`)
OTHER = "b0b0cafe" * 5
OLD = "0ld5ha00" * 5  # the sha a container NOT rebuilt by the test still carries
_QUIET_HOUR = "2026-10-16 14:05 EDT"


def _find_bash() -> str | None:
    if os.name != "nt":
        return shutil.which("bash")
    # Windows: Git Bash ONLY. System32\bash.exe (and the WindowsApps alias) is WSL, another filesystem and
    # PATH world in which these Windows paths do not exist.
    roots: list[Path] = []
    git = shutil.which("git")
    if git:
        roots += list(Path(git).resolve().parents)
    roots.append(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git")
    for root in roots:
        for rel in ("usr/bin/bash.exe", "bin/bash.exe"):
            if (root / rel).is_file():
                return str(root / rel)
    return None


_BASH = _find_bash()
pytestmark = pytest.mark.skipif(_BASH is None, reason="needs bash (Git Bash on Windows)")

# --- the fakes ----------------------------------------------------------------------------------------

_FAKE_GIT = r"""#!/usr/bin/env bash
echo "git $*" >> "$SHIM_LOG"
case "$1" in
  status)       printf '%s' "${FAKE_GIT_STATUS-}" ;;
  symbolic-ref) [ -n "${FAKE_GIT_BRANCH-main}" ] || exit 1; echo "${FAKE_GIT_BRANCH-main}" ;;
  rev-parse)
    case "$2" in
      HEAD)        echo "$FAKE_HEAD" ;;
      origin/main) echo "${FAKE_ORIGIN_MAIN:-$FAKE_HEAD}" ;;
      *)           echo "fake git: unexpected: $*" >&2; exit 64 ;;
    esac ;;
  fetch)        [ -z "${FAKE_GIT_FETCH_FAIL-}" ] || { echo "fatal: unable to access origin" >&2; exit 128; } ;;
  ls-remote)    printf '%s\trefs/heads/main\n' "${FAKE_ORIGIN_MAIN:-$FAKE_HEAD}" ;;
  *)            echo "fake git: unexpected: $*" >&2; exit 64 ;;
esac
"""

# Answers per zone, ignoring the format: the script asks for "%Y-%m-%d %H:%M %Z".
_FAKE_DATE = r"""#!/usr/bin/env bash
echo "date TZ=${TZ-} $*" >> "$SHIM_LOG"
case "${TZ-}" in
  America/New_York)         echo "${FAKE_DATE_IANA-$FAKE_NOW}" ;;
  'EST5EDT,M3.2.0,M11.1.0') echo "${FAKE_DATE_POSIX-$FAKE_NOW}" ;;
  *) echo "fake date: unexpected TZ='${TZ-}'" >&2; exit 64 ;;
esac
"""

# /admin/status: the Nth read serves $SHIM_STATE/status_N.json (else status.json); no file = unreachable.
_FAKE_CURL = r"""#!/usr/bin/env bash
echo "curl $*" >> "$SHIM_LOG"
url="${!#}"
case "$url" in
  */admin/status)
    n=$(( $(cat "$SHIM_STATE/reads" 2>/dev/null || echo 0) + 1 ))
    echo "$n" > "$SHIM_STATE/reads"
    for f in "$SHIM_STATE/status_$n.json" "$SHIM_STATE/status.json"; do
      if [ -f "$f" ]; then cat "$f"; exit 0; fi
    done
    echo "curl: (7) Failed to connect to localhost port 8000" >&2; exit 7 ;;
  */api/health)
    printf '{"status":"ok"}\n%s' "${FAKE_PROXY_CODE:-200}" ;;
  *) echo "fake curl: unexpected: $*" >&2; exit 64 ;;
esac
"""

# A `--build` records the GIT_SHA each rebuilt backend/cron image would carry; `printenv` reads it back
# (FAKE_BUILT_SHA simulates an image that did not pick it up). Containers not rebuilt carry FAKE_OLD_SHA.
_FAKE_DOCKER = r"""#!/usr/bin/env bash
echo "docker[GIT_SHA=${GIT_SHA-<unset>}] $*" >> "$SHIM_LOG"
case "$1" in
  compose)
    case " $* " in
      *" exec "*)
        [ -z "${FAKE_BACKUP_FAIL-}" ] || { echo "pg_dump: error: connection refused" >&2; exit 1; }
        echo "backup: wrote alphadeck-20261016T180500-label.sql (123 bytes)" ;;
      *" up "*)
        [ -z "${FAKE_BUILD_FAIL-}" ] || { echo "failed to solve: process did not complete" >&2; exit 17; }
        case " $* " in
          *" --build "*)
            for s in backend cron; do
              case " $* " in *" $s "*) printf '%s' "${FAKE_BUILT_SHA-$GIT_SHA}" > "$SHIM_STATE/sha_$s" ;; esac
            done ;;
        esac ;;
      *) echo "fake docker: unexpected compose: $*" >&2; exit 64 ;;
    esac ;;
  inspect)
    case "$3" in
      *Health*)       echo "${FAKE_HEALTH:-healthy}" ;;
      *State.Status*) echo "${FAKE_CRON_STATE:-running}" ;;
      *) echo "fake docker: unexpected inspect: $*" >&2; exit 64 ;;
    esac ;;
  exec)
    shift
    if [ "$1" = "-i" ]; then shift; fi
    name="$1"; shift
    case "$1" in
      printenv)
        svc="${name#alphadeck-}"; svc="${svc%-1}"
        if [ -f "$SHIM_STATE/sha_$svc" ]; then cat "$SHIM_STATE/sha_$svc"; echo; else echo "${FAKE_OLD_SHA}"; fi ;;
      python)
        case "$3" in
          *run_lock*) echo "${FAKE_LOCK:-free}" ;;
          *) exec "$SHIM_PYTHON" -c "$3" ;;
        esac ;;
      *) echo "fake docker: unexpected exec: $*" >&2; exit 64 ;;
    esac ;;
  logs)
    echo "daily-cron: scheduled for 22:30 (America/New_York), Mon-Fri"
    echo "${FAKE_CRON_BOOT_LINE-daily-cron: each pass bounded at 5400s - heartbeat on - backup heartbeat on}" ;;
  restart) echo "$2" ;;
  *) echo "fake docker: unexpected: $*" >&2; exit 64 ;;
esac
"""

# Puts the shims first on PATH and REFUSES to start the script unless git/docker/curl/date resolve to
# them: the guard that keeps a PATH mistake from ever reaching a real Docker daemon.
_LAUNCH = r"""
shim="$1"; shift
if [ -x /usr/bin/cygpath ]; then shim="$(/usr/bin/cygpath -u "$shim")"; fi
export PATH="$shim:/usr/bin:/bin:$PATH"
for c in git docker curl date; do
  if [ "$(command -v "$c")" != "$shim/$c" ]; then
    echo "LAUNCHER: $c resolves to '$(command -v "$c")', not the shim; refusing to run the script" >&2
    exit 99
  fi
done
exec "$BASH" "$@"
"""

_SHIMS = {"git": _FAKE_GIT, "date": _FAKE_DATE, "curl": _FAKE_CURL, "docker": _FAKE_DOCKER}


@dataclass
class Result:
    code: int
    out: str  # stdout + stderr, merged
    log: list[str]

    def calls(self, prefix: str) -> list[str]:
        return [ln for ln in self.log if ln.startswith(prefix)]

    def index(self, needle: str) -> int:
        """The position of the first logged call containing ``needle`` (fails the test if none)."""
        for i, ln in enumerate(self.log):
            if needle in ln:
                return i
        raise AssertionError(f"no logged call contains {needle!r}:\n" + "\n".join(self.log))


def _status(status: str = "healthy", alarms: tuple[str, ...] = (), unfinished=None) -> dict:
    return {
        "record": {"edge": "2026-10-15"},
        "cron": {"status": status, "detail": "a detail", "alarms": list(alarms)},
        "unfinished_run": unfinished,
    }


@pytest.fixture
def deploy(tmp_path):
    """A staged repo root + shims; returns ``run(*args, statuses=..., **env) -> Result``.

    ``statuses``: one /admin/status body for every read (a dict), or a list (the Nth read gets the Nth),
    or ``None`` for an unreachable endpoint. Default: healthy, no alarms."""
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    shutil.copyfile(_SCRIPT, root / "scripts" / "deploy.sh")
    (root / ".git").mkdir()  # the MAIN checkout: .git is a directory (a worktree's is a file)
    (root / ".env").write_text("SLACK_WEBHOOK_URL=\n", encoding="utf-8")
    shims = tmp_path / "shims"
    shims.mkdir()
    for name, body in _SHIMS.items():
        p = shims / name
        p.write_bytes(body.encode("utf-8"))  # LF bytes: a text-mode write would add CRs on Windows
        p.chmod(0o755)
    state = tmp_path / "state"
    state.mkdir()
    log = tmp_path / "shim.log"

    def run(*args: str, statuses: object = "default", **fake: str) -> Result:
        if statuses == "default":
            statuses = _status()
        if isinstance(statuses, dict):
            (state / "status.json").write_text(json.dumps(statuses), encoding="utf-8")
        elif isinstance(statuses, list):
            for i, body in enumerate(statuses, start=1):
                (state / f"status_{i}.json").write_text(json.dumps(body), encoding="utf-8")
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("FAKE_", "DEPLOY_", "SHIM_", "DOCKER_")) and k != "GIT_SHA"
        }
        env.update(
            SHIM_LOG=log.as_posix(),
            SHIM_STATE=state.as_posix(),
            SHIM_PYTHON=Path(sys.executable).as_posix(),
            FAKE_HEAD=HEAD,
            FAKE_NOW=_QUIET_HOUR,
            FAKE_OLD_SHA=OLD,
            DEPLOY_WAIT_S="0",  # one try per readiness poll: a failing fake fails at once
            DEPLOY_POLL_S="0",
            DOCKER_HOST="tcp://127.0.0.1:9",  # belt and suspenders: no daemon answers here
            PYTHONIOENCODING="utf-8",
        )
        env.update(fake)
        proc = subprocess.run(
            [_BASH, "-c", _LAUNCH, "launch", str(shims), "scripts/deploy.sh", *args],
            cwd=root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
            timeout=90,
        )
        assert proc.returncode != 99, proc.stdout  # the launcher's shim guard tripped
        lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
        return Result(proc.returncode, proc.stdout, lines)

    run.root = root  # type: ignore[attr-defined]
    return run


def _no_stack_change(r: Result) -> None:
    """Nothing that changes the stack ran: no backup, no build/recreate, no restart."""
    assert not [c for c in r.calls("docker") if " compose " in c or " restart " in c], r.log


# --- the file itself ------------------------------------------------------------------------------------


def test_the_script_is_LF_with_an_env_bash_shebang():
    """A CRLF checkout breaks bash on the `\\r` (the cron-sidecar lesson); .gitattributes pins *.sh to LF."""
    raw = _SCRIPT.read_bytes()
    assert raw.startswith(b"#!/usr/bin/env bash\n")
    assert b"\r" not in raw


# --- refusals: usage (exit 2, before anything runs) ------------------------------------------------------


@pytest.mark.parametrize(
    "args, says",
    [
        (["--label", "pre-deploy", "worker"], "unknown service 'worker'"),
        (["backend"], "a build needs --label"),
        (["--label", "---", "backend"], "UNLABELED, prunable dump"),
        (["--env-only", "backend"], "--env-only recreates the cron sidecar only"),
        (["--env-only", "cron", "frontend"], "--env-only recreates the cron sidecar only"),
        (["--label", "x"], "name at least one service"),
        (["--label", "x", "--force", "backend"], "unknown option: --force"),
    ],
)
def test_a_bad_invocation_refuses_before_running_anything(deploy, args, says):
    r = deploy(*args)
    assert r.code == 2, r.out
    assert "REFUSE: " in r.out and says in r.out
    assert r.log == []  # not even git ran


def test_help_prints_the_usage(deploy):
    r = deploy("--help")
    assert r.code == 0
    assert "scripts/deploy.sh --label <label> <service>..." in r.out
    assert r.log == []


# --- refusals: state (exit 3, nothing changed) ----------------------------------------------------------


def test_refuses_a_dirty_tree(deploy):
    r = deploy(
        "--label", "pre-deploy", "backend", FAKE_GIT_STATUS=" M backend/app/main.py\n?? x.txt"
    )
    assert r.code == 3, r.out
    assert "REFUSE: the tree is dirty" in r.out
    assert "M backend/app/main.py" in r.out
    _no_stack_change(r)


@pytest.mark.parametrize(
    "branch, says", [("claude/i4-deploy-wrapper", "claude/i4-deploy-wrapper"), ("", "detached")]
)
def test_refuses_anything_but_main(deploy, branch, says):
    r = deploy("--label", "pre-deploy", "frontend", FAKE_GIT_BRANCH=branch)
    assert r.code == 3, r.out
    assert "REFUSE: on " in r.out and says in r.out and "not main" in r.out
    _no_stack_change(r)


def test_refuses_a_HEAD_that_is_not_origin_main_after_a_fetch(deploy):
    r = deploy("--label", "pre-deploy", "frontend", FAKE_ORIGIN_MAIN=OTHER)
    assert r.code == 3, r.out
    assert (
        f"REFUSE: HEAD {HEAD[:12]} != origin/main {OTHER[:12]} (after git fetch origin main)"
        in r.out
    )
    assert r.index("git fetch --quiet origin main") < r.index("git rev-parse origin/main")
    _no_stack_change(r)


def test_refuses_when_the_fetch_fails(deploy):
    r = deploy("--label", "pre-deploy", "frontend", FAKE_GIT_FETCH_FAIL="1")
    assert r.code == 3, r.out
    assert "REFUSE: could not read HEAD and origin/main" in r.out
    _no_stack_change(r)


def test_refuses_from_a_worktree(deploy):
    root = deploy.root
    (root / ".git").rmdir()
    (root / ".git").write_text("gitdir: /elsewhere/.git/worktrees/x\n", encoding="utf-8")
    r = deploy("--label", "pre-deploy", "frontend")
    assert r.code == 3, r.out
    assert "REFUSE: this is a linked worktree" in r.out
    _no_stack_change(r)


def test_refuses_without_the_dot_env(deploy):
    (deploy.root / ".env").unlink()
    r = deploy("--label", "pre-deploy", "frontend")
    assert r.code == 3, r.out
    assert "REFUSE: no .env at" in r.out
    _no_stack_change(r)


@pytest.mark.parametrize(
    "now, refused",
    [
        ("2026-10-16 22:19 EDT", False),
        ("2026-10-16 22:20 EDT", True),
        ("2026-10-16 22:50 EDT", True),
        ("2026-10-16 22:51 EDT", False),
        ("2026-12-01 22:35 EST", True),  # the same wall-clock window in winter time
    ],
)
def test_the_nightly_window_boundary(deploy, now, refused):
    r = deploy("--label", "pre-deploy", "frontend", FAKE_NOW=now)
    if refused:
        assert r.code == 3, r.out
        assert f"REFUSE: it is {now} — inside 22:20-22:50 America/New_York" in r.out
        assert r.calls("docker") == [] and r.calls("curl") == []  # the stack is not even read
    else:
        assert r.code == 0, r.out
        assert f"ok: outside 22:20-22:50 America/New_York (now {now})" in r.out


def test_the_window_is_New_York_time_even_where_the_IANA_zone_is_missing(deploy):
    """Git for Windows ships no zoneinfo: `TZ=America/New_York date` silently prints UTC as "GMT" (MEASURED on
    the deploy host). That answer is rejected and the EST5EDT rule read instead — here it says 22:25, inside.
    """
    r = deploy(
        "--label",
        "pre-deploy",
        "frontend",
        FAKE_DATE_IANA="2026-10-17 02:25 GMT",
        FAKE_DATE_POSIX="2026-10-16 22:25 EDT",
    )
    assert r.code == 3, r.out
    assert "REFUSE: it is 2026-10-16 22:25 EDT" in r.out
    assert r.index("date TZ=America/New_York") < r.index("date TZ=EST5EDT,M3.2.0,M11.1.0")


def test_refuses_when_no_New_York_clock_can_be_read(deploy):
    r = deploy(
        "--label",
        "pre-deploy",
        "frontend",
        FAKE_DATE_IANA="2026-10-17 02:25 GMT",
        FAKE_DATE_POSIX="2026-10-17 02:25 UTC",
    )
    assert r.code == 3, r.out
    assert "REFUSE: cannot read the America/New_York clock" in r.out


def test_refuses_while_a_pass_is_running(deploy):
    r = deploy("--label", "pre-deploy", "backend", statuses=_status("running"))
    assert r.code == 3, r.out
    assert "REFUSE: a pass is running (cron.status=running)" in r.out
    _no_stack_change(r)


def test_refuses_a_pass_in_flight_behind_a_louder_verdict(deploy):
    """The verdict ranks unhealthy ABOVE running, so a pass in flight on an alarmed stack reads `unhealthy`;
    only `unfinished_run` (still running, inside its timeout) shows it."""
    running = {"status": "running", "past_timeout": False, "started_at": "x", "asof": "2026-10-16"}
    body = _status("unhealthy", ("the newest unlabeled DB dump is 30 h old",), running)
    r = deploy("--label", "pre-deploy", "backend", statuses=body)
    assert r.code == 3, r.out
    assert "REFUSE: a pass is running (cron.status=unhealthy)" in r.out
    _no_stack_change(r)


def test_a_KILLED_pass_past_its_timeout_does_not_block_a_deploy(deploy):
    """A `running` artifact past the run timeout is a dead pass (its alarm says so): the deploy may be the fix."""
    dead = {"status": "running", "past_timeout": True, "started_at": "x", "asof": "2026-10-15"}
    body = _status("unhealthy", ("the last pass did not finish",), dead)
    r = deploy("--label", "pre-deploy", "backend", statuses=body)
    assert r.code == 0, r.out
    assert "ok: no pass running (cron.status=unhealthy" in r.out


def test_refuses_while_the_call_of_record_lock_is_held(deploy):
    holder = "held: pid 4242 (alphadeck-pass:backfill, connected 2026-10-16T14:00:00+00:00)"
    r = deploy("--label", "pre-deploy", "backend", FAKE_LOCK=holder)
    assert r.code == 3, r.out
    assert "REFUSE: the call-of-record lock is held: pid 4242 (alphadeck-pass:backfill" in r.out
    _no_stack_change(r)


def test_refuses_when_admin_status_cannot_be_read(deploy):
    """Fail-closed: no /admin/status means a running pass cannot be ruled out."""
    r = deploy("--label", "pre-deploy", "backend", statuses=None)
    assert r.code == 3, r.out
    assert "REFUSE: cannot read cron.status from http://localhost:8000/admin/status" in r.out
    assert "stack-recovery" in r.out
    _no_stack_change(r)


def test_every_refusal_is_reported_at_once(deploy):
    r = deploy(
        "--label",
        "pre-deploy",
        "backend",
        FAKE_GIT_STATUS="?? x.txt",
        FAKE_GIT_BRANCH="feature",
        FAKE_NOW="2026-10-16 22:30 EDT",
    )
    assert r.code == 3, r.out
    refusals = [ln for ln in r.out.splitlines() if ln.startswith("REFUSE: ")]
    assert len(refusals) == 3, refusals  # dirty tree, branch, window: one run reports them all
    assert "FAIL: refused — 3 check(s) failed" in r.out


# --- dry run --------------------------------------------------------------------------------------------


def test_the_dry_run_prints_every_command_and_changes_nothing(deploy):
    r = deploy("--label", "pre-deploy", "backend", "--dry-run")
    assert r.code == 0, r.out
    pc = "docker compose -f docker-compose.yml -f docker-compose.prod.yml"
    for line in [
        f"would run: {pc} exec -T backend python -m pipeline.backup --label pre-deploy",
        f"would run: GIT_SHA={HEAD} {pc} up -d --build --no-deps backend",
        "would run: docker restart alphadeck-frontend-1",
        "would verify: docker inspect --format '{{.State.Health.Status}}' alphadeck-backend-1 -> healthy",
        "would verify: curl -s --max-time 10 -w '\\n%{http_code}' http://localhost:8080/api/health -> 200",
        f"would verify: docker exec alphadeck-backend-1 printenv ALPHADECK_IMAGE_SHA -> {HEAD}",
        "would show:   docker exec alphadeck-cron-1 printenv ALPHADECK_IMAGE_SHA (not rebuilt",
        "would verify: curl -fsS --max-time 60 http://localhost:8000/admin/status",
        "would read: curl -fsS --max-time 60 http://localhost:8000/admin/status",
        "would read: docker exec alphadeck-backend-1 python -c <pg_locks read>",
        "OK: dry-run",
    ]:
        assert line in r.out, line
    # the backup comes before the build, the build before the restart
    assert r.out.index("pipeline.backup") < r.out.index("--build") < r.out.index("docker restart")
    # it touched no Docker, read no endpoint, and fetched nothing (ls-remote moves no ref)
    assert r.calls("docker") == [] and r.calls("curl") == []
    assert "git ls-remote origin refs/heads/main" in r.log
    assert not [c for c in r.log if c.startswith("git fetch")]


def test_a_dry_run_still_reports_what_the_real_run_would_refuse(deploy):
    r = deploy("--label", "pre-deploy", "frontend", "--dry-run", FAKE_NOW="2026-10-16 22:45 EDT")
    assert r.code == 3, r.out
    assert "REFUSE: it is 2026-10-16 22:45 EDT" in r.out
    assert "would run: GIT_SHA=" in r.out  # the plan is still shown
    assert "FAIL: dry-run — the real run would REFUSE: 1 check(s)" in r.out
    assert r.calls("docker") == [] and r.calls("curl") == []


# --- the happy path ---------------------------------------------------------------------------------------


def test_the_happy_path_backs_up_builds_at_HEAD_restarts_and_verifies(deploy):
    # a stale GIT_SHA exported in the caller's shell must never reach the build
    r = deploy("--label", "pre-deploy", "backend", "cron", GIT_SHA=OTHER)
    assert r.code == 0, r.out
    assert f"OK: deployed {HEAD[:12]} to prod: backend cron (rebuild)" in r.out

    backup = r.index("exec -T backend python -m pipeline.backup --label pre-deploy")
    build = r.index("up -d --build --no-deps backend cron")
    restart = r.index("docker[GIT_SHA=<unset>] restart alphadeck-frontend-1")
    assert backup < build < restart
    assert r.log[build].startswith(f"docker[GIT_SHA={HEAD}] compose ")
    assert r.log[backup].startswith(
        "docker[GIT_SHA=<unset>] compose "
    )  # the sha rides the build only

    # verification ran after the restart, and checked the sha in BOTH rebuilt containers
    assert restart < r.index("inspect --format {{.State.Health.Status}} alphadeck-backend-1")
    assert restart < r.index("http://localhost:8080/api/health")
    assert restart < r.index("exec alphadeck-backend-1 printenv ALPHADECK_IMAGE_SHA")
    assert restart < r.index("exec alphadeck-cron-1 printenv ALPHADECK_IMAGE_SHA")
    assert f"ok: alphadeck-backend-1 ALPHADECK_IMAGE_SHA={HEAD} (the sha just built)" in r.out
    assert f"ok: alphadeck-cron-1 ALPHADECK_IMAGE_SHA={HEAD} (the sha just built)" in r.out
    assert "ok: alphadeck-cron-1 is running and printed its boot lines" in r.out
    assert "daily-cron: each pass bounded at" in r.out  # the boot log tail is shown
    assert "ok: /admin/status reads; cron.status=healthy" in r.out
    assert len(r.calls("curl -fsS --max-time 60 http://localhost:8000/admin/status")) == 2


@pytest.mark.parametrize(
    "services, restarts",
    [
        (["backend"], True),
        (["backend", "cron"], True),
        (["frontend", "backend"], False),  # the frontend recreate re-resolves the backend itself
        (["frontend"], False),
        (["cron"], False),
    ],
)
def test_the_frontend_restart_rule(deploy, services, restarts):
    r = deploy("--label", "pre-deploy", *services)
    assert r.code == 0, r.out
    restarted = [c for c in r.calls("docker") if " restart alphadeck-frontend-1" in c]
    assert bool(restarted) is restarts, r.log
    if restarts:
        assert r.index(" up -d --build ") < r.index(" restart alphadeck-frontend-1")


def test_a_frontend_only_deploy_checks_no_sha_it_did_not_build(deploy):
    r = deploy("--label", "pre-deploy", "frontend")
    assert r.code == 0, r.out
    assert (
        f"info: alphadeck-backend-1 ALPHADECK_IMAGE_SHA={OLD} (not rebuilt by this deploy)" in r.out
    )
    assert "ok: alphadeck-cron-1 is running (not recreated by this deploy)" in r.out


def test_env_only_recreates_cron_with_no_build_and_no_backup(deploy):
    r = deploy("--env-only", "cron")
    assert r.code == 0, r.out
    compose = [c for c in r.calls("docker") if " compose " in c]
    assert compose == [
        "docker[GIT_SHA=<unset>] compose -f docker-compose.yml -f docker-compose.prod.yml "
        "up -d --no-deps --no-build cron"
    ]
    assert "skipped: --env-only without --label" in r.out
    assert (
        f"ok: alphadeck-cron-1 ALPHADECK_IMAGE_SHA={OLD} (unchanged: the env-only recreate kept the image)"
        in r.out
    )
    assert "ok: alphadeck-cron-1 is running and printed its boot lines" in r.out


# --- verification failures (deployed, exit 5) -------------------------------------------------------------


def test_a_sha_mismatch_fails_verification(deploy):
    r = deploy("--label", "pre-deploy", "backend", "cron", FAKE_BUILT_SHA=OTHER)
    assert r.code == 5, r.out
    assert f"FAIL: alphadeck-backend-1 ALPHADECK_IMAGE_SHA={OTHER}, expected {HEAD}" in r.out
    assert f"FAIL: alphadeck-cron-1 ALPHADECK_IMAGE_SHA={OTHER}, expected {HEAD}" in r.out
    assert "2 verification check(s) failed" in r.out
    assert "stack-recovery" in r.out


@pytest.mark.parametrize(
    "fake, says",
    [
        ({"FAKE_HEALTH": "starting"}, "alphadeck-backend-1 health is 'starting', not healthy"),
        ({"FAKE_PROXY_CODE": "502"}, "http://localhost:8080/api/health -> 502"),
        ({"FAKE_CRON_STATE": "restarting"}, "alphadeck-cron-1 is 'restarting', not running"),
        (
            {"FAKE_CRON_BOOT_LINE": "daily-cron: DB not ready"},
            "alphadeck-cron-1 never printed its boot line",
        ),
    ],
)
def test_a_failed_readiness_check_fails_verification(deploy, fake, says):
    r = deploy("--label", "pre-deploy", "backend", "cron", **fake)
    assert r.code == 5, r.out
    assert f"FAIL: {says}" in r.out
    assert "stack-recovery" in r.out


def test_a_new_alarm_fails_verification(deploy):
    before = _status("healthy")
    after = _status("unhealthy", ("the cron's code_sha differs from this backend's",))
    r = deploy("--label", "pre-deploy", "backend", statuses=[before, after])
    assert r.code == 5, r.out
    assert "FAIL: cron.alarms grew from 0 to 1 during the deploy" in r.out
    assert "alarm: the cron's code_sha differs from this backend's" in r.out


def test_a_quiet_to_loud_status_fails_verification(deploy):
    r = deploy("--label", "pre-deploy", "cron", statuses=[_status("healthy"), _status("stale")])
    assert r.code == 5, r.out
    assert "FAIL: cron.status went healthy -> stale during the deploy" in r.out


def test_a_pre_existing_alarm_is_shown_but_does_not_fail_the_deploy(deploy):
    body = _status("unhealthy", ("the newest unlabeled DB dump is 30 h old",))
    r = deploy("--label", "pre-deploy", "frontend", statuses=body)
    assert r.code == 0, r.out
    assert "cron.status=unhealthy was already unhealthy before the deploy" in r.out
    assert "alarm: the newest unlabeled DB dump is 30 h old" in r.out


# --- a failed step (exit 4) -------------------------------------------------------------------------------


def test_a_failed_backup_stops_before_the_build(deploy):
    r = deploy("--label", "pre-deploy", "backend", FAKE_BACKUP_FAIL="1")
    assert r.code == 4, r.out
    assert "FAIL: the pre-deploy backup failed — nothing was rebuilt" in r.out
    assert not [c for c in r.calls("docker") if " up " in c or " restart " in c]


def test_a_failed_build_stops_before_the_restart_and_the_checks(deploy):
    r = deploy("--label", "pre-deploy", "backend", FAKE_BUILD_FAIL="1")
    assert r.code == 4, r.out
    assert "FAIL: the rebuild failed" in r.out and "stack-recovery" in r.out
    assert not [c for c in r.calls("docker") if " restart " in c or " printenv " in c]
