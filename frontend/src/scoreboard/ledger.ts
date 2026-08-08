import type { DisplaySignal, MemberDisplaySignalsOut, ScoredMemberOut } from "../api/hooks";
import { archLabel } from "../util/format";
import { formatMarketCap } from "../workbench/format";
import { familyCls, type LifecycleKind, type OverlayEvent, overlayTooltip } from "./overlay";

// Pure formatters for the drawer's per-episode event LEDGER (Slice B) — the tabular companion to the chart.
// The ledger lists the SAME numbered events the chart draws (row #N ↔ chip #N); it shares the ONE `events`
// array built ONCE in EpisodeScorecard, so the numbering is never re-derived here (the reuse invariant).
// Every row traces to a recorded fact (#6 — nothing invented); a missing identity field reads "—" (never a
// guess); only signals that carry a headline surface (honest loudness #7). Canvas/coordinate math lives in
// the chart — these are the jsdom-testable strings the reviewer can trust without an eye.

export interface LedgerRow {
  n: number; // the STABLE chronological id — identical to the chip's number (built in overlay.ts)
  cls: string; // the family hue class, same as the chip: ov-insider / ov-trigger / ov-lifecycle
  date: string; // raw ISO — the component formats via fmtDate
  type: string; // the human family/kind label
  detail: string; // the family specifics (reuses overlayTooltip's per-family lines where they read well)
}

// A lifecycle row names its specific kind in the type cell — the number's tint already carries the family,
// so "armed" reads clearer than "lifecycle · armed".
const LIFECYCLE_TYPE: Record<LifecycleKind, string> = {
  warmed: "warmed",
  armed: "armed",
  dearmed: "de-armed",
  exit_by: "exit-by",
};

/** One overlay event → its ledger row. The `detail` reuses the chart tooltip's per-family lines where they
 *  read as a table cell (insider: who + fill + disclosure lag + market-price context; trigger: label +
 *  source), and stays quiet where the tooltip would only echo another column (a lifecycle row's single
 *  tooltip line IS its date). The insider "transacted <date>" echo is dropped — the date is its own column. */
export function ledgerRow(e: OverlayEvent): LedgerRow {
  const base = { n: e.n, cls: familyCls(e.family), date: e.date };
  if (e.family === "insider") {
    const tip = overlayTooltip(e);
    const lines = tip.lines.filter((l) => !l.startsWith("transacted "));
    return { ...base, type: "insider buy", detail: [tip.title, ...lines].join(" · ") };
  }
  if (e.family === "trigger") {
    const tip = overlayTooltip(e); // title = the trigger label; lines = ["kind · ticker"]
    return { ...base, type: "arm trigger", detail: [tip.title, ...tip.lines].join(" · ") };
  }
  const detail =
    e.kind === "dearmed"
      ? (e.closeReason ?? "—")
      : e.kind === "exit_by"
        ? "signal-validity horizon"
        : "—"; // warmed / armed: the WHY rides the separate numbered trigger rows
  return { ...base, type: LIFECYCLE_TYPE[e.kind], detail };
}

export interface IdentityCell {
  label: string;
  value: string;
}

/** The Cockpit identity line for the drawer — archetype · sector · exchange · origin · market cap, off the
 *  scored member (found by security_id in the parent). A missing field reads "—" (NEVER a guess, #6) —
 *  origin included: it is derived server-side from the SEC's own locators and abstains to null, so the "—"
 *  is the ladder's honest unknown. Deliberately NOT the scoring meters / fit / size-weight — that
 *  duplication is the NamePanel's job, which the operator asked the Scoreboard drawer to stay out of. */
export function identityCells(scored: ScoredMemberOut | null | undefined): IdentityCell[] {
  const cells: IdentityCell[] = [
    { label: "archetype", value: scored?.archetype ? archLabel(scored.archetype) : "—" },
    { label: "sector", value: scored?.sector ?? "—" },
    { label: "exchange", value: scored?.exchange ?? "—" },
    { label: "origin", value: scored?.origin ?? "—" },
    { label: "market cap", value: formatMarketCap(scored?.market_cap?.value) },
  ];
  // The resolved vendor price symbol — an EXCEPTION cell, appended ONLY when the name is priced under a
  // symbol other than its SEC ticker (honest loudness #7: the healthy common case has none, so a "—" here
  // would carry no signal). Off the scored wire (identity_for), never a call input — pure identity.
  if (scored?.price_symbol) cells.push({ label: "priced under", value: scored.price_symbol });
  return cells;
}

// The display-signal registry render order (backend signals/display/__init__.py). The ledger owns the order
// so it is stable regardless of wire order; an unregistered new kind sorts last (renders, zero FE change).
const SIGNAL_ORDER = ["sma_position", "range_52w", "volume_regime", "insider_flow_90d"];

/** The present-only display-signal headlines for the drawer strip, in registry order. Renders ONLY the
 *  signals that actually carry a headline (honest loudness #7 — a headline-less signal is silence, not a
 *  blank row). The component renders each via the shared Cockpit DisplayHeadlineRow. */
export function signalHeadlines(member: MemberDisplaySignalsOut | null | undefined): DisplaySignal[] {
  const withHeadline = (member?.signals ?? []).filter((s) => s.headline);
  const rank = (k: string) => {
    const i = SIGNAL_ORDER.indexOf(k);
    return i === -1 ? SIGNAL_ORDER.length : i;
  };
  return [...withHeadline].sort((a, b) => rank(a.kind) - rank(b.kind));
}
