import type { BacktestRunSummaryOut } from "../api/hooks";

// The run picker — the registry, newest first, one row per completed run.
//
// It shows the DIALS each run moved rather than any outcome. That is the whole discipline of this
// surface in one control: a picker that ranked runs by a metric would be a leaderboard over
// experiments, and the reader would pick the winner before reading the nulls. So the rows carry what
// a run IS (when, which policy, which window, which dials, how many episodes) and nothing about how
// it came out.

type Props = {
  runs: readonly BacktestRunSummaryOut[];
  selected: string | null;
  onSelect: (runId: string) => void;
};

export function RunPicker({ runs, selected, onSelect }: Props) {
  if (!runs.length) return null;
  return (
    <div className="bt-runs" role="group" aria-label="Runs">
      {runs.map((r) => {
        const on = r.run_id === selected;
        return (
          <button
            key={r.run_id}
            type="button"
            className={`bt-run${on ? " on" : ""}`}
            aria-pressed={on}
            onClick={() => onSelect(r.run_id)}
            title={r.hypothesis ?? "no pre-registered hypothesis — an exploratory run"}
          >
            <span className="bt-run-id">{r.run_id}</span>
            <span className="bt-run-meta">
              {r.window_start} → {r.window_end} · policy {r.config_short} · clock {r.clock} ·{" "}
              {r.n_episodes} episode{r.n_episodes === 1 ? "" : "s"}
            </span>
            <span className="bt-run-dials">
              {(r.dials_moved ?? []).length === 0
                ? "production dials"
                : `dials: ${(r.dials_moved ?? []).join(", ")}`}
              {/* A windowed pass writes dozens of runs (S1), so the registry is no longer a short list of
                  standalone experiments — without the pass id a reader cannot tell which rows belong to
                  one curve, and `sweep.json` cannot answer it because it is latest-only. */}
              {r.pass_id ? ` · pass ${r.pass_id}` : ""}
            </span>
          </button>
        );
      })}
    </div>
  );
}
