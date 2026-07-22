import { useEffect, useState } from "react";

import type { AdminRunOut } from "../api/hooks";
import {
  useAdminRuns,
  useAdminStatus,
  useBackupJob,
  useBackups,
  useCreateBackup,
  useDailyRunJob,
  useStartDailyRun,
} from "../api/hooks";
import { errText } from "../workbench/format";

interface Props {
  onBack: () => void;
  onOpenWorkbench: () => void;
  onOpenScoreboard: () => void;
}

/** UTC artifact stamp -> a compact readable form ("2026-07-20 22:30Z"). */
const fmtStamp = (iso: string) => iso.replace("T", " ").slice(0, 16) + "Z";

const fmtDur = (s: number) => (s >= 90 ? `${Math.round(s / 60)} min` : `${Math.round(s)}s`);

/** Bytes -> a compact human size ("512 B" / "3.4 KB" / "18.0 MB"). */
const fmtBytes = (n: number) => {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
};

/** A UTC ISO instant -> a compact "how long ago" ("40s" / "12 min" / "3h" / "2d"); "recently" if
 *  unparseable. The last-snapshot line uses it; the exact stamp still shows in the list. */
const fmtAge = (iso: string) => {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "recently";
  const secs = Math.max(0, (Date.now() - then) / 1000);
  if (secs < 90) return `${Math.round(secs)}s`;
  if (secs < 5400) return `${Math.round(secs / 60)} min`;
  if (secs < 129600) return `${Math.round(secs / 3600)}h`;
  return `${Math.round(secs / 86400)}d`;
};

/** One run's counts, the artifact's own vocabulary — shared by the last-run line, the run-now result,
 *  and (column-by-column) the history table. */
function RunSummaryLine({ run }: { run: AdminRunOut }) {
  return (
    <span className="adm-runline">
      asof <b>{run.asof}</b> ({run.mode}) — {run.appended} appended · {run.unchanged} unchanged
      {run.withheld > 0 && <> · {run.withheld} withheld</>}
      {run.errored > 0 && <> · {run.errored} errored</>}
      {run.transitions > 0 && <> · {run.transitions} transitions</>} · {run.edgar_fetches} EDGAR
      fetches · {fmtDur(run.duration_s)}
    </span>
  );
}

function Problems({ problems }: { problems: AdminRunOut["problems"] }) {
  if (!problems?.length) return null;
  return (
    <ul className="adm-problems">
      {problems.map((p) => (
        <li key={p}>{p}</li>
      ))}
    </ul>
  );
}

/** The Operator Admin (ops surface, Slice 1) — a READ surface over the cron's own instrumentation
 *  (record freshness vs the Mon-Fri+RUN_AT schedule, the run-of-record history, a health verdict)
 *  plus ONE explicit trigger: "Run daily now". Honest loudness throughout: loud styling is reserved
 *  for stale / unhealthy; "current", "never begun", and "never ran" stay quiet. The trigger fires
 *  ONLY on the button click — never on mount, render, or poll (reads may poll; the trigger may not). */
