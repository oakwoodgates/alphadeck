import { useState } from "react";

import type { BacktestRunSummaryOut } from "../api/hooks";
import { defaultOpen, groupLabel, groupRuns } from "./passes";

// The run picker — the registry, newest first, one row per completed run, GROUPED BY PASS.
//
// It shows the DIALS each run moved rather than any outcome. That is the whole discipline of this
// surface in one control: a picker that ranked runs by a metric would be a leaderboard over
// experiments, and the reader would pick the winner before reading the nulls. So the rows carry what
// a run IS (when, which policy, which window, which dials, how many episodes) and nothing about how
// it came out.
//
// THE GROUPING IS NEW (D) AND IT IS NOT A FILTER. A windowed pass writes dozens of runs — 225 for one
// real pass — so a flat list stopped being readable the first time one landed, with the four smoke runs
// that preceded it scattered among them. The newest REAL pass opens; everything else collapses, in one
// click, still counted in its header. Nothing is hidden and nothing is dropped: the registry's job is
// counting trials, and an experiment you cannot see is one you cannot count against yourself.

type Props = {
  runs: readonly BacktestRunSummaryOut[];
  selected: string | null;
  onSelect: (runId: string) => void;
};

function RunRow({
  r,
  on,
  onSelect,
}: {
  r: BacktestRunSummaryOut;
  on: boolean;
  onSelect: (runId: string) => void;
}) {
  return (
    <button
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
      </span>
    </button>
  );
}

export function RunPicker({ runs, selected, onSelect }: Props) {
  const groups = groupRuns(runs);
  // The pass holding the SELECTED run always opens, whatever the default would be: a shared link to a
  // run inside a collapsed calibration pass must land on something visible.
  const selectedPass = runs.find((r) => r.run_id === selected)?.pass_id ?? null;
  const [open, setOpen] = useState<string | null>(() => selectedPass ?? defaultOpen(groups));
  if (!runs.length) return null;

  return (
    <div className="bt-passes" role="group" aria-label="Runs">
      {groups.map((g) => {
        const isOpen = g.passId === open || g.passId === selectedPass;
        return (
          <section key={g.passId} className={`bt-pass${g.calibration ? " cal" : ""}`}>
            <button
              type="button"
              className="grp-h rp-head"
              aria-expanded={isOpen}
              onClick={() => setOpen(isOpen ? null : g.passId)}
              title={g.hypothesis || "no pre-registered hypothesis — exploratory runs"}
            >
              <span className="chev">{isOpen ? "▾" : "▸"}</span>
              <span className="lbl">{groupLabel(g, "run")}</span>
            </button>
            {isOpen && (
              <div className="bt-runs">
                {g.items.map((r) => (
                  <RunRow key={r.run_id} r={r} on={r.run_id === selected} onSelect={onSelect} />
                ))}
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}
