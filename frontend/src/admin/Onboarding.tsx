import type { OnboardingNameOut } from "../api/hooks";
import { useOnboarding, useRetryOnboarding } from "../api/hooks";
import { errText } from "../workbench/format";

const LEG_WORD: Record<string, string> = {
  form4: "Form 4",
  form8k: "8-K",
  schedule13: "13D/G",
  price: "prices",
  fund_shares: "fund shares",
  fundamentals: "fundamentals",
};

const STOP_WORD: Record<string, string> = {
  done: "the queue ran out",
  window: "entered the nightly window",
  pass_running: "yielded to a running pass",
  budget_names: "the run's name budget",
  budget_time: "the run's time budget",
  daily_cap: "the daily cap",
};

const stamp = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" }) : "—";

/** "Form 4, 8-K of 3 history legs" — the name's checkpoint, in words. */
function Legs({ n, history }: { n: OnboardingNameOut; history: string[] }) {
  const doneHistory = history.filter((l) => n.legs_done.includes(l));
  if (n.legs_done.length === 0) return <span className="adm-sub">no leg loaded yet</span>;
  return (
    <span className="adm-sub">
      {doneHistory.length} of {history.length} history legs loaded
      {doneHistory.length > 0 ? ` (${doneHistory.map((l) => LEG_WORD[l] ?? l).join(", ")})` : ""}
    </span>
  );
}

/** The "Onboarding" panel (X1): the staged cold-ingest queue. A cold name's first ingest (its whole Form 4
 *  history, MEASURED 300-600 s) loads in its own job OUTSIDE the nightly window; until a name is loaded the nightly reads
 *  only its recent filings and stamps its thesis "not fully loaded". QUIET (one summary line + a collapsed
 *  list) while names wait or load; LOUD only when a name FAILED (its history legs failed every attempt —
 *  Retry re-queues it) or the queue STALLED (names waiting, no job run finished for hours). Renders nothing
 *  when the queue has never held a name. */
export function Onboarding() {
  const q = useOnboarding();
  const retry = useRetryOnboarding();

  if (q.isError) {
    return (
      <section className="adm-card" data-testid="adm-onboarding">
        <div className="adm-h">Onboarding</div>
        <div className="adm-err">{errText(q.error)}</div>
      </section>
    );
  }
  const d = q.data;
  if (!d) return null;
  const c = d.counts;
  const total = Object.entries(c)
    .filter(([k]) => k !== "parked")
    .reduce((s, [, v]) => s + v, 0);
  if (total === 0 && !d.stalled) return null;
  const failed = d.pending.filter((n) => n.status === "failed");
  const waiting = d.pending.filter((n) => n.status !== "failed");
  const run = d.last_run;

  return (
    <section className="adm-card" data-testid="adm-onboarding">
      <div className="adm-h">Onboarding</div>

      {d.stalled && (
        <div className="adm-line adm-loud" data-testid="adm-onboarding-stalled">
          STALLED — {d.stalled}
        </div>
      )}

      <div className="adm-line adm-quiet" data-testid="adm-onboarding-summary">
        {c.queued ?? 0} queued · {c.in_progress ?? 0} loading · {c.done ?? 0} loaded · {c.failed ?? 0} failed
        {c.parked ? ` · ${c.parked} parked (in no live basket)` : ""} — history loads in batches outside the
        nightly window ({d.window})
      </div>
      {d.paused && (
        <div className="adm-sub" data-testid="adm-onboarding-paused">
          {d.paused}
        </div>
      )}
      {run && (
        <div className="adm-sub" data-testid="adm-onboarding-run">
          last run {stamp(run.started_at)} ({run.run_kind ?? "?"}, {run.status}) —{" "}
          {Object.entries(run.counts ?? {})
            .map(([k, v]) => `${v} ${k}`)
            .join(", ") || "nothing"}
          {run.stopped ? `; stopped: ${STOP_WORD[run.stopped] ?? run.stopped}` : ""}
        </div>
      )}

      {failed.length > 0 && (
        <div className="adm-stale" data-testid="adm-onboarding-failed">
          <div className="adm-line adm-loud">
            <b>{failed.length}</b> name(s) FAILED to load their history — the nightly still reads their recent
            filings; Retry queues them again
          </div>
          <ul className="adm-repairs">
            {failed.map((n) => (
              <li key={n.security_id} data-testid="adm-onboarding-failed-row">
                <b>{n.ticker ?? n.security_id}</b>
                {n.name ? <span className="adm-sub"> ({n.name})</span> : null} — <Legs n={n} history={d.history_legs} />{" "}
                · {n.attempts} attempt(s)
                {n.last_error ? <div className="adm-sub adm-mono">{n.last_error}</div> : null}
                <div className="adm-repair-act">
                  <button
                    type="button"
                    className="adm-minibtn primary"
                    disabled={retry.isPending}
                    onClick={() => retry.mutate(n.security_id)}
                  >
                    Retry
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {waiting.length > 0 && (
        <details className="adm-quietlist" data-testid="adm-onboarding-waiting">
          <summary className="adm-line adm-quiet">{waiting.length} waiting or loading</summary>
          <ul className="adm-problems adm-quietrows">
            {waiting.map((n) => (
              <li
                key={n.security_id}
                className={n.in_live_basket ? undefined : "adm-grayed"}
                data-testid="adm-onboarding-waiting-row"
              >
                <b>{n.ticker ?? n.security_id}</b> — {n.status === "in_progress" ? "loading" : "queued"} ·{" "}
                <Legs n={n} history={d.history_legs} />
                {n.in_live_basket ? "" : " · parked: no live basket holds it (resumes if re-added)"}
                {n.attempts > 0 && n.last_error ? (
                  <span className="adm-sub">
                    {" "}
                    · {n.attempts} failed attempt(s): {n.last_error}
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        </details>
      )}

      {d.recent_done.length > 0 && (
        <details className="adm-quietlist" data-testid="adm-onboarding-done">
          <summary className="adm-line adm-quiet">latest {d.recent_done.length} loaded</summary>
          <ul className="adm-problems adm-quietrows">
            {d.recent_done.map((n) => (
              <li key={n.security_id}>
                <b>{n.ticker ?? n.security_id}</b> — loaded {stamp(n.finished_at)}
                {n.last_error ? <span className="adm-sub"> · {n.last_error}</span> : null}
              </li>
            ))}
          </ul>
        </details>
      )}

      {retry.isError && <div className="adm-err">{errText(retry.error)}</div>}
    </section>
  );
}
