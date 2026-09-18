import { useState } from "react";

import type { BacktestSweepRefOut } from "../api/hooks";
import { defaultOpen, groupLabel, groupSweeps } from "./passes";

// THE CURVE SWITCHER — every curve a pass wrote, not just the last one.
//
// `sweep.json` is latest-only, so the moment a pass wrote six curves five of them were unreachable from
// this page the instant the sixth landed. The runs survived (each point cites its own run_id); the curves
// did not. This picks among the kept copies.
//
// It shows what a curve IS — the dial, the slice it was read on, the window, how wide its band came out
// and WHICH RULE that band was keyed on — and nothing about whether the result was good. A band width
// without its rule is not a fact about a dial; and a picker sorted by band width would be a leaderboard
// over experiments, which is the same trap the run picker refuses.

export type CurveSelection = {
  pass_id: string;
  dial: string;
  metric_slice: string;
};

type Props = {
  sweeps: readonly BacktestSweepRefOut[];
  selected: CurveSelection | null;
  onSelect: (sel: CurveSelection) => void;
};

export function same(a: CurveSelection | null, b: BacktestSweepRefOut): boolean {
  return (
    a != null &&
    a.pass_id === b.pass_id &&
    a.dial === b.dial &&
    a.metric_slice === (b.metric_slice ?? "")
  );
}

export function SweepPicker({ sweeps, selected, onSelect }: Props) {
  const groups = groupSweeps(sweeps);
  const [open, setOpen] = useState<string | null>(() => defaultOpen(groups));
  if (!sweeps.length) return null;

  return (
    <div className="bt-passes" role="group" aria-label="Sweep curves">
      {groups.map((g) => {
        const on = g.passId === open;
        return (
          <section key={g.passId} className={`bt-pass${g.calibration ? " cal" : ""}`}>
            <button
              type="button"
              className="grp-h rp-head"
              aria-expanded={on}
              onClick={() => setOpen(on ? null : g.passId)}
              title={g.hypothesis || "no pre-registered hypothesis"}
            >
              <span className="chev">{on ? "▾" : "▸"}</span>
              <span className="lbl">{groupLabel(g, "curve")}</span>
            </button>
            {on && (
              <div className="bt-curves">
                {g.items.map((c) => {
                  const picked = same(selected, c);
                  return (
                    <button
                      key={`${c.pass_id}|${c.dial}|${c.metric_slice}`}
                      type="button"
                      className={`bt-curve${picked ? " on" : ""}`}
                      aria-pressed={picked}
                      onClick={() =>
                        onSelect({
                          pass_id: c.pass_id,
                          dial: c.dial,
                          metric_slice: c.metric_slice ?? "",
                        })
                      }
                      title={c.decision_rule || "no pre-registered decision rule"}
                    >
                      <span className="bt-curve-dial">{c.dial}</span>
                      {c.metric_slice && (
                        <span className="sb-badge b-base" title="read on ONE algorithm family">
                          {c.metric_slice}
                        </span>
                      )}
                      <span className="bt-curve-meta">
                        {c.n_points} point{c.n_points === 1 ? "" : "s"} · {c.n_windows} window
                        {c.n_windows === 1 ? "" : "s"} · {c.clock} clock ·{" "}
                        {c.plateau_width > 1
                          ? `band ${c.plateau_width} wide`
                          : "no band (nothing found)"}{" "}
                        <em title="the cross-window agreement the band was keyed on — a band is meaningless without it">
                          keyed on {c.plateau_rule}
                        </em>
                        {c.mirror_reused && (
                          <em title="this pass swept an existing frozen mirror rather than exporting its own — the same tape an earlier pass used">
                            {" "}
                            · inherited tape
                          </em>
                        )}
                      </span>
                    </button>
                  );
                })}
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}
