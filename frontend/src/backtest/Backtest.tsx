import type { ReactNode } from "react";

import { useBacktestRun, useBacktestRuns, useBacktestSweep } from "../api/hooks";
import { errText } from "../workbench/format";
import { BacktestLabels } from "./BacktestLabels";
import { ManifestCard } from "./ManifestCard";
import { PooledPanel } from "./PooledPanel";
import { RunLedger } from "./RunLedger";
import { RunPicker } from "./RunPicker";
import { SweepCurve } from "./SweepCurve";

// THE BACKTEST — a research surface, and nothing on it is ever the record.
//
// It is dev/sig-only BY DATA AVAILABILITY, with no build flag anywhere: a run is written by a CLI that
// needs the `.[replay]` extra, the store directory does not exist on prod, and this page then says one
// quiet line. The nav tab always renders — the `available:false` idiom hides SECTIONS, not tabs, and
// gating the tab would cost a request on every page load to answer a question this page answers itself.
//
// READING ORDER IS THE DESIGN. The five permanent labels first (what this surface is and is not), then
// the run and what produced it, then the POOLED view — the algorithm as the unit — and only then, folded
// away, the per-thesis rows. Per-thesis outcomes are the leaderboard trap: the platform is opinionated
// about timing and deferential about the idea (#4), so the thesis rows are a drill-down a reader has to
// go and get, never the headline.
//
// Nothing here writes, nothing here notifies, and no surface links INTO it: Board, Cockpit and
// Scoreboard do not know it exists. A simulated call has never been near `calls`.

type Props = {
  header?: ReactNode;
  /** The selected run, from ?run= — so a run is a shareable link. */
  runId: string | null;
  onSelectRun: (runId: string) => void;
  /** The ↗ jump from a ledger row to that name's Cockpit. */
  onSelect: (thesisId: string, nameKey?: string) => void;
};

export function Backtest({ header, runId, onSelectRun, onSelect }: Props) {
  const runsQ = useBacktestRuns();
  const sweepQ = useBacktestSweep();
  const runs = runsQ.data?.runs ?? [];
  // No explicit pick → the newest run, which is the registry's own first row. A default rather than an
  // empty state: the operator who just finished a run should land on it.
  const selected = runId ?? runs[0]?.run_id ?? null;
  const runQ = useBacktestRun(selected);
  const run = runQ.data;
  const storeMissing = runsQ.data != null && runsQ.data.available === false;
  // The labels ride every numeric response; whichever is loaded serves them, and they render once.
  const labels = run?.labels?.length ? run.labels : (sweepQ.data?.labels ?? []);

  return (
    <div className="board-shell">
      {header}
      <main className="sb-body bt-body">
        <div className="sect-h">
          Backtest{" "}
          <em>
            · replaying the call algorithm over history — a recompute under chosen dials, never the
            record, never a trade signal
          </em>
        </div>

        <BacktestLabels labels={labels} />

        {runsQ.isLoading && <div className="bt-quiet">Loading…</div>}
        {runsQ.error != null && (
          <div className="note err">backtest unreachable: {errText(runsQ.error)}</div>
        )}

        {/* The normal state on prod — one quiet line, not an empty shell and not an error. */}
        {storeMissing && (
          <p className="bt-quiet">
            No backtest runs on this stack. Runs are written by the CLI (
            <code>python -m backtest.run --start … --end …</code>) in an environment carrying the
            replay extra, and this stack has no run store.
          </p>
        )}

        {!storeMissing && runsQ.data?.available && runs.length === 0 && (
          <p className="bt-quiet">The run store exists but holds no completed run yet.</p>
        )}

        {runs.length > 0 && (
          <RunPicker runs={runs} selected={selected} onSelect={onSelectRun} />
        )}

        {runQ.error != null && (
          <div className="note err">run unreachable: {errText(runQ.error)}</div>
        )}
        {run != null && run.available === false && (
          <p className="bt-quiet">
            That run is not on this stack — it may have been written somewhere else, or the link may be
            stale.
          </p>
        )}

        {run?.available && run.manifest && (
          <ManifestCard manifest={run.manifest} dialTrials={runsQ.data?.dial_trials ?? {}} />
        )}

        {/* POOLED FIRST. A run with no pooled view was written before the nulls existed; saying so is
            the honest rendering, because the alternative is showing returns with nothing to read them
            against. */}
        {run?.available &&
          (run.pooled ? (
            <PooledPanel pooled={run.pooled} />
          ) : (
            <p className="bt-quiet">
              This run has no pooled view — it predates the null models. Its episodes are below, but
              there is nothing on this page to read their returns against.
            </p>
          ))}

        {run?.available && run.ledger && (
          <RunLedger
            ledger={run.ledger}
            episodes={run.episodes ?? []}
            onSelect={onSelect}
          />
        )}
        {run?.available && !run.ledger && (
          <p className="bt-quiet">This run carries no episode ledger — it predates the drill-down.</p>
        )}

        {sweepQ.data?.available && <SweepCurve sweep={sweepQ.data.sweep} />}
      </main>
    </div>
  );
}
