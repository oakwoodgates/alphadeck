import type { ScoreboardEpisodeOut } from "../api/hooks";
import { awaitingForwardBar, fmtReturn, operatorLine } from "./rows";

// Pure display logic for the episode scorecard's four timing lenses (the rows.ts model: unit-tested,
// no React). Honest loudness throughout: a lens with no data returns null (the component renders
// nothing), a missing leg reads "—", never "null"/"NaN". Every number here is already on the wire —
// this module only phrases and gates what ScoreboardEpisodeOut already carries.

type Ret = ReturnType<typeof fmtReturn>;

/** A raw close, in the operator cell's price idiom (`$` + two decimals); "—" when unknowable. */
export function fmtPrice(x: number | null | undefined): string {
  if (x === null || x === undefined) return "—";
  return `$${x.toFixed(2)}`;
}

/** No forward bar has landed yet: either no bar at all (`insufficient_prices`) or ONLY the arm-day
 *  bar (`awaitingForwardBar`: exit_date === arm_date). The forward / peak / arm_until returns are
 *  all a degenerate 0.0% over a single (or zero) bar — the ledger row shows "—" for exactly this
 *  (EpisodeRow's `awaiting` guard); the scorecard mirrors it (no false-flat move, no peak to judge). */
export function noForwardBar(e: ScoreboardEpisodeOut): boolean {
  return awaitingForwardBar(e) || e.insufficient_prices;
}

/** Lens 1 (The move): the honest one-liner when the prices don't yet support a realized move.
 *  No forward bar (nothing to score) takes priority over truncated (a bar, but short of the
 *  horizon); null when the move stands on its own. */
export function moveNote(e: ScoreboardEpisodeOut): string | null {
  if (noForwardBar(e)) return "awaiting the first forward bar — nothing to score yet";
  if (e.truncated) return "measured to the last bar ≤ as-of — the horizon isn't reached yet";
  return null;
}

/** Lens 2 (Horizon calibration): how the exit-by sat relative to the realized peak. `days` is
 *  `exit_vs_peak_days` (exit_date − peak_date). null when the horizon-vs-peak gap is unknowable.
 *  `truncated` anchors the phrasing honestly: on a still-running/truncated episode the HORIZON
 *  (exit_by) hasn't closed — only the measurement (the last bar ≤ as-of) has, so it reads "last
 *  bar …"; "horizon closed …" is reserved for a matured (non-truncated) episode. Value unchanged. */
export function peakTimingPhrase(
  days: number | null | undefined,
  truncated = false,
): string | null {
  if (days === null || days === undefined) return null;
  if (days < 0) return `horizon closes ${-days}d before the peak`; // defensive: peak_date ≤ last bar
  const anchor = truncated ? "last bar" : "horizon closed";
  if (days > 0) return `${anchor} ${days}d after the peak`;
  return truncated ? "last bar at the peak" : "horizon closed at the peak";
}

/** Lens 2: the giveback from the peak to the exit, in words — only when there IS a visible one
 *  (peak strictly above the exit AND the rounded displays differ, so it never reads "from X to X"). */
export function givebackPhrase(
  peak: number | null | undefined,
  forward: number | null | undefined,
): string | null {
  if (peak === null || peak === undefined || forward === null || forward === undefined) return null;
  if (peak <= forward) return null;
  const p = fmtReturn(peak).text;
  const f = fmtReturn(forward).text;
  if (p === f) return null;
  return `gave back from ${p} to ${f}`;
}

export interface HorizonLens {
  peak: Ret;
  timing: string | null;
  giveback: string | null;
}

/** Lens 2, assembled — null (hide the whole lens) when there's no realized peak to judge against. */
export function horizonLens(e: ScoreboardEpisodeOut): HorizonLens | null {
  if (e.peak_return === null || e.peak_return === undefined) return null;
  if (e.peak_date === null || e.peak_date === undefined) return null;
  return {
    peak: fmtReturn(e.peak_return),
    timing: peakTimingPhrase(e.exit_vs_peak_days, e.truncated),
    giveback: givebackPhrase(e.peak_return, e.forward_return),
  };
}

export type EdgeLens =
  | { kind: "no_warm"; line: string }
  | { kind: "compared"; warm: Ret; forward: Ret; lead: string };

// A move of this size (0.5pp) is the floor for reading warm-vs-arm as a real lead either way; inside
// it, the two are "in step" (an honest neutral, not a manufactured verdict).
const EDGE_EPS = 0.005;

/** Lens 3 (Edge preservation): did we arm in time, or did the move happen during warming?
 *  No warm_date → a single quiet line (armed without a visible warm-up), never an empty stat. */
export function edgeLens(e: ScoreboardEpisodeOut): EdgeLens {
  if (e.warm_date === null || e.warm_date === undefined) {
    return {
      kind: "no_warm",
      line: "armed without a visible warm-up (conviction + confirmation co-fired)",
    };
  }
  const w = e.warm_return;
  const f = e.forward_return;
  let lead: string;
  if (w === null || w === undefined || f === null || f === undefined) {
    lead = "the warm-up leg isn't priced yet";
  } else if (w - f > EDGE_EPS) {
    lead = "much of the move happened during warming — armed late";
  } else if (f - w > EDGE_EPS) {
    lead = "the warming stretch gave nothing up — arming waited out the early chop";
  } else {
    lead = "little separates the warm-up from the arm — armed in step with the move";
  }
  return { kind: "compared", warm: fmtReturn(w), forward: fmtReturn(f), lead };
}

/** Lens 4 (setup): confidence as an integer percent — the product labels this SETUP STRENGTH, an
 *  experimental relative indicator, NOT a probability. null when unset (renders nothing). */
export function setupStrengthPct(e: ScoreboardEpisodeOut): number | null {
  return e.confidence === null || e.confidence === undefined ? null : Math.round(e.confidence * 100);
}

/** Lens 4 (A2): the ONE quiet operator line — null when no decision is logged (nothing renders, not a
 *  "no decision" stub: the capture gap is the episode ROW's story, `operatorLine`'s "none"). Delegates
 *  to `rows.operatorLine` so the row cell and this line can never phrase the same decision two ways:
 *  "operator: took 2026-06-05 @ 12.34 · running +8.0% (close, inferred)" / "operator: passed 2026-06-05". */
export function operatorLensLine(e: ScoreboardEpisodeOut): string | null {
  const l = operatorLine(e);
  if (l.kind === "none") return null;
  const ret = l.ret != null && l.ret.text !== "—" ? ` ${l.ret.text}` : "";
  const inferred = l.inferred ? " (close, inferred)" : "";
  return `operator: ${l.text}${ret}${inferred}`;
}
