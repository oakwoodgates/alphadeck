import { useState } from "react";

import type { BacktestRunSummaryOut } from "../api/hooks";
import { type Arm, armLabel, defaultOpen, groupArms, groupLabel, groupRuns } from "./passes";

// The run picker — the registry, newest first, one row per completed run, GROUPED BY PASS and, within an
// open pass, SUB-GROUPED BY POLICY ARM.
//
// It shows what a run IS rather than any outcome. That is the whole discipline of this surface in one
// control: a picker that ranked runs by a metric would be a leaderboard over experiments, and the reader
// would pick the winner before reading the nulls. So the rows carry when, over which window, with how many
// episodes — and nothing about how it came out.
//
// THE ARM SUB-GROUPING (C). Within one pass the runs form a policy-arm × window matrix: a phase sweeps a
// few policy configurations and replays each over every window (phase-1b was 3 configs × 15 windows). A
// flat wrap of dozens of near-identical cards buried that structure and let the run_id dominate each card.
// So an open pass now names each ARM once — which dials it moved, to what value (COMMIT B), how many runs —
// and the rows beneath it carry only what varies run to run: the window, and the episode count. The
// run_id is demoted to a muted footer but KEPT: it is the shareable, citable key. Arms are not
// independently collapsible in this version — the pass open/close is the only state.
//
// GROUPING IS NEVER A FILTER. Every run stays on screen; the groups only decide what is expanded. The
// newest REAL pass opens, the rest collapse in one click, still counted in their headers. An experiment
// you cannot see is one you cannot count against yourself.

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
      {/* What varies run to run within an arm: the window and the episode count. The policy and the dials
          are on the arm header above, so they are not repeated here. */}
      <span className="bt-run-meta">
        {r.window_start} → {r.window_end} · clock {r.clock} · {r.n_episodes} episode
        {r.n_episodes === 1 ? "" : "s"}
      </span>
      {/* The run_id, demoted to a muted footer but kept — it is the shareable/citable key. */}
      <span className="bt-run-id">{r.run_id}</span>
    </button>
  );
}

function ArmGroup({
  arm,
  selected,
  onSelect,
}: {
  arm: Arm;
  selected: string | null;
  onSelect: (runId: string) => void;
}) {
  const n = arm.runs.length;
  return (
    <div className="bt-arm">
      <div className="bt-arm-head">
        <span className="lbl">{armLabel(arm)}</span>
        <span className="cfg">policy {arm.configShort}</span>
        <span className="ct">
          {n} run{n === 1 ? "" : "s"}
        </span>
      </div>
      <div className="bt-runs">
        {arm.runs.map((r) => (
          <RunRow key={r.run_id} r={r} on={r.run_id === selected} onSelect={onSelect} />
        ))}
      </div>
    </div>
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
              <div className="bt-arms">
                {groupArms(g.items).map((a) => (
                  <ArmGroup key={a.configHash} arm={a} selected={selected} onSelect={onSelect} />
                ))}
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}