export function Admin({ onBack, onOpenWorkbench, onOpenScoreboard }: Props) {
  const statusQ = useAdminStatus();
  const runsQ = useAdminRuns(20);
  const start = useStartDailyRun();
  const [jobId, setJobId] = useState<string | null>(null);
  const jobQ = useDailyRunJob(jobId);

  const job = jobQ.data ?? null;
  // kicked off and not yet terminal: the first poll in flight (job null) counts as running
  const running = jobId !== null && !jobQ.isError && (job === null || job.status === "running");

  // Backups (Slice 4): the Create-snapshot trigger + its poll + the list. Same operator-initiated
  // discipline as the run trigger — the create fires ONLY on the click; the list + age are reads.
  const backupsQ = useBackups();
  const createBackup = useCreateBackup();
  const [backupJobId, setBackupJobId] = useState<string | null>(null);
  const [backupLabel, setBackupLabel] = useState("");
  const backupJobQ = useBackupJob(backupJobId);
  const backupJob = backupJobQ.data ?? null;
  const backupRunning =
    backupJobId !== null &&
    !backupJobQ.isError &&
    (backupJob === null || backupJob.status === "running");

  // when a run lands (done OR failed), the freshness + history reads are stale — refresh them.
  // A READ refresh only: nothing here can re-fire the trigger.
  const jobStatus = job?.status;
  useEffect(() => {
    if (jobStatus === "done" || jobStatus === "failed") {
      void statusQ.refetch();
      void runsQ.refetch();
    }
    // statusQ/runsQ are stable-enough query handles; keying on the terminal transition is the intent
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobStatus]);

  // when a snapshot lands (done OR failed), refresh the list + the last-snapshot age. A READ refresh
  // only: nothing here re-fires the create trigger (the operator-initiated invariant).
  const backupJobStatus = backupJob?.status;
  useEffect(() => {
    if (backupJobStatus === "done" || backupJobStatus === "failed") {
      void backupsQ.refetch();
      void statusQ.refetch();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [backupJobStatus]);

  const status = statusQ.data;
  const runs = runsQ.data?.runs ?? [];
  const backups = backupsQ.data?.backups ?? [];

  return (
    <div className="board-shell adm-shell">
      <header className="topbar">
        <div className="brand">
          <span className="dot" />
          ALPHA&nbsp;DECK <small>// research cockpit</small>
        </div>
        <nav className="nav">
          <a onClick={onBack}>Board</a>
          <a onClick={onOpenWorkbench}>Workbench</a>
          <a onClick={onOpenScoreboard}>Scoreboard</a>
          <a className="on">Admin</a>
        </nav>
        <div className="spacer" />
        {/* no as-of dial: this is a "now" ops surface, not an as-of-scrubable view */}
      </header>

      {statusQ.isLoading && <div className="center-note">Reading the run record…</div>}
      {statusQ.error != null && (
        <div className="center-note err">Admin status unavailable — is the backend on :8000?</div>
      )}

      {status && (
        <div className="adm-body">
          {/* 1 — freshness / the dead-man's switch: loud ONLY when stale */}
          <section
            className={`adm-card adm-fresh${status.record.stale ? " stale" : ""}`}
            data-testid="adm-fresh"
          >
            <div className="adm-h">Record freshness</div>
            {status.record.edge == null ? (
              <div className="adm-line adm-quiet">
                The record has never begun — no call-of-record yet. It starts with the first daily
                run.
              </div>
            ) : status.record.stale ? (
              <div className="adm-line adm-loud">
                record last advanced <b>{status.record.edge}</b> ·{" "}
                <b>{status.record.days_behind}</b> expected run(s) behind
              </div>
            ) : (
              <div className="adm-line adm-quiet">
                record last advanced <b>{status.record.edge}</b> · current
              </div>
            )}
            <div className="adm-sub">
              {status.record.reason} · today {status.record.today} · last expected as-of{" "}
              {status.record.expected_asof}
            </div>
          </section>

          {/* 2 — cron health: the one-word verdict + the last run */}
          <section className="adm-card" data-testid="adm-cron">
            <div className="adm-h">Cron health</div>
            <div className="adm-line">
              <span className={`adm-chip s-${status.cron.status}`}>
                {status.cron.status.replace("_", " ")}
              </span>
              <span className="adm-sub">{status.cron.detail}</span>
            </div>
            {status.last_run && (
              <div className="adm-lastrun">
                <span className="adm-sub">last run {fmtStamp(status.last_run.ran_at)} · </span>
                <RunSummaryLine run={status.last_run} />
                {!status.last_run.healthy && <Problems problems={status.last_run.problems} />}
              </div>
            )}
          </section>

          {/* 3 — the ONE trigger: explicit click only, disabled + progress while running */}
          <section className="adm-card" data-testid="adm-run">
            <div className="adm-h">Run daily now</div>
            <div className="adm-runrow">
              <button
                type="button"
                className="adm-runbtn"
                disabled={running || start.isPending}
                onClick={() =>
                  start.mutate(undefined, { onSuccess: (ref) => setJobId(ref.job_id) })
                }
              >
                {running ? "Running…" : "Run daily now"}
              </button>
              <span className="adm-note">
                does a LIVE EDGAR pull — ~2 min warm, up to ~65 min on a cold cache
              </span>
            </div>
            {start.isError && <div className="adm-err">{errText(start.error)}</div>}
            {running && (
              <div className="adm-progress">
                running the full daily pass (ingest → call-of-record → run log)… the result appears
                here when it lands; a cold pass can take ~65 min.
              </div>
            )}
            {jobQ.isError && (
              <div className="adm-err">
                the run was lost from view (the server restarted or the job expired) — it may still
                be finishing; check the run history below.
              </div>
            )}
            {job?.status === "failed" && <div className="adm-err">run failed: {job.error}</div>}
            {job?.status === "done" && job.result && (
              <div
                className={`adm-done${job.result.healthy ? "" : " bad"}`}
                data-testid="adm-run-result"
              >
                done — <RunSummaryLine run={job.result} />
                {!job.result.healthy && <Problems problems={job.result.problems} />}
              </div>
            )}
          </section>

          {/* 3b — Backups: create a DB snapshot (operator-initiated) + the list + last-snapshot age.
              CREATE + LIST only; RESTORE is a deliberate CLI act, never a button. */}
          <section className="adm-card" data-testid="adm-backups">
            <div className="adm-h">Backups</div>
            {status.last_backup == null ? (
              <div className="adm-line adm-quiet">no snapshots yet — create one</div>
            ) : (
              <div className="adm-line adm-quiet">
                last snapshot <b>{fmtAge(status.last_backup.created_at)}</b> ago
              </div>
            )}
            <div className="adm-runrow">
              <button
                type="button"
                className="adm-runbtn"
                disabled={backupRunning || createBackup.isPending}
                onClick={() =>
                  createBackup.mutate(
                    { label: backupLabel },
                    { onSuccess: (ref) => setBackupJobId(ref.job_id) },
                  )
                }
              >
                {backupRunning ? "Creating…" : "Create snapshot"}
              </button>
              <input
                className="adm-input"
                type="text"
                placeholder="optional label (e.g. pre-migration)"
                value={backupLabel}
                onChange={(e) => setBackupLabel(e.target.value)}
                disabled={backupRunning}
              />
              <span className="adm-note">
                pg_dump to ./data/backups — a labeled dump is kept (never auto-pruned); restore is CLI-only
              </span>
            </div>
            {createBackup.isError && <div className="adm-err">{errText(createBackup.error)}</div>}
            {backupRunning && <div className="adm-progress">running pg_dump…</div>}
            {backupJobQ.isError && (
              <div className="adm-err">
                the snapshot was lost from view (the server restarted or the job expired) — it may
                still have completed; check the list below.
              </div>
            )}
            {backupJob?.status === "failed" && (
              <div className="adm-err">snapshot failed: {backupJob.error}</div>
            )}
            {backupJob?.status === "done" && backupJob.result && (
              <div className="adm-done" data-testid="adm-backup-result">
                done — {backupJob.result.name} ({fmtBytes(backupJob.result.bytes)})
              </div>
            )}

            {backupsQ.isLoading && <div className="adm-sub">loading…</div>}
            {backupsQ.error != null && <div className="adm-err">backups unavailable</div>}
            {backupsQ.data && backups.length === 0 && (
              <div className="adm-line adm-quiet">no snapshots yet</div>
            )}
            {backups.length > 0 && (
              <table className="basket adm-histtbl">
                <thead>
                  <tr>
                    <th>snapshot</th>
                    <th>size</th>
                    <th>created</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {backups.map((b) => (
                    <tr key={b.name}>
                      <td className="adm-mono">{b.name}</td>
                      <td className="adm-num">{fmtBytes(b.bytes)}</td>
                      <td className="adm-mono">{fmtStamp(b.created_at)}</td>
                      <td>
                        {b.labeled && <span className="adm-badge b-labeled">labeled</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          {/* 4 — run history: the run-of-record artifacts, newest first */}
          <section className="adm-card" data-testid="adm-hist">
            <div className="adm-h">Run history</div>
            {runsQ.isLoading && <div className="adm-sub">loading…</div>}
            {runsQ.error != null && <div className="adm-err">run history unavailable</div>}
            {runsQ.data && runs.length === 0 && (
              <div className="adm-line adm-quiet">no runs recorded yet</div>
            )}
            {runs.length > 0 && (
              <table className="basket adm-histtbl">
                <thead>
                  <tr>
                    <th>ran</th>
                    <th>as-of</th>
                    <th>mode</th>
                    <th>appended</th>
                    <th>unchanged</th>
                    <th>withheld</th>
                    <th>errored</th>
                    <th>transitions</th>
                    <th>edgar</th>
                    <th>took</th>
                    <th>health</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.map((r) => (
                    <tr key={r.ran_at} className={r.healthy ? undefined : "adm-row-bad"}>
                      <td className="adm-mono">{fmtStamp(r.ran_at)}</td>
                      <td className="adm-mono">{r.asof}</td>
                      <td className="adm-mono">{r.mode}</td>
                      <td className="adm-num">{r.appended}</td>
                      <td className="adm-num">{r.unchanged}</td>
                      <td className="adm-num">{r.withheld}</td>
                      <td className="adm-num">{r.errored}</td>
                      <td className="adm-num">{r.transitions}</td>
                      <td className="adm-num">{r.edgar_fetches}</td>
                      <td className="adm-num">{fmtDur(r.duration_s)}</td>
                      <td>
                        {r.healthy ? (
                          <span className="adm-ok">ok</span>
                        ) : (
                          <span className="adm-bad" title={r.problems.join("; ")}>
                            {r.problems[0] ?? "unhealthy"}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
